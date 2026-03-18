"""
macros.py — Système de macros et automatisations
SEMAINE 11 — MERCREDI

Une macro = séquence de commandes nommée, exécutée en ordre.
Exemple : "mode nuit" → volume 0 + verrouille écran + veille

Stockage : data/macros.json (persistant)

Macros prédéfinies incluses :
  "mode travail"  → volume 50% + ouvre Chrome + ouvre VS Code
  "mode nuit"     → volume 0 + verrouille écran
  "mode cinéma"   → volume 80% + plein écran + pas de notifications
  "démarrage"     → infos système + liste apps ouvertes
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
DEFAULT_MACROS = {
    "mode travail": {
        "description": "Prépare le PC pour travailler",
        "commands": [
            "mets le volume à 50%",
            "ouvre Chrome",
        ],
        "delay_between": 1.5,
        "builtin": True,
    },
    "mode nuit": {
        "description": "Prépare le PC pour la nuit",
        "commands": [
            "mets le volume à 0%",
            "verrouille l'écran",
        ],
        "delay_between": 1.0,
        "builtin": True,
    },
    "mode cinéma": {
        "description": "Mode lecture vidéo",
        "commands": [
            "mets le volume à 80%",
        ],
        "delay_between": 0.5,
        "builtin": True,
    },
    "démarrage": {
        "description": "Rapport de démarrage du PC",
        "commands": [
            "montre les infos système",
            "quelles applications sont ouvertes",
            "infos réseau",
        ],
        "delay_between": 0.5,
        "builtin": True,
    },
}


class MacroStep:
    """Résultat d'une étape de macro."""
    def __init__(self, index: int, command: str, result: dict,
                 duration_ms: int):
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
        self.name      = name
        self.steps     = steps
        self.total_ms  = total_ms
        ok_count       = sum(1 for s in steps if s.success)
        self.success   = ok_count == len(steps)
        self.message   = (
            f"Macro '{name}' : {ok_count}/{len(steps)} étapes réussies "
            f"({total_ms}ms)"
        )

    def to_dict(self) -> dict:
        return {
            "success":   self.success,
            "message":   self.message,
            "macro":     self.name,
            "steps":     [s.to_dict() for s in self.steps],
            "total_ms":  self.total_ms,
            "ok_count":  sum(1 for s in self.steps if s.success),
            "total":     len(self.steps),
        }


class MacroManager:
    """
    Gestionnaire de macros persistant.
    Thread-safe.
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

    # ── API publique ─────────────────────────────────────────────────────────

    def run(self, name: str, agent,
            on_step: callable = None) -> dict:
        """
        Exécute une macro.

        Args:
            name    : nom de la macro (insensible à la casse)
            agent   : instance Agent pour exécuter les commandes
            on_step : callback(step_index, command, result) appelé
                      après chaque étape (pour notifications temps réel)

        Returns:
            dict avec success, message, steps[], total_ms
        """
        macro = self._find(name)
        if not macro:
            return {"success": False,
                    "message": f"Macro '{name}' introuvable. "
                               f"Essaie : {self._list_names()}"}

        commands    = macro.get("commands", [])
        delay       = float(macro.get("delay_between", 1.0))
        stop_on_err = macro.get("stop_on_error", False)

        if not commands:
            return {"success": False,
                    "message": f"Macro '{name}' est vide."}

        logger.info(f"Macro '{name}' — {len(commands)} commande(s)")
        steps    = []
        t_start  = time.time()

        for i, cmd in enumerate(commands):
            logger.info(f"  Étape {i+1}/{len(commands)} : '{cmd}'")
            t0     = time.time()
            result = agent.handle_command(cmd)
            ms     = int((time.time() - t0) * 1000)
            step   = MacroStep(i, cmd, result, ms)
            steps.append(step)

            if on_step:
                try:
                    on_step(i, cmd, result)
                except Exception:
                    pass

            icon = "✓" if step.success else "✗"
            logger.info(f"  {icon} [{ms}ms] {step.message[:60]}")

            # Arrêter si l'étape échoue et stop_on_error activé
            if not step.success and stop_on_err:
                logger.warning(f"Macro stoppée à l'étape {i+1} (stop_on_error)")
                break

            # Pause entre les étapes (sauf après la dernière)
            if i < len(commands) - 1 and delay > 0:
                time.sleep(delay)

        total_ms = int((time.time() - t_start) * 1000)
        res      = MacroResult(name, steps, total_ms)
        logger.info(res.message)
        return res.to_dict()

    def save_macro(self, name: str, commands: list,
                   description: str = "",
                   delay_between: float = 1.0,
                   stop_on_error: bool = False) -> dict:
        """
        Crée ou met à jour une macro.

        Args:
            name          : nom de la macro (ex: "mode bureau")
            commands      : liste de commandes en langage naturel
            description   : description courte
            delay_between : secondes entre chaque étape
            stop_on_error : stopper si une étape échoue
        """
        name = name.strip().lower()
        if not name:
            return {"success": False, "message": "Nom de macro vide."}
        if not commands:
            return {"success": False, "message": "Liste de commandes vide."}
        if len(commands) > 20:
            return {"success": False,
                    "message": "Maximum 20 commandes par macro."}

        macro = {
            "description":  description or f"Macro '{name}'",
            "commands":     [c.strip() for c in commands if c.strip()],
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
        logger.info(f"Macro '{name}' {action} ({len(macro['commands'])} étapes)")
        return {
            "success": True,
            "message": f"Macro '{name}' {action} ({len(macro['commands'])} étape(s)).",
            "data":    {"name": name, **macro},
        }

    def delete_macro(self, name: str) -> dict:
        """Supprime une macro (les builtins sont protégées)."""
        name = name.strip().lower()
        with self._lock:
            macro = self._macros.get(name)
            if not macro:
                return {"success": False,
                        "message": f"Macro '{name}' introuvable."}
            if macro.get("builtin"):
                return {"success": False,
                        "message": f"'{name}' est une macro prédéfinie — impossible de la supprimer."}
            del self._macros[name]
            self._save()
        return {"success": True,
                "message": f"Macro '{name}' supprimée."}

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
                "commands":    m.get("commands", []),
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
                return {"success": False,
                        "message": f"Macro '{old}' introuvable."}
            if self._macros[old].get("builtin"):
                return {"success": False,
                        "message": "Impossible de renommer une macro prédéfinie."}
            if new in self._macros:
                return {"success": False,
                        "message": f"Le nom '{new}' est déjà utilisé."}
            self._macros[new] = self._macros.pop(old)
            self._save()
        return {"success": True,
                "message": f"Macro renommée : '{old}' → '{new}'."}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find(self, name: str) -> dict | None:
        """Cherche une macro (insensible à la casse, recherche partielle)."""
        name_lower = name.strip().lower()
        with self._lock:
            # Correspondance exacte d'abord
            if name_lower in self._macros:
                return self._macros[name_lower]
            # Recherche partielle
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
        except Exception as e:
            logger.error(f"Chargement macros : {e}")
            self._macros = {}

    def _save(self):
        try:
            MACROS_FILE.write_text(
                json.dumps(self._macros, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Sauvegarde macros : {e}")