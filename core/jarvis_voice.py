"""
core/jarvis_voice.py — Moteur de réponse naturelle de Jarvis
=============================================================

C'est ici que Jarvis cesse d'être un robot et devient un assistant humain.

CORRECTIONS :
  [Bug1+7] _build_messages() : injection des données CONCRÈTES dans le
  contexte Groq au lieu de juste lister les noms de clés.

  Avant :
      data_hint = "Données disponibles : tabs, count, display"
      → Groq n'a aucune idée des vrais noms → hallucine "Telegram", "Spotify"

  Après :
      _extract_concrete_data_hint() injecte les vraies valeurs par intent :
      - APP_OPEN       → "Application lancée : bloc-notes (PID 15648)"
      - APP_LIST_RUNNING → "Applications ouvertes (5) : Chrome, VSCode, ..."
      - WINDOW_CLOSE   → "Fenêtre fermée : Gestionnaire des tâches"
      - FILE_SEARCH    → "Fichiers trouvés (3) : rapport.pdf, ..."
      - BROWSER_LIST_TABS → "Onglets ouverts (2) : Python docs, GitHub"
      - WIFI_LIST      → "Réseaux (3) : MonWifi, Voisin5G, ... (connecté : MonWifi)"
"""

from __future__ import annotations

import random
import re
import time
from typing import Optional

from config.logger import get_logger
from config.settings import GROQ_API_KEY, GROQ_MODEL_NAME

logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
_TIMEOUT_S          = 8
_MAX_RESULT_CHARS   = 600
_GROQ_RESPONSE_TOKENS = 120
_FACT_SENSITIVE_INTENTS = {
    "SYSTEM_DISK",
    "SYSTEM_INFO",
    "SYSTEM_NETWORK",
    "NETWORK_INFO",
    "POWER_STATE",
    "SYSTEM_PROCESSES",
}

# ── Fallbacks par intent ──────────────────────────────────────────────────────
_FALLBACKS: dict[str, list[str]] = {
    "APP_OPEN":         ["C'est lancé.", "Voilà, c'est ouvert.", "C'est parti."],
    "APP_CLOSE":        ["Fermé.", "C'est fermé.", "J'ai fermé ça."],
    "SYSTEM_INFO":      ["Voici les infos système.", "J'ai récupéré l'état du système."],
    "SYSTEM_SHUTDOWN":  ["Extinction programmée.", "Le PC va s'éteindre dans quelques instants."],
    "SYSTEM_RESTART":   ["Redémarrage en cours.", "Je redémarre la machine."],
    "SYSTEM_LOCK":      ["Écran verrouillé.", "J'ai verrouillé l'écran."],

    "AUDIO_VOLUME_SET": ["Volume ajusté.", "C'est réglé.", "Volume modifié."],
    "AUDIO_VOLUME_UP":  ["Volume monté.", "J'ai augmenté le son."],
    "AUDIO_VOLUME_DOWN":["Volume baissé.", "J'ai réduit le son."],
    "AUDIO_MUTE":       ["Son coupé.", "Muet activé."],
    "BROWSER_SEARCH":   ["Je cherche ça.", "Recherche lancée.", "C'est parti sur le web."],
    "BROWSER_URL":      ["Page ouverte.", "Voilà, la page est chargée."],
    "FILE_SEARCH":      ["Je cherche ce fichier.", "Recherche en cours."],
    "FILE_OPEN":        ["Fichier ouvert.", "C'est ouvert."],
    "SCREEN_CAPTURE":   ["Capture effectuée.", "Screenshot pris."],
    "VISION_READ_SCREEN": ["Je lis ce qu'il y a à l'écran.", "Voilà ce que je vois."],
    "VISION_CLICK_TEXT": ["Je clique dessus.", "C'est fait."],
    "VISION_SUMMARIZE":  ["Voilà ce qui s'affiche à l'écran.", "Résumé de la page."],
    "VISION_FIND_BUTTON": ["Bouton trouvé.", "Le voici."],
    "VISION_EXTRACT_LINKS": ["Liens extraits.", "J'ai trouvé ces liens."],
    "MACRO_RUN":        ["Macro lancée.", "Séquence exécutée."],
    "REPEAT_LAST":      ["Je répète la dernière commande.", "C'est reparti."],
    "HISTORY_SHOW":     ["Voici l'historique.", "J'ai l'historique."],
    "HELP": [
        "Je suis JARVIS. Voici tout ce que je peux faire pour toi.",
        "Bien sûr, laisse-moi te présenter mes capacités.",
    ],
    "GREETING": [
        "Bonjour ! Qu'est-ce que je peux faire pour toi ?",
        "Salut ! JARVIS à ton service.",
        "Hello ! Dis-moi ce que tu veux.",
    ],
    "INCOMPLETE": [
        "Il me manque une info pour faire ça — tu peux préciser ?",
        "Presque — dis-moi juste ce que tu veux exactement.",
    ],
    "UNKNOWN": [
        "Je n'ai pas bien saisi. Tu peux reformuler ?",
        "Hmm, je n'ai pas compris. Dis-moi autrement ?",
    ],
    "_success": ["C'est fait.", "Voilà, c'est réglé.", "Ça marche.", "Effectué."],
    "_error":   ["Quelque chose n'a pas fonctionné.", "Je n'ai pas pu faire ça."],
}

