"""
macros.py — Système de macros et automatisations
SEMAINE 11 — MERCREDI

Une macro = séquence de commandes nommée, exécutée en ordre.
Exemple : "mode nuit" → volume 0 + verrouille écran + veille

Stockage : data/macros.json (persistant)

Macros prédéfinies incluses :
  "mode travail"  → volume 50% + ouvre Chrome + joue playlist de travail
  "mode nuit"     → volume 0 + verrouille écran
  "mode cinéma"   → volume 80% + plein écran + pas de notifications
  "démarrage"     → infos système + liste apps ouvertes

CORRECTION [P3] — Bypass Groq pour les étapes de macro :
  Avant : chaque étape appelait agent.handle_command(cmd) → Groq complet
          → 8-10s par étape → macro de 2 étapes = 25s

  Après : les étapes peuvent être stockées en format direct :
          {"intent": "AUDIO_VOLUME_SET", "params": {"level": 50}}
          → executor.execute() direct, 0 appel Groq, ~50ms par étape

  Rétrocompatibilité totale :
  - Étapes texte existantes → agent.handle_command() comme avant
  - Étapes dict avec intent → executor.execute() direct

  Migration automatique :
  - save_macro() accepte maintenant les étapes en dict ou texte
  - _compile_step() convertit les phrases simples connues en dict
    pour accélérer les prochaines exécutions

CORRECTION [P1] — Macro "mode travail" mise à jour :
  Ajout de "joue la playlist de travail" comme 3e étape.
  Utilise la préférence mémorisée si disponible (pref_music_travail).
"""

import json
import threading
import time
from pathlib import Path
from config.logger   import get_logger
from config.settings import BASE_DIR

logger = get_logger(__name__)

MACROS_FILE = BASE_DIR / "data" / "macros.json"

# ── Macros prédéfinies ────────────────────────────────────────────────────────
# Format hybride : les étapes peuvent être des strings OU des dicts {intent, params}
# Les dicts sont exécutés directement via executor (bypass Groq = ultra rapide)
DEFAULT_MACROS = {
    "mode travail": {
        "description": "Prépare le PC pour travailler",
        "commands": [
            {"intent": "AUDIO_VOLUME_SET", "params": {"level": 50},
             "label": "mets le volume à 50%"},
            {"intent": "APP_OPEN", "params": {"app_name": "chrome", "args": []},
             "label": "ouvre Chrome"},
            {"intent": "MUSIC_PLAYLIST_PLAY", "params": {"name": "ma playlist"},
             "label": "joue ma playlist de travail",
             "use_pref": "pref_music_travail"},   # ← utilise la préférence si mémorisée
        ],
        "delay_between": 0.5,
        "builtin": True,
    },
    "mode nuit": {
        "description": "Prépare le PC pour la nuit",
        "commands": [
            {"intent": "AUDIO_VOLUME_SET", "params": {"level": 0},
             "label": "mets le volume à 0%"},
            {"intent": "SYSTEM_LOCK", "params": {},
             "label": "verrouille l'écran"},
        ],
        "delay_between": 1.0,
        "builtin": True,
    },
    "mode cinéma": {
        "description": "Mode lecture vidéo",
        "commands": [
            {"intent": "AUDIO_VOLUME_SET", "params": {"level": 80},
             "label": "mets le volume à 80%"},
        ],
        "delay_between": 0.5,
        "builtin": True,
    },
    "mode codage": {
        "description": "Prépare le PC pour coder",
        "commands": [
            {"intent": "AUDIO_VOLUME_SET", "params": {"level": 50},
             "label": "mets le volume à 50%"},
            {"intent": "APP_OPEN", "params": {"app_name": "vscode", "args": []},
             "label": "ouvre VS Code"},
            {"intent": "MUSIC_PLAYLIST_PLAY", "params": {"name": "ma playlist"},
             "label": "joue ma playlist de codage",
             "use_pref": "pref_music_codage"},
        ],
        "delay_between": 0.5,
        "builtin": True,
    },
    "démarrage": {
        "description": "Rapport de démarrage du PC",
        "commands": [
            {"intent": "SYSTEM_INFO", "params": {},
             "label": "montre les infos système"},
            {"intent": "APP_LIST_RUNNING", "params": {},
             "label": "quelles applications sont ouvertes"},
            {"intent": "NETWORK_INFO", "params": {},
             "label": "infos réseau"},
        ],
        "delay_between": 0.5,
        "builtin": True,
    },
}

