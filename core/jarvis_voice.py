"""
core/jarvis_voice.py — Moteur de réponse naturelle de Jarvis
=============================================================

C'est ici que Jarvis cesse d'être un robot et devient un assistant humain.

Au lieu de retourner le message brut du module ("'chrome' lancée."),
JarvisVoice envoie à Groq :
  - la commande originale de l'utilisateur
  - le résultat technique de l'exécution (succès/échec + données)
  - les N derniers échanges de la conversation
  - l'intent reconnu

Et Groq génère une réponse naturelle, variée, contextuelle — comme Jarvis
dans Iron Man. Chaque réponse est unique même pour la même commande.

GARANTIES :
  - Jamais de réponse vide : fallback intelligent si Groq échoue
  - Pas de latence bloquante : timeout 8s, fallback instantané
  - Réponses courtes par défaut (1-2 phrases) sauf si données à afficher
  - Ton adapté : décontracté, précis, jamais robotique
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

_TIMEOUT_S = 8          # timeout Groq pour la génération de réponse
_MAX_RESULT_CHARS = 600 # on tronque les données brutes envoyées à Groq
_GROQ_RESPONSE_TOKENS = 120  # réponses courtes, naturelles

# ── Fallbacks par intent (si Groq indisponible) ───────────────────────────────
# Chaque liste a plusieurs variantes → tirée aléatoirement
_FALLBACKS: dict[str, list[str]] = {
    "APP_OPEN": [
        "C'est lancé.",
        "Voilà, c'est ouvert.",
        "J'ai ouvert ça pour toi.",
        "C'est parti.",
    ],
    "APP_CLOSE": [
        "Fermé.",
        "C'est fermé.",
        "J'ai fermé ça.",
    ],
    "SYSTEM_INFO": [
        "Voici les infos système.",
        "J'ai récupéré l'état du système.",
    ],
    "SYSTEM_SHUTDOWN": [
        "Extinction programmée.",
        "Le PC va s'éteindre dans quelques instants.",
    ],
    "SYSTEM_RESTART": [
        "Redémarrage en cours.",
        "Je redémarre la machine.",
    ],
    "SYSTEM_LOCK": [
        "Écran verrouillé.",
        "J'ai verrouillé l'écran.",
    ],
    "AUDIO_VOLUME_SET": [
        "Volume ajusté.",
        "C'est réglé.",
        "Volume modifié.",
    ],
    "AUDIO_VOLUME_UP": [
        "Volume monté.",
        "J'ai augmenté le son.",
    ],
    "AUDIO_VOLUME_DOWN": [
        "Volume baissé.",
        "J'ai réduit le son.",
    ],
    "AUDIO_MUTE": [
        "Son coupé.",
        "Muet activé.",
    ],
    "BROWSER_SEARCH": [
        "Je cherche ça.",
        "Recherche lancée.",
        "C'est parti sur le web.",
    ],
    "BROWSER_URL": [
        "Page ouverte.",
        "Voilà, la page est chargée.",
    ],
    "FILE_SEARCH": [
        "Je cherche ce fichier.",
        "Recherche en cours.",
    ],
    "FILE_OPEN": [
        "Fichier ouvert.",
        "C'est ouvert.",
    ],
    "SCREEN_CAPTURE": [
        "Capture effectuée.",
        "Screenshot pris.",
    ],
    "MACRO_RUN": [
        "Macro lancée.",
        "Séquence exécutée.",
    ],
    "REPEAT_LAST": [
        "Je répète la dernière commande.",
        "C'est reparti.",
    ],
    "HISTORY_SHOW": [
        "Voici l'historique.",
        "J'ai l'historique.",
    ],
    "HELP": [
        "Je suis JARVIS. Voici tout ce que je peux faire pour toi.",
        "Bien sûr, laisse-moi te présenter mes capacités.",
        "Je m'appelle JARVIS — voici la liste complète de mes fonctions.",
        "Avec plaisir, voici ce que je sais faire.",
    ],
    "GREETING": [
        "Bonjour ! Qu'est-ce que je peux faire pour toi ?",
        "Salut ! JARVIS à ton service.",
        "Hello ! Dis-moi ce que tu veux.",
        "Bonjour ! Je t'écoute.",
    ],
    "INCOMPLETE": [
        "Il me manque une info pour faire ça — tu peux préciser ?",
        "Je vais avoir besoin d'un peu plus de détails.",
        "Presque — dis-moi juste ce que tu veux exactement.",
        "Je suis là, mais j'ai besoin d'un peu plus pour agir.",
    ],
    "UNKNOWN": [
        "Je n'ai pas bien saisi. Tu peux reformuler ?",
        "Hmm, je n'ai pas compris. Dis-moi autrement ?",
        "Pas sûr de ce que tu veux. Tu peux préciser ?",
    ],
    "_success": [
        "C'est fait.",
        "Voilà, c'est réglé.",
        "Ça marche.",
        "Effectué.",
        "Tout bon.",
    ],
    "_error": [
        "Quelque chose n'a pas fonctionné.",
        "Je n'ai pas pu faire ça.",
        "Raté — je t'explique pourquoi.",
        "Problème rencontré.",
    ],
}

# ── Prompt système de JarvisVoice ─────────────────────────────────────────────
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
2. Ne confirme pas bêtement — ajoute toujours de la valeur :
   - Mauvais : "J'ai ouvert Chrome."
   - Bon : "Chrome est lancé — tu cherches quelque chose ?"
3. Si la même action se répète → remarque légère :
   - Chrome ouvert 3 fois : "Chrome encore — grand jour de navigation ?"
   - Volume baissé souvent : "Je note que tu préfères le son bas."
4. Après une recherche réussie → propose l'étape suivante :
   - "Trouvé — je l'ouvre ?"
   - "C'est là — tu veux y accéder ?"
5. Après une erreur → explique et propose une alternative :
   - "Pas trouvé ici — je cherche ailleurs ?"
   - "Raté — Chrome est peut-être fermé, je le lance ?"
6. Pour une salutation → réponse courte et vivante :
   - "Présent. Qu'est-ce qu'on fait ?"
   - "Jarvis à l'écoute."
   - "Opérationnel. À toi."
7. Pour une question simple → réponse directe uniquement :
   - Heure : "Il est 14h32."
   - État système : "CPU à 23%, RAM à 4.2 Go. Tout va bien."
8. Ne commence JAMAIS par : "Bien sûr", "D'accord", "Absolument",
   "Certainement", "Je vais", "Voici"
9. Ne dis JAMAIS "Commande exécutée" ou "Action effectuée"
10. Tu t'appelles JARVIS — assume cette identité naturellement
11. Si des données sont affichées dans la bulle → dis juste
    "C'est affiché." ou "Voilà." — ne répète pas le contenu

EXEMPLES PARFAITS :
- Ouvre Chrome → "Chrome est lancé — un site à ouvrir ?"
- Volume 70% → "À 70%. Je retiens ça."
- Fichier trouvé → "Trouvé dans E:/Médias — je l'ouvre ?"
- Bonjour → "Jarvis à l'écoute. Qu'est-ce qu'on fait ?"
- Éteins dans 10min → "Extinction dans 10 minutes. Je note."
- Erreur → "Pas pu faire ça — reformule ou je cherche autrement ?"
- Chrome 3ème fois → "Chrome encore — grand jour de navigation ?"
- Aide → "Je suis JARVIS. Dis-moi ce que tu veux faire."

Réponds UNIQUEMENT avec la phrase de Jarvis. Rien d'autre."""