# ── Prompt système ────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """Tu es JARVIS, l'assistant IA personnel de ton utilisateur.
Tu viens d'exécuter une action sur son PC. Ta mission : lui répondre comme
le JARVIS de Tony Stark — intelligent, naturel, avec du caractère.

PERSONNALITÉ :
- Humour discret et élégant — une remarque légère parfois, jamais lourd
- Loyal mais pas servile — tu signales si quelque chose mérite attention
- Proactif — tu proposes la prochaine étape utile sans qu'on te demande
- Décontracté pour les tâches simples, précis pour les tâches importantes
- Tu varies tes formulations — jamais deux fois la même phrase

RÈGLES ABSOLUES :
1. 1 à 2 phrases maximum — concis, efficace, humain
2. Ne confirme pas bêtement — ajoute toujours de la valeur
3. Si la même action se répète → remarque légère
4. Après une recherche réussie → propose l'étape suivante
5. Après une erreur → explique et propose une alternative
6. Pour une salutation → réponse courte et vivante
7. Pour une question simple → réponse directe uniquement
8. Ne commence JAMAIS par : "Bien sûr", "D'accord", "Absolument",
   "Certainement", "Je vais", "Voici"
9. Ne dis JAMAIS "Commande exécutée" ou "Action effectuée"
10. Tu t'appelles JARVIS — assume cette identité naturellement
11. Si des données sont affichées dans la bulle → dis juste
    "C'est affiché." ou "Voilà." — ne répète pas le contenu
12. CRITIQUE : utilise UNIQUEMENT les noms d'apps/fenêtres fournis dans

    le contexte. N'invente JAMAIS de noms (Telegram, Spotify, etc.)
    si ce n'est pas explicitement mentionné.
13. MUSIQUE : Jarvis utilise VLC comme lecteur local. JAMAIS Spotify,
    Apple Music, ou un service streaming. Ne mentionne jamais "Spotify"
    sauf si l'utilisateur le demande explicitement.

EXEMPLES PARFAITS :
- Ouvre Chrome → "Chrome est lancé — un site à ouvrir ?"
- Volume 70% → "À 70%. Je retiens ça."
- Fichier trouvé → "Trouvé dans E:/Médias — je l'ouvre ?"
- Bonjour → "Jarvis à l'écoute. Qu'est-ce qu'on fait ?"
- Éteins dans 10min → "Extinction dans 10 minutes. Je note.


Réponds UNIQUEMENT avec la phrase de Jarvis. Rien d'autre."""