# ── Compilation de phrases simples → intents directs ─────────────────────────
# Permet de convertir les macros texte existantes en format rapide
_PHRASE_TO_INTENT = {
    "mets le volume à 50%":   {"intent": "AUDIO_VOLUME_SET", "params": {"level": 50}},
    "mets le volume à 0%":    {"intent": "AUDIO_VOLUME_SET", "params": {"level": 0}},
    "mets le volume à 80%":   {"intent": "AUDIO_VOLUME_SET", "params": {"level": 80}},
    "mets le volume à 70%":   {"intent": "AUDIO_VOLUME_SET", "params": {"level": 70}},
    "mets le volume à 100%":  {"intent": "AUDIO_VOLUME_SET", "params": {"level": 100}},
    "coupe le son":            {"intent": "AUDIO_MUTE", "params": {}},
    "ouvre chrome":            {"intent": "APP_OPEN", "params": {"app_name": "chrome", "args": []}},
    "ouvre firefox":           {"intent": "APP_OPEN", "params": {"app_name": "firefox", "args": []}},
    "ouvre vscode":            {"intent": "APP_OPEN", "params": {"app_name": "vscode", "args": []}},
    "ouvre vs code":           {"intent": "APP_OPEN", "params": {"app_name": "vscode", "args": []}},
    "verrouille l'écran":     {"intent": "SYSTEM_LOCK", "params": {}},
    "verrouille ecran":        {"intent": "SYSTEM_LOCK", "params": {}},
    "joue ma playlist":        {"intent": "MUSIC_PLAYLIST_PLAY", "params": {"name": "ma playlist"}},
    "infos réseau":            {"intent": "NETWORK_INFO", "params": {}},
    "infos réseau":            {"intent": "NETWORK_INFO", "params": {}},
    "montre les infos système":{"intent": "SYSTEM_INFO", "params": {}},
    "quelles applications sont ouvertes": {"intent": "APP_LIST_RUNNING", "params": {}},
}


class MacroStep:
    """Résultat d'une étape de macro."""
    def __init__(self, index: int, command: str, result: dict, duration_ms: int):
        self.index       = index
        self.command     = command
        self.success     = result.get("success", False)
        self.message     = result.get("message", "")[:150]
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        return {
            "step":        self.index + 1,
            "command":     self.command,
            "success":     self.success,
            "message":     self.message,
            "duration_ms": self.duration_ms,
        }


class MacroResult:
    """Résultat d'exécution d'une macro complète."""
    def __init__(self, name: str, steps: list, total_ms: int):
        self.name     = name
        self.steps    = steps
        self.total_ms = total_ms
        ok_count      = sum(1 for s in steps if s.success)
        self.success  = ok_count == len(steps)
        self.message  = (
            f"Macro '{name}' : {ok_count}/{len(steps)} étapes réussies "
            f"({total_ms}ms)"
        )

    def to_dict(self) -> dict:
        return {
            "success":  self.success,
            "message":  self.message,
            "macro":    self.name,
            "steps":    [s.to_dict() for s in self.steps],
            "total_ms": self.total_ms,
            "ok_count": sum(1 for s in self.steps if s.success),
            "total":    len(self.steps),
        }