class JarvisVoice:
    """
    Génère des réponses vocales naturelles via Groq.

    Usage dans agent.py :
        natural_msg = self.voice.generate(
            user_command=raw,
            intent=intent,
            params=params,
            exec_result=result,
            conversation_history=self.context.history,
        )
    """

    def __init__(self):
        self._client = None
        self._available = False
        self._cooldown_until = 0.0
        self._init_groq()

    def _init_groq(self):
        """Initialise le client Groq (lazy, silencieux si clé absente)."""
        if not GROQ_API_KEY:
            logger.warning("JarvisVoice: GROQ_API_KEY manquante — mode fallback activé.")
            return
        try:
            from groq import Groq
            self._client = Groq(api_key=GROQ_API_KEY)
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
    ) -> str:
        """
        Génère la réponse naturelle de Jarvis.

        Args:
            user_command        : ce que l'utilisateur a dit ("ouvre chrome")
            intent              : intent reconnu ("APP_OPEN")
            params              : paramètres extraits ({"app_name": "chrome"})
            exec_result         : résultat de l'exécution ({"success": True, ...})
            conversation_history: historique des échanges (liste de {role, content})

        Returns:
            Une phrase naturelle, prête à être affichée/prononcée.
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
            )
            response = self._call_groq(messages)
            if response:
                return self._clean(response)
        except Exception as e:
            logger.error(f"JarvisVoice.generate erreur: {e}")

        return self._fallback(intent, exec_result)

    def generate_proactive(self, situation: str, context: dict | None = None) -> str:
        """
        Génère un message proactif de Jarvis (ex: alerte batterie faible, tâche terminée).

        Args:
            situation : description de la situation ("batterie à 15%")
            context   : données supplémentaires optionnelles

        Returns:
            Une alerte naturelle et brève.
        """
        if not self._available:
            return f"Attention : {situation}"

        prompt = (
            f"Tu es JARVIS. Envoie une alerte brève et naturelle à l'utilisateur "
            f"pour la situation suivante : {situation}. "
            f"1 phrase maximum. Commence directement par l'alerte, sans formule d'intro."
        )
        try:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            response = self._call_groq(messages)
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
    ) -> list:
        """
        Construit la liste de messages pour Groq.

        Structure :
          system  → personnalité Jarvis
          [hist]  → N derniers échanges user/assistant (contexte conv)
          user    → résumé de la commande + résultat technique
        """
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

        # Injecter les derniers échanges (sans le tout dernier qui est la commande courante)
        for msg in history[-8:]:
            role = msg.get("role", "user")
            content = str(msg.get("content", "")).strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Construire le résumé de la commande actuelle
        success = bool((exec_result or {}).get("success", False))
        raw_msg = str((exec_result or {}).get("message", "")).strip()
        data = (exec_result or {}).get("data")

        # Résumer les données si présentes (on ne les envoie pas brutes)
        data_hint = ""
        if data and isinstance(data, dict):
            keys = [k for k in data.keys() if not k.startswith("_")]
            if keys:
                data_hint = f" Données disponibles : {', '.join(keys[:5])}."
        elif data and isinstance(data, list):
            data_hint = f" {len(data)} éléments retournés."

        # Message de contexte pour Groq
        context_msg = (
            f"Commande utilisateur : \"{user_command}\"\n"
            f"Intent reconnu : {intent}\n"
            f"Paramètres : {self._safe_params(params)}\n"
            f"Résultat : {'SUCCÈS' if success else 'ÉCHEC'}\n"
            f"Message technique : {raw_msg[:_MAX_RESULT_CHARS]}"
            f"{data_hint}\n\n"
            f"Génère la réponse naturelle de Jarvis."
        )

        messages.append({"role": "user", "content": context_msg})
        return messages

    # ── Appel API Groq ────────────────────────────────────────────────────────

    def _call_groq(self, messages: list) -> str | None:
        """Appel API Groq avec timeout. Retourne None en cas d'échec."""
        if not self._client:
            return None

        try:
            completion = self._client.chat.completions.create(
                model=GROQ_MODEL_NAME,
                messages=messages,
                max_tokens=_GROQ_RESPONSE_TOKENS,
                temperature=0.85,      # variabilité haute → réponses différentes
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

        # Exemple Groq: "Please try again in 7m33.6s"
        wait_s = 300.0
        m = re.search(r"Please try again in\s+(?:(\d+)m)?([\d\.]+)s", msg)
        if m:
            minutes = float(m.group(1) or 0)
            seconds = float(m.group(2) or 0)
            wait_s = (minutes * 60.0) + seconds

        self._cooldown_until = time.time() + wait_s
        logger.warning(f"JarvisVoice Groq cooldown ~{int(wait_s)}s (fallback actif).")

    # ── Fallback ──────────────────────────────────────────────────────────────

    def _fallback(self, intent: str, exec_result: dict) -> str:
        """
        Réponse de fallback quand Groq n'est pas disponible.
        Tire aléatoirement parmi les variantes de l'intent.
        """
        # Essayer d'abord les variantes spécifiques à l'intent
        variants = _FALLBACKS.get(intent)
        if variants:
            return random.choice(variants)

        # Fallback générique selon succès/échec
        success = bool((exec_result or {}).get("success", False))
        bucket = "_success" if success else "_error"
        return random.choice(_FALLBACKS[bucket])

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        """Nettoie la réponse Groq : supprime guillemets parasites, retours à la ligne, etc."""
        text = text.strip().strip('"').strip("'").strip()
        # Supprimer les préfixes indésirables que Groq peut ajouter
        bad_starts = [
            "Jarvis : ", "JARVIS : ", "Jarvis: ", "JARVIS: ",
            "Réponse : ", "Response: ",
        ]
        for bad in bad_starts:
            if text.startswith(bad):
                text = text[len(bad):].strip()
        # Garder seulement la première phrase si Groq a débordé
        for sep in ["\n\n", "\n"]:
            if sep in text:
                text = text.split(sep)[0].strip()
        return text or "C'est fait."

    @staticmethod
    def _safe_params(params: dict) -> str:
        """Résume les params de façon lisible pour Groq (évite les données binaires)."""
        if not params:
            return "{}"
        safe = {}
        for k, v in (params or {}).items():
            if isinstance(v, (str, int, float, bool)):
                safe[k] = v
            elif isinstance(v, list):
                safe[k] = v[:3]  # max 3 éléments
        return str(safe)

    @property
    def is_available(self) -> bool:
        return self._available