class JarvisVoice:
    """Génère des réponses vocales naturelles via Groq."""

    def __init__(self):
        self._client       = None
        self._available    = False
        self._cooldown_until = 0.0
        self._init_groq()

    def _init_groq(self):
        if not GROQ_API_KEY:
            logger.warning("JarvisVoice: GROQ_API_KEY manquante — mode fallback activé.")
            return
        try:
            from groq import Groq
            self._client    = Groq(api_key=GROQ_API_KEY)
            self._available = True
            logger.info("JarvisVoice: Groq initialisé.")
        except ImportError:
            logger.warning("JarvisVoice: package 'groq' non installé — mode fallback.")
        except Exception as e:
            logger.warning(f"JarvisVoice: init Groq échouée ({e}) — mode fallback.")

    # ── API publique ──────────────────────────────────────────────────────────

    def generate(
        self,
        user_command: str,
        intent: str,
        params: dict,
        exec_result: dict,
        conversation_history: Optional[list] = None,
        sensory_context: dict = None,
    ) -> str:
        """
        Génère une réponse naturelle.
        
        [TONY STARK V2] Accepte sensory_context pour adapter la réponse
        au contexte actuel du PC (fenêtre active, CPU, etc.)
        """
        if not self._available:
            return self._fallback(intent, exec_result)
        if time.time() < self._cooldown_until:
            return self._fallback(intent, exec_result)

        try:
            messages = self._build_messages(
                user_command=user_command,
                intent=intent,
                params=params,
                exec_result=exec_result,
                history=conversation_history or [],
                sensory_context=sensory_context,  # TONY STARK V2
            )
            response = self._call_groq(messages)
            if response:
                cleaned = self._clean(response)
                if self._is_fact_sensitive_intent(intent):
                    if self._response_is_fact_consistent(intent, cleaned, exec_result):
                        return cleaned
                    logger.warning(f"JarvisVoice: réponse {intent} incohérente, fallback sécurisé.")
                    grounded = self._grounded_fallback(intent, exec_result)
                    if grounded:
                        return grounded
                return cleaned
        except Exception as e:
            logger.error(f"JarvisVoice.generate erreur: {e}")

        return self._fallback(intent, exec_result)

    def _deterministic_message(self, intent: str, exec_result: dict) -> str | None:
        if intent != "SYSTEM_DISK":
            return None
        data       = (exec_result or {}).get("data") or {}
        partitions = data.get("partitions") if isinstance(data, dict) else None
        if not isinstance(partitions, list) or not partitions:
            return "C'est affiché."
        accessible = [p for p in partitions if isinstance(p, dict) and isinstance(p.get("free_gb"), (int, float))]
        if not accessible:
            return "C'est affiché."
        total_free = round(sum(float(p.get("free_gb", 0.0)) for p in accessible), 1)

        def _drive_key(part: dict) -> str:
            dev = str(part.get("device", "")).upper()
            return dev[:2] if len(dev) >= 2 and dev[1] == ":" else dev

        detail_parts = [f"{_drive_key(p)} {float(p.get('free_gb', 0.0)):.1f} Go libres"
                        for p in sorted(accessible, key=_drive_key)]
        detail = " | ".join(detail_parts)
        return f"Espace libre total detecte: {total_free:.1f} Go ({detail})." if detail else f"Espace libre total detecte: {total_free:.1f} Go."

    def generate_proactive(self, situation: str, context: dict | None = None) -> str:
        if not self._available:
            return f"Attention : {situation}"
        prompt = (
            f"Tu es JARVIS. Envoie une alerte brève et naturelle à l'utilisateur "
            f"pour la situation suivante : {situation}. "
            f"1 phrase maximum. Commence directement par l'alerte, sans formule d'intro."
        )
        try:
            messages  = [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
            response  = self._call_groq(messages)
            if response:
                return self._clean(response)
        except Exception as e:
            logger.error(f"JarvisVoice.generate_proactive erreur: {e}")
        return f"Attention : {situation}"

    # ── Construction des messages Groq ────────────────────────────────────────

    def _build_messages(
        self,
        user_command: str,
        intent: str,
        params: dict,
        exec_result: dict,
        history: list,
        sensory_context: dict = None,
    ) -> list:
        """
        Construit la liste de messages pour Groq.

        [Bug1+7] CORRECTION : injection des données CONCRÈTES (noms d'apps,
        titres de fenêtres, listes réelles) au lieu de juste les clés du dict.
        Groq avait tendance à halluciner des noms (Telegram, Spotify...) car
        il ne recevait que "Données disponibles : tabs, count, display".
        """
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

        for msg in history[-8:]:
            role    = msg.get("role", "user")
            content = str(msg.get("content", "")).strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        success = bool((exec_result or {}).get("success", False))
        raw_msg = str((exec_result or {}).get("message", "")).strip()
        data    = (exec_result or {}).get("data")

        # [Bug1+7] Extraire les données CONCRÈTES selon l'intent

        data_hint = self._extract_concrete_data_hint(intent, params, data)

        # [TONY STARK V2] Ajouter le contexte sensoriel si disponible
        sensory_hint = ""
        if sensory_context and isinstance(sensory_context, dict):
            window = sensory_context.get("window", {})
            system = sensory_context.get("system", {})
            if window or system:
                sensory_hint = "\nCONTEXTE PC ACTUEL :\n"
                if window:
                    sensory_hint += f"- Fenêtre active : {window.get('title', 'inconnue')}\n"
                if system:
                    cpu = system.get("cpu_percent", "?")
                    ram = system.get("ram_percent", "?")
                    sensory_hint += f"- Charge système : CPU {cpu}%, RAM {ram}%"

                # Enrichir avec les apps importantes via format_for_groq
        if sensory_context and sensory_hint:
            try:
                from core.sensory import SensoryCapteur
                full_fmt = SensoryCapteur.format_for_groq(sensory_context)
                if full_fmt and len(full_fmt) > len(sensory_hint):
                    sensory_hint = "\n" + full_fmt
            except Exception:
                pass  # Garder sensory_hint construit manuellement

        context_msg = (
            f"Commande utilisateur : \"{user_command}\"\n"
            f"Intent reconnu : {intent}\n"
            f"Paramètres : {self._safe_params(params)}\n"
            f"Résultat : {'SUCCÈS' if success else 'ÉCHEC'}\n"
            f"Message technique : {raw_msg[:_MAX_RESULT_CHARS]}"
            f"{data_hint}"
            f"{sensory_hint}\n\n"
            f"Génère la réponse naturelle de Jarvis."
        )

        if self._is_fact_sensitive_intent(intent):
            truth = self._grounding_hint(intent, exec_result)
            if truth:
                context_msg += (
                    "\n\n---\n"
                    "DONNÉES RÉELLES (strictement obligatoire):\n"
                    f"{truth}\n"
                    "---\n"
                    "RÈGLES: N'invente AUCUN nombre, disque, ou valeur.\n"
                    "Cite UNIQUEMENT les données ci-dessus. Zéro exception."
                )

        messages.append({"role": "user", "content": context_msg})
        return messages

    def _extract_concrete_data_hint(self, intent: str, params: dict, data) -> str:
        """
        [Bug1+7] Extrait les données concrètes à injecter dans le prompt Groq.

        Pour les intents sensibles (apps, fenêtres, listes), on injecte les
        VRAIS noms et valeurs — pas juste les noms de clés du dict résultat.
        Cela empêche Groq d'inventer des noms (Telegram, Spotify, etc.).
        """
        if not data or not isinstance(data, dict):
            return ""

        lines = []

        # ── APP_OPEN : nom de l'app réellement lancée ──────────────────────
        if intent == "APP_OPEN":
            app = str(params.get("app_name") or params.get("name") or "").strip()
            pid = data.get("pid")
            if app:
                lines.append(f"Application lancée : {app}" + (f" (PID {pid})" if pid else ""))

        # ── APP_LIST_RUNNING : liste RÉELLE des apps ouvertes ─────────────
        elif intent == "APP_LIST_RUNNING":
            apps = data.get("apps") or data.get("running") or []
            if isinstance(apps, list) and apps:
                names = [str(a.get("name") or a) for a in apps[:8]]
                lines.append(f"Applications ouvertes ({len(apps)}) : {', '.join(names)}")
            elif isinstance(data.get("display"), str) and data["display"].strip():
                # display contient déjà la liste formatée — l'injecter directement
                first_lines = data["display"].strip().splitlines()[:6]
                lines.append("Applications ouvertes (extrait) :\n" + "\n".join(first_lines))
            # IMPORTANT : si aucune donnée concrète → dire explicitement à Groq
            # de ne pas inventer de noms
            if not lines:
                lines.append("ATTENTION : ne pas inventer de noms d'applications. "
                             "Dire juste le nombre ou 'les applications en cours'.")

        # ── APP_CLOSE / WINDOW_CLOSE : nom EXACT de la fenêtre fermée ─────
        elif intent in {"APP_CLOSE", "WINDOW_CLOSE"}:
            closed = (
                data.get("closed_title") or
                data.get("title") or
                params.get("app_name") or
                params.get("query") or
                params.get("name") or ""
            )
            if closed:
                lines.append(f"Fenêtre fermée : {closed}")
            else:
                lines.append("ATTENTION : ne pas inventer le nom de la fenêtre fermée.")

        # ── FILE_SEARCH : noms réels des fichiers trouvés ──────────────────
        elif intent == "FILE_SEARCH":
            results = data.get("results") or []
            if results:
                names = [str(r.get("name") or r) for r in results[:5]]
                lines.append(f"Fichiers trouvés ({data.get('count', len(results))}) : {', '.join(names)}")

        # ── BROWSER_LIST_TABS : titres réels des onglets ───────────────────
        elif intent == "BROWSER_LIST_TABS":
            tabs = data.get("tabs") or []
            if isinstance(tabs, list) and tabs:
                titles = [str(t.get("title") or t.get("url") or "?") for t in tabs[:5]]
                lines.append(f"Onglets ouverts ({len(tabs)}) : {', '.join(titles)}")

        # ── WIFI_LIST : noms réels des réseaux ────────────────────────────
        elif intent == "WIFI_LIST":
            networks = data.get("networks") or []
            if networks:
                ssids     = [str(n.get("ssid") or n) for n in networks[:5]]
                connected = data.get("connected_ssid", "")
                suffix    = f" (connecté : {connected})" if connected else ""
                lines.append(f"Réseaux Wi-Fi ({len(networks)}) : {', '.join(ssids)}{suffix}")

        # ── MUSIC : tout ce qui est musique → VLC, jamais Spotify ─────────
        elif intent.startswith("MUSIC_"):
            # Toujours préciser VLC comme lecteur — jamais Spotify/autre
            lines.append("Lecteur : VLC Media Player (local)")
            if intent == "MUSIC_PLAYLIST_CREATE":
                pl_name = data.get("name") or params.get("name") or ""
                added   = data.get("added", 0)
                if pl_name:
                    msg = f"Playlist créée : '{pl_name}'"
                    if added:
                        msg += f" ({added} chanson(s) ajoutée(s))"
                    lines.append(msg)
            elif intent == "MUSIC_PLAYLIST_ADD_FOLDER":
                added  = data.get("added", 0)
                folder = data.get("folder", "")
                pl     = data.get("playlist") or params.get("name") or ""
                lines.append(f"{added} chanson(s) ajoutée(s) à '{pl}' depuis '{folder}'")
            elif intent == "MUSIC_PLAYLIST_PLAY":
                pl    = data.get("playlist") or params.get("name") or ""
                count = data.get("count", 0)
                first = data.get("first", "")
                if pl:
                    lines.append(f"Lecture playlist '{pl}' — {count} morceau(x) via VLC")
                    if first:
                        lines.append(f"Premier morceau : {first}")
            elif intent in {"MUSIC_PLAY", "MUSIC_CURRENT"}:
                current = data.get("name") or data.get("current") or ""
                if current:
                    lines.append(f"Morceau en cours : {current}")
            elif intent == "MUSIC_LIBRARY_SCAN":
                total = data.get("total", 0)
                new_c = data.get("new", 0)
                lines.append(f"Bibliothèque : {total} chanson(s) indexée(s)"
                             + (f", {new_c} nouvelle(s)" if new_c else ""))

        # ── Fallback générique : valeurs simples + avertissement ───────────
        else:
            simple = {k: v for k, v in data.items()
                      if not k.startswith("_") and isinstance(v, (str, int, float, bool))
                      and k not in ("display",)}
            if simple:
                lines.append(f"Données : {simple}")
            else:
                keys = [k for k in data.keys() if not k.startswith("_")]
                if keys:
                    lines.append(f"Données disponibles : {', '.join(keys[:5])}")

        return ("\n" + "\n".join(lines)) if lines else ""

    # ── Appel API Groq ────────────────────────────────────────────────────────

    def _call_groq(self, messages: list) -> str | None:
        if not self._client:
            return None
        try:
            completion = self._client.chat.completions.create(
                model=GROQ_MODEL_NAME,
                messages=messages,
                max_tokens=_GROQ_RESPONSE_TOKENS,
                temperature=0.85,
                top_p=0.92,
                timeout=_TIMEOUT_S,
            )
            content = completion.choices[0].message.content
            return (content or "").strip()
        except Exception as e:
            self._set_cooldown_from_error(e)
            logger.warning(f"JarvisVoice Groq call failed: {e}")
            return None

    def _set_cooldown_from_error(self, error: Exception):
        msg = str(error)
        if "rate_limit_exceeded" not in msg and "Rate limit reached" not in msg:
            return
        wait_s = 300.0
        m = re.search(r"Please try again in\s+(?:(\d+)m)?([\d\.]+)s", msg)
        if m:
            wait_s = float(m.group(1) or 0) * 60.0 + float(m.group(2) or 0)
        self._cooldown_until = time.time() + wait_s
        logger.warning(f"JarvisVoice Groq cooldown ~{int(wait_s)}s (fallback actif).")

    # ── Fallback ──────────────────────────────────────────────────────────────

    def _fallback(self, intent: str, exec_result: dict) -> str:
        variants = _FALLBACKS.get(intent)
        if variants:
            return random.choice(variants)
        success = bool((exec_result or {}).get("success", False))
        bucket  = "_success" if success else "_error"
        return random.choice(_FALLBACKS[bucket])

    # ── Fact-grounding ────────────────────────────────────────────────────────

    @staticmethod
    def _is_fact_sensitive_intent(intent: str) -> bool:
        return intent in _FACT_SENSITIVE_INTENTS

    def _grounding_hint(self, intent: str, exec_result: dict) -> str:
        if intent == "SYSTEM_DISK":
            return self._disk_truth_hint(exec_result)
        msg     = str((exec_result or {}).get("message", "")).strip()
        data    = (exec_result or {}).get("data") or {}
        display = data.get("display") if isinstance(data, dict) else ""
        display_line = ""
        if isinstance(display, str) and display.strip():
            display_line = display.strip().splitlines()[-1][:120]
        parts = []
        if msg:
            parts.append(f"Données: {msg[:150]}")
        if display_line:
            parts.append(f"Extrait: {display_line}")
        return " | ".join(parts) if parts else ""

    def _grounded_fallback(self, intent: str, exec_result: dict) -> str | None:
        deterministic = self._deterministic_message(intent, exec_result)
        if deterministic:
            return deterministic
        data = (exec_result or {}).get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("display"), str) and data.get("display", "").strip():
            return "C'est affiché."
        raw_msg = str((exec_result or {}).get("message", "")).strip()
        if raw_msg:
            return raw_msg[:180]
        return None

    def _disk_truth_hint(self, exec_result: dict) -> str:
        data       = (exec_result or {}).get("data") or {}
        partitions = data.get("partitions") if isinstance(data, dict) else None
        if not isinstance(partitions, list) or not partitions:
            return ""
        accessible = [p for p in partitions if isinstance(p, dict) and isinstance(p.get("free_gb"), (int, float))]
        if not accessible:
            return ""
        total_free  = round(sum(float(p.get("free_gb", 0.0)) for p in accessible), 1)
        drive_infos = []
        for part in accessible:
            dev   = str(part.get("device", "")).upper()
            drive = dev[:2] if len(dev) >= 2 and dev[1] == ":" else dev
            drive_infos.append(f"{drive}: {float(part.get('free_gb', 0.0)):.1f}Go")
        return (f"Total: {total_free:.1f}Go | " + " | ".join(drive_infos) +
                f" (Cite ces chiffres EXACTEMENT, pas d'autres)")

    def _response_is_fact_consistent(self, intent: str, text: str, exec_result: dict) -> bool:
        if intent == "SYSTEM_DISK":
            return self._disk_message_is_consistent(text, exec_result)
        mentioned = self._extract_numbers(text)
        if not mentioned:
            return True
        allowed = self._extract_allowed_numbers(exec_result)
        if not allowed:
            return True
        for m in mentioned:
            if not any(abs(m - a) <= 0.2 for a in allowed):
                return False
        return True

    @staticmethod
    def _extract_numbers(text: str) -> list[float]:
        vals = []
        for raw in re.findall(r"\d+(?:[\.,]\d+)?", text or ""):
            try:
                vals.append(float(raw.replace(",", ".")))
            except ValueError:
                continue
        return vals

    def _extract_allowed_numbers(self, exec_result: dict) -> list[float]:
        out: list[float] = []

        def walk(obj):
            if isinstance(obj, bool):
                return
            if isinstance(obj, (int, float)):
                out.append(float(obj))
                return
            if isinstance(obj, str):
                out.extend(self._extract_numbers(obj))
                return
            if isinstance(obj, dict):
                for v in obj.values():
                    walk(v)
                return
            if isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk((exec_result or {}).get("message", ""))
        walk((exec_result or {}).get("data", {}))
        return sorted({round(v, 2) for v in out})

    def _disk_message_is_consistent(self, text: str, exec_result: dict) -> bool:
        data       = (exec_result or {}).get("data") or {}
        partitions = data.get("partitions") if isinstance(data, dict) else None
        if not isinstance(partitions, list) or not partitions:
            return True
        accessible = [p for p in partitions if isinstance(p, dict) and isinstance(p.get("free_gb"), (int, float))]
        if not accessible:
            return True
        lowered    = text.lower()
        total_free = round(sum(float(p.get("free_gb", 0.0)) for p in accessible), 1)
        allowed_drives   = set()
        required_pairs   = []
        for part in accessible:
            dev   = str(part.get("device", "")).upper()
            drive = dev[:2] if len(dev) >= 2 and dev[1] == ":" else dev
            if not drive:
                continue
            allowed_drives.add(drive)
            required_pairs.append((drive, float(part.get("free_gb", 0.0))))
        mentioned_drives = set(re.findall(r"\b([A-Z]:)", text.upper()))
        if not mentioned_drives.issubset(allowed_drives):
            return False
        if not self._contains_value(lowered, total_free):
            return False
        for drive, free_gb in required_pairs:
            drive_letter = drive[0].lower()
            if not re.search(rf"\b{drive_letter}\s*:", lowered):
                return False
            if not self._contains_value(lowered, free_gb):
                return False
        return True

    @staticmethod
    def _contains_value(text_lower: str, value: float) -> bool:
        dot   = f"{value:.1f}"
        comma = dot.replace(".", ",")
        return dot in text_lower or comma in text_lower

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        text = text.strip().strip('"').strip("'").strip()
        bad_starts = ["Jarvis : ", "JARVIS : ", "Jarvis: ", "JARVIS: ", "Réponse : ", "Response: "]
        for bad in bad_starts:
            if text.startswith(bad):
                text = text[len(bad):].strip()
        for sep in ["\n\n", "\n"]:
            if sep in text:
                text = text.split(sep)[0].strip()
        return text or "C'est fait."

    @staticmethod
    def _safe_params(params: dict) -> str:
        if not params:
            return "{}"
        safe = {}
        for k, v in (params or {}).items():
            if isinstance(v, (str, int, float, bool)):
                safe[k] = v
            elif isinstance(v, list):
                safe[k] = v[:3]
        return str(safe)

    @property
    def is_available(self) -> bool:
        return self._available