class MacroManager:
    """
    Gestionnaire de macros persistant.
    Thread-safe. Supporte les étapes en texte ET en format direct {intent, params}.
    """

    def __init__(self):
        self._lock   = threading.Lock()
        self._macros = {}
        MACROS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        # Injecter les macros prédéfinies si absentes
        changed = False
        for name, macro in DEFAULT_MACROS.items():
            if name not in self._macros:
                self._macros[name] = dict(macro)
                changed = True
        if changed:
            self._save()
        logger.info(f"MacroManager — {len(self._macros)} macro(s) chargée(s)")

    # ── API publique ──────────────────────────────────────────────────────────

    def run(self, name: str, agent, on_step: callable = None) -> dict:
        """
        Exécute une macro.

        [P3] Chaque étape est exécutée en mode direct (bypass Groq) si possible :
          - Étape dict {intent, params} → executor.execute() direct (~50ms)
          - Étape dict avec use_pref   → résoudre la préférence en mémoire d'abord
          - Étape texte               → compilée en dict si phrase connue, sinon Groq

        Args:
            name    : nom de la macro
            agent   : instance Agent
            on_step : callback(step_index, command, result) optionnel

        Returns:
            dict avec success, message, steps[], total_ms
        """
        macro = self._find(name)
        if not macro:
            return {
                "success": False,
                "message": f"Macro '{name}' introuvable. Essaie : {self._list_names()}",
            }

        commands    = macro.get("commands", [])
        delay       = float(macro.get("delay_between", 1.0))
        stop_on_err = macro.get("stop_on_error", False)

        if not commands:
            return {"success": False, "message": f"Macro '{name}' est vide."}

        logger.info(f"Macro '{name}' — {len(commands)} commande(s)")
        steps   = []
        t_start = time.time()

        for i, cmd in enumerate(commands):
            label = self._step_label(cmd)
            logger.info(f"  Étape {i+1}/{len(commands)} : '{label}'")

            t0     = time.time()
            result = self._execute_step(cmd, agent)
            ms     = int((time.time() - t0) * 1000)
            step   = MacroStep(i, label, result, ms)
            steps.append(step)

            if on_step:
                try:
                    on_step(i, label, result)
                except Exception:
                    pass

            icon = "✓" if step.success else "✗"
            logger.info(f"  {icon} [{ms}ms] {step.message[:60]}")

            if not step.success and stop_on_err:
                logger.warning(f"Macro stoppée à l'étape {i+1} (stop_on_error)")
                break

            if i < len(commands) - 1 and delay > 0:
                time.sleep(delay)

        total_ms = int((time.time() - t_start) * 1000)
        res      = MacroResult(name, steps, total_ms)
        logger.info(res.message)
        return res.to_dict()

    def _execute_step(self, cmd, agent) -> dict:
        """
        [P3] Exécute une étape de macro en mode direct si possible.

        Priorité :
          1. dict avec use_pref → résoudre préférence + execute direct
          2. dict avec intent   → execute direct (bypass Groq)
          3. str connue         → compile en dict + execute direct
          4. str inconnue       → agent.handle_command() (Groq)
        """
        # ── Cas 1 & 2 : étape déjà en format dict ───────────────────────────
        if isinstance(cmd, dict):
            intent = cmd.get("intent", "")
            params = dict(cmd.get("params", {}) or {})

            # Résoudre la préférence si indiquée
            pref_key = cmd.get("use_pref", "")
            if pref_key and hasattr(agent, "_memory"):
                try:
                    pref_value = agent._memory.recall_fact(pref_key)
                    if pref_value and intent == "MUSIC_PLAYLIST_PLAY":
                        params["name"] = pref_value
                        logger.info(f"  → Préférence '{pref_key}' résolue : '{pref_value}'")
                except Exception:
                    pass  # Fallback sur params par défaut

            if intent:
                try:
                    executor = agent._executor
                    result   = executor.execute(intent, params, agent=agent)
                    return result if isinstance(result, dict) else {
                        "success": bool(result), "message": str(result), "data": {}
                    }
                except Exception as e:
                    logger.warning(f"  Execute direct échoué : {e} → fallback Groq")

        # ── Cas 3 : étape texte → essayer de compiler en dict ───────────────
        if isinstance(cmd, str):
            compiled = _PHRASE_TO_INTENT.get(cmd.strip().lower())
            if compiled:
                try:
                    executor = agent._executor
                    result   = executor.execute(
                        compiled["intent"], dict(compiled["params"]), agent=agent
                    )
                    return result if isinstance(result, dict) else {
                        "success": bool(result), "message": str(result), "data": {}
                    }
                except Exception as e:
                    logger.warning(f"  Compile+execute échoué : {e} → fallback Groq")

        # ── Cas 4 : fallback Groq (texte non compilable) ─────────────────────
        cmd_text = cmd if isinstance(cmd, str) else cmd.get("label", str(cmd))
        return agent.handle_command(cmd_text)

    def save_macro(self, name: str, commands: list,
                   description: str = "",
                   delay_between: float = 1.0,
                   stop_on_error: bool = False) -> dict:
        """
        Crée ou met à jour une macro.
        Accepte les commandes en texte OU en dict {intent, params, label}.
        Les phrases texte simples sont auto-compilées en dict si possible.
        """
        name = name.strip().lower()
        if not name:
            return {"success": False, "message": "Nom de macro vide."}
        if not commands:
            return {"success": False, "message": "Liste de commandes vide."}
        if len(commands) > 20:
            return {"success": False, "message": "Maximum 20 commandes par macro."}

        # Auto-compiler les étapes texte connues en format direct
        compiled_commands = []
        for cmd in commands:
            if isinstance(cmd, str):
                compiled = _PHRASE_TO_INTENT.get(cmd.strip().lower())
                if compiled:
                    compiled_commands.append({
                        **compiled,
                        "label": cmd.strip(),
                    })
                else:
                    compiled_commands.append(cmd.strip())
            else:
                compiled_commands.append(cmd)

        macro = {
            "description":   description or f"Macro '{name}'",
            "commands":      [c for c in compiled_commands if c],
            "delay_between": max(0.0, min(10.0, delay_between)),
            "stop_on_error": stop_on_error,
            "builtin":       False,
            "created_at":    int(time.time()),
        }
        with self._lock:
            existed = name in self._macros
            self._macros[name] = macro
            self._save()

        action = "mise à jour" if existed else "créée"
        n_steps = len(macro["commands"])
        logger.info(f"Macro '{name}' {action} ({n_steps} étapes)")
        return {
            "success": True,
            "message": f"Macro '{name}' {action} ({n_steps} étape(s)).",
            "data":    {"name": name, **macro},
        }

    def delete_macro(self, name: str) -> dict:
        """Supprime une macro (les builtins sont protégées)."""
        name = name.strip().lower()
        with self._lock:
            macro = self._macros.get(name)
            if not macro:
                return {"success": False, "message": f"Macro '{name}' introuvable."}
            if macro.get("builtin"):
                return {"success": False,
                        "message": f"'{name}' est une macro prédéfinie — impossible de la supprimer."}
            del self._macros[name]
            self._save()
        return {"success": True, "message": f"Macro '{name}' supprimée."}

    def list_macros(self) -> dict:
        """Liste toutes les macros disponibles."""
        with self._lock:
            macros = dict(self._macros)
        result = []
        for name, m in macros.items():
            result.append({
                "name":        name,
                "description": m.get("description", ""),
                "steps":       len(m.get("commands", [])),
                "commands":    [self._step_label(c) for c in m.get("commands", [])],
                "builtin":     m.get("builtin", False),
            })
        result.sort(key=lambda x: (not x["builtin"], x["name"]))
        msg = (f"{len(result)} macro(s) : "
               + ", ".join(f"'{m['name']}'" for m in result[:5]))
        if len(result) > 5:
            msg += f" et {len(result)-5} autre(s)"
        return {
            "success": True,
            "message": msg,
            "data":    {"macros": result, "count": len(result)},
        }

    def get_macro(self, name: str) -> dict | None:
        """Retourne une macro par nom (insensible à la casse)."""
        return self._find(name)

    def rename_macro(self, old_name: str, new_name: str) -> dict:
        """Renomme une macro."""
        old = old_name.strip().lower()
        new = new_name.strip().lower()
        with self._lock:
            if old not in self._macros:
                return {"success": False, "message": f"Macro '{old}' introuvable."}
            if self._macros[old].get("builtin"):
                return {"success": False,
                        "message": "Impossible de renommer une macro prédéfinie."}
            if new in self._macros:
                return {"success": False, "message": f"Le nom '{new}' est déjà utilisé."}
            self._macros[new] = self._macros.pop(old)
            self._save()
        return {"success": True, "message": f"Macro renommée : '{old}' → '{new}'."}

    def replay_last(self, agent) -> dict:
        """Rejoue la dernière macro exécutée (utilisé par REPEAT_LAST)."""
        # Chercher dans l'historique de l'agent
        try:
            last = agent._history.get_last_by_intent("MACRO_RUN")
            if last:
                name = (last.get("params") or {}).get("name", "")
                if name:
                    return self.run(name, agent)
        except Exception:
            pass
        return {"success": False, "message": "Aucune macro précédente à rejouer."}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _step_label(cmd) -> str:
        """Retourne le label lisible d'une étape (texte ou dict)."""
        if isinstance(cmd, dict):
            return cmd.get("label") or f"{cmd.get('intent', '?')}({cmd.get('params', {})})"
        return str(cmd)

    def _find(self, name: str) -> dict | None:
        """Cherche une macro (insensible à la casse, recherche partielle)."""
        name_lower = name.strip().lower()
        with self._lock:
            if name_lower in self._macros:
                return self._macros[name_lower]
            for key, macro in self._macros.items():
                if name_lower in key or key in name_lower:
                    return macro
        return None

    def _list_names(self) -> str:
        with self._lock:
            names = list(self._macros.keys())
        return ", ".join(f"'{n}'" for n in names[:6])

    def _load(self):
        try:
            if MACROS_FILE.exists():
                self._macros = json.loads(
                    MACROS_FILE.read_text(encoding="utf-8")
                )
                # Migration : convertir les anciennes macros texte en format dict
                self._migrate_legacy_macros()
        except Exception as e:
            logger.error(f"Chargement macros : {e}")
            self._macros = {}

    def _migrate_legacy_macros(self):
        """
        Migration silencieuse : convertit les étapes texte connues
        en format dict pour accélérer les prochaines exécutions.
        """
        changed = False
        for name, macro in self._macros.items():
            new_cmds = []
            for cmd in macro.get("commands", []):
                if isinstance(cmd, str):
                    compiled = _PHRASE_TO_INTENT.get(cmd.strip().lower())
                    if compiled:
                        new_cmds.append({**compiled, "label": cmd.strip()})
                        changed = True
                    else:
                        new_cmds.append(cmd)
                else:
                    new_cmds.append(cmd)
            if changed:
                macro["commands"] = new_cmds
        if changed:
            self._save()
            logger.info("Macros migrées vers format direct (bypass Groq)")

    def _save(self):
        try:
            MACROS_FILE.write_text(
                json.dumps(self._macros, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Sauvegarde macros : {e}")