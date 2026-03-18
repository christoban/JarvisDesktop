"""
command_parser.py — Parseur IA conversationnel JARVIS
Transforme n'importe quelle phrase en intention structurée via Groq (LLaMA 3.3 70B).

DIFFÉRENCE CLÉ vs l'ancienne version :
  - Groq reçoit UNE VRAIE CONVERSATION (messages user/assistant alternés)
    au lieu d'un dump de contexte dans un message système séparé.
  - Le system prompt définit JARVIS comme un assistant conversationnel,
    pas comme un simple classificateur JSON.
  - Les few-shot examples montrent des échanges naturels, pas juste des paires commande→JSON.
  - Groq génère AUSSI le message de réponse naturelle de Jarvis (response_message),
    en plus de l'intent technique.
"""

import json
import re
import time
import unicodedata
from config.logger import get_logger
from config.settings import (
    GROQ_API_KEY,
    GROQ_MODEL_NAME,
)

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  CATALOGUE DES INTENTIONS — inchangé, compatible avec IntentExecutor
# ══════════════════════════════════════════════════════════════════════════════

INTENTS = {
    # ── Système ───────────────────────────────────────────────────────────────
    "SYSTEM_SHUTDOWN":     {
        "desc": "Éteindre l'ordinateur",
        "params": {"delay_seconds": "int, délai avant extinction (défaut 10)"}
    },
    "SYSTEM_RESTART":      {
        "desc": "Redémarrer l'ordinateur",
        "params": {"delay_seconds": "int, délai avant redémarrage (défaut 10)"}
    },
    "SYSTEM_SLEEP":        {"desc": "Mettre en veille",             "params": {}},
    "SYSTEM_HIBERNATE":    {"desc": "Mettre en hibernation",        "params": {}},
    "SYSTEM_LOCK":         {"desc": "Verrouiller l'écran",          "params": {}},
    "SYSTEM_UNLOCK":       {"desc": "Déverrouiller l'écran",        "params": {}},
    "SYSTEM_LOGOUT":       {"desc": "Déconnecter l'utilisateur",    "params": {}},
    "SYSTEM_TIME": {"desc": "Donner l'heure et la date actuelles", "params": {"timezone": "str optionnel"}},
    "SYSTEM_INFO":         {"desc": "Infos système : CPU, RAM, uptime", "params": {}},
    "SYSTEM_DISK":         {"desc": "Infos disque et stockage",     "params": {}},
    "SYSTEM_PROCESSES":    {
        "desc": "Lister les processus en cours",
        "params": {"sort_by": "str: 'cpu' ou 'ram'"}
    },
    "SYSTEM_KILL_PROCESS": {
        "desc": "Fermer/tuer un processus",
        "params": {"target": "str, nom ou PID du processus"}
    },
    "SYSTEM_NETWORK":      {"desc": "Infos réseau et IP",           "params": {}},
    "SYSTEM_TEMPERATURE":  {"desc": "Températures des composants",  "params": {}},
    "SYSTEM_FULL_REPORT":  {"desc": "Rapport système complet",      "params": {}},
    "SYSTEM_TASK_MANAGER": {"desc": "Ouvrir le gestionnaire des tâches", "params": {}},
    "SYSTEM_CANCEL_SHUTDOWN": {"desc": "Annuler une extinction programmée", "params": {}},
    "POWER_SLEEP":        {"desc": "Mettre le PC en veille", "params": {}},
    "POWER_HIBERNATE":    {"desc": "Mettre le PC en hibernation", "params": {}},
    "POWER_CANCEL":       {"desc": "Annuler extinction/redemarrage planifie", "params": {}},
    "POWER_STATE":        {"desc": "Afficher l'etat d'alimentation", "params": {}},
    "SCREEN_UNLOCK":      {"desc": "Deverrouiller l'ecran", "params": {"password": "str optionnel"}},
    "SCREEN_OFF":         {"desc": "Eteindre l'ecran sans verrouiller", "params": {}},
    "WAKE_ON_LAN":        {
        "desc": "Reveiller un PC par Wake-on-LAN",
        "params": {"mac_address": "str, adresse MAC", "broadcast": "str optionnel", "port": "int optionnel"}
    },

    # ── Reseau ─────────────────────────────────────────────────────────────────
    "WIFI_LIST":       {"desc": "Lister les reseaux Wi-Fi disponibles",     "params": {}},
    "WIFI_CONNECT":    {"desc": "Se connecter a un reseau Wi-Fi",
                        "params": {"ssid": "str, nom du reseau", "password": "str optionnel"}},
    "WIFI_DISCONNECT": {"desc": "Se deconnecter du Wi-Fi courant",          "params": {}},
    "WIFI_ENABLE":     {"desc": "Activer l'interface Wi-Fi",                "params": {}},
    "WIFI_DISABLE":    {"desc": "Desactiver l'interface Wi-Fi",             "params": {}},
    "BLUETOOTH_ENABLE":  {"desc": "Activer Bluetooth",                      "params": {}},
    "BLUETOOTH_DISABLE": {"desc": "Desactiver Bluetooth",                   "params": {}},
    "BLUETOOTH_LIST":    {"desc": "Lister les appareils Bluetooth",         "params": {}},
    "NETWORK_INFO":      {"desc": "Afficher les informations reseau",       "params": {}},

    # ── Applications ──────────────────────────────────────────────────────────
    "APP_OPEN":    {
        "desc": "Ouvrir une application",
        "params": {"app_name": "str, nom de l'application", "args": "list, arguments optionnels"}
    },
    "APP_CLOSE":   {"desc": "Fermer une application", "params": {"app_name": "str"}},
    "APP_RESTART": {"desc": "Redémarrer une application", "params": {"app_name": "str"}},
    "APP_CHECK":   {"desc": "Vérifier si une application est ouverte", "params": {"app_name": "str"}},
    "APP_LIST_RUNNING": {"desc": "Lister les applications ouvertes", "params": {}},
    "APP_LIST_KNOWN":   {"desc": "Lister les applications connues",  "params": {}},

    # ── Fichiers & Dossiers ───────────────────────────────────────────────────
    "FILE_SEARCH":  {
        "desc": "Rechercher un fichier par nom",
        "params": {"query": "str, nom ou partie du nom", "search_dirs": "list optionnel"}
    },
    "FILE_SEARCH_TYPE":    {"desc": "Rechercher des fichiers par type",
                            "params": {"extension": "str, extension ou catégorie"}},
    "FILE_SEARCH_CONTENT": {"desc": "Rechercher un mot dans le contenu des fichiers",
                            "params": {"keyword": "str"}},
    "FILE_OPEN":   {
        "desc": "Ouvrir un fichier",
        "params": {"path": "str, chemin ou nom", "search_dirs": "list optionnel", "target_type": "str optionnel"}
    },
    "FILE_CLOSE":    {"desc": "Fermer un fichier ou dossier ouvert", "params": {"path": "str"}},
    "FILE_COPY":     {"desc": "Copier un fichier",    "params": {"src": "str", "dst": "str"}},
    "FILE_MOVE":     {"desc": "Déplacer un fichier",  "params": {"src": "str", "dst": "str"}},
    "FILE_RENAME":   {"desc": "Renommer un fichier",  "params": {"path": "str", "new_name": "str"}},
    "FILE_DELETE":   {"desc": "Supprimer un fichier", "params": {"path": "str"}},
    "FILE_INFO":     {"desc": "Informations sur un fichier", "params": {"path": "str"}},
    "FOLDER_LIST":   {"desc": "Lister le contenu d'un dossier", "params": {"path": "str"}},
    "FOLDER_CREATE": {"desc": "Créer un dossier", "params": {"path": "str"}},
    "WINDOW_CLOSE":  {"desc": "Fermer une fenêtre ouverte", "params": {"query": "str, titre ou nom"}},

    # ── Navigateur ────────────────────────────────────────────────────────────────
    "BROWSER_OPEN":           {"desc": "Ouvrir le navigateur", "params": {"url": "str optionnel", "browser": "str optionnel"}},
    "BROWSER_CLOSE":          {"desc": "Fermer le navigateur / tous les onglets", "params": {}},
    "BROWSER_URL":            {"desc": "Ouvrir une URL spécifique", "params": {"url": "str", "new_tab": "bool optionnel"}},
    "BROWSER_NEW_TAB":        {"desc": "Ouvrir un nouvel onglet", "params": {"url": "str optionnel", "count": "int optionnel"}},
    "BROWSER_BACK":           {"desc": "Page précédente", "params": {"index": "int optionnel"}},
    "BROWSER_FORWARD":        {"desc": "Page suivante", "params": {"index": "int optionnel"}},
    "BROWSER_RELOAD":         {"desc": "Recharger la page", "params": {"hard": "bool optionnel", "index": "int optionnel"}},
    "BROWSER_CLOSE_TAB":      {"desc": "Fermer un onglet", "params": {"index": "int optionnel", "query": "str optionnel"}},
    "BROWSER_SEARCH":         {"desc": "Rechercher sur le web (Google par défaut)", "params": {"query": "str", "engine": "str optionnel", "new_tab": "bool optionnel"}},
    "BROWSER_SEARCH_YOUTUBE": {"desc": "Chercher une vidéo sur YouTube", "params": {"query": "str"}},
    "BROWSER_SEARCH_GITHUB":  {"desc": "Chercher sur GitHub", "params": {"query": "str"}},
    "BROWSER_OPEN_RESULT":    {"desc": "Ouvrir un résultat de recherche par numéro", "params": {"rank": "int (défaut 1)", "new_tab": "bool optionnel"}},
    "BROWSER_LIST_RESULTS":   {"desc": "Lister les résultats de recherche détectés", "params": {}},
    "BROWSER_GO_TO_SITE":     {"desc": "Naviguer vers un site connu (youtube, gmail, github...)", "params": {"site": "str", "query": "str optionnel"}},
    "BROWSER_NAVIGATE":       {"desc": "Naviguer vers une URL ou un site", "params": {"url": "str"}},
    "BROWSER_READ":           {"desc": "Lire le contenu texte de la page active", "params": {"index": "int optionnel"}},
    "BROWSER_PAGE_INFO":      {"desc": "Obtenir titre et URL de la page active", "params": {}},
    "BROWSER_EXTRACT_LINKS":  {"desc": "Extraire tous les liens de la page", "params": {}},
    "BROWSER_SUMMARIZE":      {"desc": "Résumer la page active via IA", "params": {"index": "int optionnel"}},
    "BROWSER_SCROLL":         {"desc": "Scroller la page", "params": {"direction": "str: up/down/top/bottom", "amount": "int optionnel"}},
    "BROWSER_CLICK_TEXT":     {"desc": "Cliquer sur un élément par son texte", "params": {"text": "str"}},
    "BROWSER_FILL_FIELD":     {"desc": "Remplir un champ de formulaire", "params": {"selector": "str CSS", "value": "str", "submit": "bool optionnel"}},
    "BROWSER_TYPE":           {"desc": "Taper du texte dans le champ actif de la page", "params": {"text": "str", "submit": "bool optionnel"}},
    "BROWSER_DOWNLOAD":       {"desc": "Télécharger un fichier", "params": {"url": "str optionnel", "link_text": "str optionnel"}},
    "BROWSER_LIST_TABS":      {"desc": "Lister les onglets ouverts", "params": {}},
    "BROWSER_SWITCH_TAB":     {"desc": "Basculer sur un onglet", "params": {"index": "int optionnel", "query": "str optionnel"}},
    "BROWSER_FIND_AND_OPEN":  {"desc": "Trouver le meilleur résultat pour une requête et l'ouvrir automatiquement", "params": {"query": "str"}},
    "BROWSER_CONTEXT":        {"desc": "Quel site est actif ? État du navigateur", "params": {}},

    # ── Audio ─────────────────────────────────────────────────────────────────
    "AUDIO_VOLUME_UP":   {"desc": "Monter le volume",  "params": {"step": "int, % à ajouter (défaut 10)"}},
    "AUDIO_VOLUME_DOWN": {"desc": "Baisser le volume", "params": {"step": "int, % à retirer (défaut 10)"}},
    "AUDIO_VOLUME_SET":  {"desc": "Définir le volume à un niveau précis", "params": {"level": "int, 0-100"}},
    "AUDIO_MUTE":        {"desc": "Couper/rétablir le son", "params": {}},
    "AUDIO_PLAY":        {"desc": "Jouer une musique ou un son", "params": {"query": "str, titre ou artiste"}},

    # ── Documents ─────────────────────────────────────────────────────────────
    "DOC_READ":        {"desc": "Lire un document Word ou PDF", "params": {"path": "str"}},
    "DOC_SUMMARIZE":   {"desc": "Résumer un document",          "params": {"path": "str"}},
    "DOC_SEARCH_WORD": {"desc": "Chercher un mot dans un document", "params": {"path": "str", "keyword": "str"}},

    # ── Écran ─────────────────────────────────────────────────────────────────
    "SCREEN_CAPTURE":      {"desc": "Capture d'écran",                        "params": {}},
    "SCREENSHOT_TO_PHONE": {"desc": "Envoyer une capture d'écran au téléphone", "params": {}},
    "SCREEN_BRIGHTNESS":   {"desc": "Régler la luminosité",  "params": {"level": "int, 0-100"}},
    "SCREEN_INFO":         {"desc": "Infos sur l'écran",                      "params": {}},
    "SCREEN_RECORD":       {"desc": "Enregistrer l'écran",                    "params": {}},

    # ── Historique / Macros ───────────────────────────────────────────────────
    "REPEAT_LAST":   {"desc": "Répéter la dernière commande", "params": {}},
    "HISTORY_SHOW":  {"desc": "Afficher l'historique des commandes", "params": {"count": "int optionnel"}},
    "HISTORY_CLEAR": {"desc": "Effacer l'historique", "params": {}},
    "HISTORY_SEARCH":{"desc": "Chercher dans l'historique", "params": {"keyword": "str"}},
    "MACRO_RUN":     {"desc": "Lancer une macro", "params": {"name": "str"}},
    "MACRO_LIST":    {"desc": "Lister les macros disponibles", "params": {}},
    "MACRO_SAVE":    {"desc": "Créer/sauvegarder une macro",
                      "params": {"name": "str", "commands": "list", "description": "str optionnel"}},
    "MACRO_DELETE":  {"desc": "Supprimer une macro", "params": {"name": "str"}},

    "GREETING": {"desc": "Salutation ou message d'accueil", "params": {}},

    "MEMORY_SHOW": {"desc": "Afficher ce dont Jarvis se souvient", "params": {}},

    "INCOMPLETE": {
        "desc": "Commande incomplète — paramètre manquant",
        "params": {
            "missing": "str, ce qui manque",
            "suggested_intent": "str, intention probable",
        }
    },

    # ── Aide / Inconnu ────────────────────────────────────────────────────────
    "HELP":    {"desc": "Afficher l'aide et les commandes disponibles", "params": {}},
    "UNKNOWN": {"desc": "Intention non reconnue", "params": {}},
}


# ══════════════════════════════════════════════════════════════════════════════
#  FEW-SHOT EXAMPLES — montrent à Groq le format attendu
#  Chaque paire (user, assistant) montre une compréhension naturelle complète
# ══════════════════════════════════════════════════════════════════════════════

FEW_SHOT_EXAMPLES = [
    (
        "mets chrome",
        '{"intent":"APP_OPEN","params":{"app_name":"chrome","args":[]},"confidence":0.99,'
        '"response_message":"Je lance Chrome tout de suite."}'
    ),
    (
        "monte le son un peu",
        '{"intent":"AUDIO_VOLUME_UP","params":{"step":10},"confidence":0.98,'
        '"response_message":"Volume monté de 10%."}'
    ),
    (
        "mets le volume à 70",
        '{"intent":"AUDIO_VOLUME_SET","params":{"level":70},"confidence":0.99,'
        '"response_message":"Volume réglé à 70%."}'
    ),
    (
        "cherche les dernières nouvelles sur python",
        '{"intent":"BROWSER_SEARCH","params":{"query":"dernières nouvelles python"},"confidence":0.98,'
        '"response_message":"Je lance la recherche sur les dernières nouvelles Python."}'
    ),
    (
        "éteins l'ordi dans 5 minutes",
        '{"intent":"SYSTEM_SHUTDOWN","params":{"delay_seconds":300},"confidence":0.99,'
        '"response_message":"D\'accord, j\'éteins le PC dans 5 minutes."}'
    ),
    (
        "quel est l'état de mon PC",
        '{"intent":"SYSTEM_INFO","params":{},"confidence":0.97,'
        '"response_message":"Je vérifie l\'état du système."}'
    ),
    (
        "coupe le son",
        '{"intent":"AUDIO_MUTE","params":{},"confidence":0.99,'
        '"response_message":"Son coupé."}'
    ),
    (
        "ouvre mes documents",
        '{"intent":"FOLDER_LIST","params":{"path":"Documents"},"confidence":0.97,'
        '"response_message":"J\'ouvre ton dossier Documents."}'
    ),
    (
        "va sur youtube et cherche Python tutorial",
        '{"intent":"BROWSER_GO_TO_SITE","params":{"site":"youtube","query":"Python tutorial"},"confidence":0.99,'
        '"response_message":"Je cherche Python tutorial sur YouTube."}'
    ),
    (
        "résume cette page",
        '{"intent":"BROWSER_SUMMARIZE","params":{},"confidence":0.98,'
        '"response_message":"Voici le résumé de la page."}'
    ),
    (
        "scrolle vers le bas",
        '{"intent":"BROWSER_SCROLL","params":{"direction":"down"},"confidence":0.99,'
        '"response_message":"Je descends dans la page."}'
    ),
    (
        "ouvre le deuxième résultat",
        '{"intent":"BROWSER_OPEN_RESULT","params":{"rank":2},"confidence":0.99,'
        '"response_message":"J\'ouvre le deuxième résultat."}'
    ),
    (
        "trouve-moi le meilleur tutoriel Python et ouvre-le",
        '{"intent":"BROWSER_FIND_AND_OPEN","params":{"query":"tutoriel Python"},"confidence":0.95,'
        '"response_message":"Je cherche et j\'ouvre le meilleur résultat."}'
    ),
    (
        "referme là",
        '{"intent":"WINDOW_CLOSE","params":{"query":""},"confidence":0.97,'
        '"response_message":"Je ferme la fenêtre."}'
    ),
    (
        "ferme ça",
        '{"intent":"WINDOW_CLOSE","params":{"query":""},"confidence":0.97,'
        '"response_message":"Fenêtre fermée."}'
    ),
    (
        "ferme cette fenêtre",
        '{"intent":"WINDOW_CLOSE","params":{"query":""},"confidence":0.99,'
        '"response_message":"Fenêtre fermée."}'
    ),
    (
        "ouvre le dossier films",
        '{"intent":"FILE_OPEN","params":{"path":"films","target_type":"directory"},"confidence":0.99,'
        '"response_message":"J\'ouvre le dossier films."}'
    ),
    (
        "liste le contenu du dossier films",
        '{"intent":"FOLDER_LIST","params":{"path":"films"},"confidence":0.99,'
        '"response_message":"Voici le contenu du dossier films."}'
    ),
    (
        "recherche le dossier films et ouvre le",
        '{"intent":"FILE_OPEN","params":{"path":"films","target_type":"directory"},"confidence":0.98,'
        '"response_message":"Je cherche et ouvre le dossier films."}'
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND PARSER
# ══════════════════════════════════════════════════════════════════════════════

class CommandParser:
    """
    Parse une commande en langage naturel via Groq (LLaMA 3.3 70B).

    NOUVEAUTÉS vs ancienne version :
    - L'historique est injecté comme de vrais messages user/assistant alternés,
      pas comme un dump texte dans un message système. Groq voit une vraie conversation.
    - Le system prompt définit Jarvis comme un assistant, pas un classificateur.
    - Groq génère aussi response_message : la réponse naturelle que Jarvis va dire.
    - Le _semantic_guard reste en fallback mais ne court-circuite plus Groq.
    """

    def __init__(self):
        self.client       = None
        self.ai_available = False
        self._groq_cooldown_until = 0.0
        self._init_client()

    def _can_use_groq(self) -> bool:
        return self.ai_available and time.time() >= self._groq_cooldown_until

    def _set_groq_cooldown_from_error(self, error: Exception):
        msg = str(error)
        if "rate_limit_exceeded" not in msg and "Rate limit reached" not in msg:
            return

        # Exemple Groq: "Please try again in 47m42.432s"
        wait_s = 600.0
        m = re.search(r"Please try again in\s+(?:(\d+)m)?([\d\.]+)s", msg)
        if m:
            minutes = float(m.group(1) or 0)
            seconds = float(m.group(2) or 0)
            wait_s = (minutes * 60.0) + seconds

        self._groq_cooldown_until = time.time() + wait_s
        logger.warning(f"Groq en cooldown parser pendant ~{int(wait_s)}s (fallback actif).")

    def _init_client(self):
        try:
            if not GROQ_API_KEY or GROQ_API_KEY.startswith("VOTRE"):
                logger.warning("CommandParser : clé Groq non configurée.")
                return
            from groq import Groq
            self.client       = Groq(api_key=GROQ_API_KEY)
            self.ai_available = True
            logger.info(f"CommandParser → Groq ({GROQ_MODEL_NAME}) ✓")
        except ImportError:
            logger.warning("Groq SDK non installé. Exécute : pip install groq")
        except Exception as e:
            logger.error(f"Erreur init Groq : {e}")

    def health_check(self) -> dict:
        if not self.ai_available:
            return {"available": False, "message": "Groq non configuré.", "latency_ms": 0}
        try:
            start  = time.time()
            result = self.parse("test de connexion")
            return {
                "available":   True,
                "message":     "Groq opérationnel.",
                "latency_ms":  int((time.time() - start) * 1000),
                "model":       GROQ_MODEL_NAME,
                "test_result": result,
            }
        except Exception as e:
            return {"available": False, "message": f"Erreur : {e}", "latency_ms": 0}

    # ──────────────────────────────────────────────────────────────────────────
    #  PARSE PRINCIPAL
    # ──────────────────────────────────────────────────────────────────────────

    def parse(self, command: str, retries: int = 2) -> dict:
        """Parse sans contexte conversationnel."""
        command = command.strip()
        if not command:
            return self._unknown(command, "Commande vide.")

        if self._can_use_groq():
            for attempt in range(retries + 1):
                try:
                    result           = self._call_groq_ai(command, history=[])
                    result           = self._semantic_guard(command, result)
                    result["source"] = "groq"
                    logger.info(f"Intent: {result['intent']} (conf={result['confidence']:.2f}, src=groq)")
                    return result
                except Exception as e:
                    logger.warning(f"Groq tentative {attempt + 1} échouée : {e}")
                    self._set_groq_cooldown_from_error(e)
                    if time.time() < self._groq_cooldown_until:
                        break
                    if attempt < retries:
                        time.sleep(0.5 * (attempt + 1))

        result           = self._semantic_guard(command, self._fallback_keywords(command))
        result["source"] = "fallback"
        return result

    def parse_with_context(self, command: str, history: list = None, retries: int = 2) -> dict:
        """
        Parse avec l'historique de conversation.

        CLEF : history est une liste de dicts {"role": "user"|"assistant", "content": str}.
        Ces messages sont injectés comme de vrais tours de conversation dans l'appel Groq,
        PAS comme un dump texte dans un message système séparé.
        Groq voit ainsi la vraie conversation et comprend les références contextuelles.
        """
        command = command.strip()
        if not command:
            return self._unknown(command, "Commande vide.")

        if self._can_use_groq():
            for attempt in range(retries + 1):
                try:
                    result           = self._call_groq_ai(command, history=history or [])
                    result           = self._semantic_guard(command, result)
                    result["source"] = "groq"
                    logger.info(f"Intent: {result['intent']} (conf={result['confidence']:.2f}, src=groq+ctx)")
                    return result
                except Exception as e:
                    logger.warning(f"Groq+ctx tentative {attempt + 1} échouée : {e}")
                    self._set_groq_cooldown_from_error(e)
                    if time.time() < self._groq_cooldown_until:
                        break
                    if attempt < retries:
                        time.sleep(0.5 * (attempt + 1))

        result           = self._semantic_guard(command, self._fallback_keywords(command))
        result["source"] = "fallback"
        return result

    # ──────────────────────────────────────────────────────────────────────────
    #  APPEL GROQ — ARCHITECTURE CONVERSATIONNELLE
    # ──────────────────────────────────────────────────────────────────────────

    def _call_groq_ai(self, command: str, history: list = None) -> dict:
        """
        Appel Groq avec architecture de messages conversationnelle correcte.

        Structure des messages envoyés à Groq :
          1. system  → personnalité Jarvis + catalogue d'intentions
          2. user    → few-shot example 1
          3. assistant → réponse JSON few-shot 1
          ... (autres few-shot)
          4. user    → historique réel tour 1 (si disponible)
          5. assistant → historique réel tour 1 réponse
          ... (autres tours d'historique)
          6. user    → la commande actuelle

        Ainsi Groq voit une vraie conversation et résout les références contextuelles
        ("celui-là", "aussi pour chrome", "le même") naturellement.
        """
        # Extraire la mémoire de l'historique si présente
        memory_summary = ""
        history_to_use = []
        if history:
            for msg in history:
                if msg.get("role") == "system" and msg.get("memory"):
                    memory_summary = msg["memory"]
                else:
                    history_to_use.append(msg)
        else:
            history_to_use = history or []

        messages = [{"role": "system", "content": self._build_system_prompt(memory_summary)}]

        # Few-shot examples (calibrage du format JSON + ton naturel)
        for user_msg, assistant_msg in FEW_SHOT_EXAMPLES[:6]:
            messages.append({"role": "user",      "content": user_msg})
            messages.append({"role": "assistant", "content": assistant_msg})

        # Historique réel de la conversation — injecté comme vrais messages
        # (C'est le changement clé : plus de dump texte dans un message système)
        if history_to_use:
            for msg in history_to_use[-(12):]:  # 6 derniers échanges = 12 messages
                role    = msg.get("role", "user")
                content = str(msg.get("content", "")).strip()
                if content and role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})

        # La commande actuelle
        messages.append({"role": "user", "content": command})

        response = self.client.chat.completions.create(
            model=GROQ_MODEL_NAME,
            messages=messages,
            temperature=0.1,   # Légèrement supérieur à 0 pour des réponses plus naturelles
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        raw_json = response.choices[0].message.content.strip()
        return self._parse_json_response(raw_json, command)

    # ──────────────────────────────────────────────────────────────────────────
    #  PROMPT SYSTÈME — Jarvis conversationnel, pas classificateur
    # ──────────────────────────────────────────────────────────────────────────

    def _build_system_prompt(self, memory_summary: str = "") -> str:
        intents_block = "\n".join(
            f'- {key}: {v["desc"]}  '
            f'[params: {", ".join(f"{k}={t}" for k, t in v["params"].items()) or "aucun"}]'
            for key, v in INTENTS.items()
            if key != "UNKNOWN"
        )

        memory_block = ""
        if memory_summary:
            memory_block = f"""
MÉMOIRE (ce que tu sais déjà sur l'utilisateur) :
{memory_summary}

Utilise cette mémoire pour :
- Résoudre "le", "ça", "celui-là" → ils référencent les éléments ci-dessus
- Personnaliser les réponses : si tu sais que son volume habituel est 70%,
  tu peux dire "comme d'habitude ?" quand il demande de régler le volume
- Détecter les répétitions et faire une remarque légère
"""

        return f"""Tu es JARVIS, l'assistant IA de contrôle PC. Tu es conversationnel, intelligent et proactif.
Tu comprends le français, l'anglais, les tournures naturelles, l'argot et les références contextuelles.
Tu dois comprendre l'intention COMPLÈTE même si la phrase est longue, mélangée ou imprécise.

TON TRAVAIL :
Analyser ce que l'utilisateur veut VRAIMENT faire et retourner UN JSON structuré.
Si une phrase contient plusieurs actions ("recherche le dossier films et ouvre le"),
retiens l'ACTION FINALE — celle que l'utilisateur veut voir accomplie.

INTENTIONS DISPONIBLES :
{intents_block}{memory_block}

RÈGLES DE COMPRÉHENSION :

1. Lis TOUTE la phrase — ne te base jamais sur un seul mot-clé :
   - "mets le volume" → AUDIO_VOLUME_SET
   - "mets chrome" → APP_OPEN
   - "éteins l'écran" → SCREEN_OFF
   - "éteins l'ordi" → SYSTEM_SHUTDOWN

2. Utilise le contexte de la conversation pour résoudre les références :
   - "celui-là", "le même", "ça", "oui ouvre le" → regarde l'historique
   - "oui" après une question → confirme l'action précédente
   - "non" → annule

3. Extrais les paramètres précis :
   - "à 70%" → level=70
   - "dans 5 minutes" → delay_seconds=300
   - noms d'apps en minuscules

4. Commandes incomplètes → intent="INCOMPLETE" :
   Si l'information nécessaire manque totalement :
   - "fais une recherche" → INCOMPLETE, missing="sujet de recherche"
   - "ouvre un fichier" → INCOMPLETE, missing="nom du fichier"
   - "connecte au wifi" → INCOMPLETE, missing="nom du réseau"
   NE PAS inventer un paramètre vide — demander est mieux qu'exécuter faux.

5. Phrases avec plusieurs actions → retenir l'ACTION FINALE :
   - "recherche le dossier films et ouvre le" → FILE_OPEN path="films" target_type="directory"
   - "cherche rapport.pdf et lis le" → DOC_READ path="rapport.pdf"
   - "trouve chrome et lance le" → APP_OPEN app_name="chrome"
   - "va sur youtube et cherche Python" → BROWSER_GO_TO_SITE site="youtube" query="Python"

6. Salutations → intent="GREETING" :
   - "bonjour", "salut", "hello", "bonsoir" → GREETING
   JAMAIS HELP pour une simple salutation.

7. Questions sur Jarvis → intent="HELP" :
   - "que sais-tu faire", "quelles sont tes capacités", "aide" → HELP
   - "qui es-tu", "ton nom" → HELP

8. FOLDER_LIST vs FILE_OPEN — distinction critique :
   - "ouvre le dossier films" → FILE_OPEN path="films" target_type="directory"
   - "liste le dossier films" → FOLDER_LIST path="films"
   - "qu'est ce qu'il y a dans films" → FOLDER_LIST
   - "ouvre", "accède à", "va dans" → FILE_OPEN
   - "liste", "montre le contenu" → FOLDER_LIST

9. Fermer une fenêtre → WINDOW_CLOSE, pas SCREEN_OFF :
   - "referme là", "ferme ça", "ferme cette fenêtre" → WINDOW_CLOSE query=""
   - "éteins l'écran" → SCREEN_OFF
   - "referme" = fermer une fenêtre, JAMAIS éteindre l'écran

10. Contexte navigateur actif :
    - Si on vient d'ouvrir Chrome et l'utilisateur dit "cherche X" → BROWSER_SEARCH
    - Si on vient d'ouvrir un dossier et l'utilisateur dit "cherche X dedans" → FILE_SEARCH

11. Si vraiment impossible à comprendre → intent="UNKNOWN", confidence bas.

FORMAT DE RÉPONSE (JSON uniquement, rien d'autre) :
{{"intent": "NOM_INTENTION", "params": {{}}, "confidence": 0.95, "response_message": "Réponse naturelle."}}

EXEMPLES DE PARAMÈTRES :
- "éteins dans 30 secondes" → {{"delay_seconds": 30}}
- "ouvre word avec rapport.docx" → {{"app_name": "word", "args": ["rapport.docx"]}}
- "mets le volume à 75%" → {{"level": 75}}
- "cherche le dossier films et ouvre le" → FILE_OPEN {{"path": "films", "target_type": "directory"}}
- "va sur youtube et cherche lofi" → BROWSER_GO_TO_SITE {{"site": "youtube", "query": "lofi"}}
"""

    def _parse_json_response(self, raw_json: str, original_command: str) -> dict:
        clean = re.sub(r"```(?:json)?", "", raw_json).strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"JSON invalide : {e}  Brut: {raw_json}")
            return self._unknown(original_command, f"JSON invalide : {e}")

        intent          = data.get("intent", "UNKNOWN")
        params          = data.get("params", {})
        confidence      = float(data.get("confidence", 0.5))
        response_message = str(data.get("response_message", "")).strip()

        if intent not in INTENTS:
            logger.warning(f"Intent inconnu : '{intent}' — fallback UNKNOWN")
            intent = "UNKNOWN"

        return {
            "intent":           intent,
            "params":           params if isinstance(params, dict) else {},
            "confidence":       min(max(confidence, 0.0), 1.0),
            "response_message": response_message,
            "raw":              original_command,
        }

    # ──────────────────────────────────────────────────────────────────────────
    #  FALLBACK KEYWORDS — utilisé uniquement si Groq indisponible
    # ──────────────────────────────────────────────────────────────────────────

    def _fallback_keywords(self, command: str) -> dict:
        """Fallback par mots-clés — utilisé SEULEMENT si Groq est hors ligne."""
        lower = self._normalize_text(command.lower())

        # Heure / date
        if any(k in lower for k in [
            "heure", "heure est", "time is", "what time", "quelle heure",
            "date", "quel jour", "aujourd'hui",
        ]):
            return {"intent": "SYSTEM_TIME", "params": {}, "confidence": 0.95}

        # Système
        if any(k in lower for k in ["eteins", "eteinds", "shutdown", "poweroff", "coupe le pc", "arrête le pc"]):
            return {"intent": "SYSTEM_SHUTDOWN", "params": {"delay_seconds": 10}, "confidence": 0.8}
        if any(k in lower for k in ["redémarre", "redemarre", "restart", "reboot"]):
            return {"intent": "SYSTEM_RESTART", "params": {"delay_seconds": 10}, "confidence": 0.8}
        if any(k in lower for k in ["veille", "sleep", "hiberne", "hibernate"]):
            return {"intent": "SYSTEM_SLEEP", "params": {}, "confidence": 0.75}
        if any(k in lower for k in ["verrouille", "lock"]):
            return {"intent": "SYSTEM_LOCK", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["infos systeme", "info systeme", "system info", "etat du pc", "état du pc"]):
            return {"intent": "SYSTEM_INFO", "params": {}, "confidence": 0.8}

        # Audio — ordre important : volume AVANT play pour éviter la collision
        if "volume" in lower:
            if any(k in lower for k in ["monte", "augmente", "hausse", "up", "plus"]):
                return {"intent": "AUDIO_VOLUME_UP", "params": {"step": self._extract_number(lower, 10)}, "confidence": 0.85}
            if any(k in lower for k in ["baisse", "diminue", "descends", "down", "moins"]):
                return {"intent": "AUDIO_VOLUME_DOWN", "params": {"step": self._extract_number(lower, 10)}, "confidence": 0.85}
            return {"intent": "AUDIO_VOLUME_SET", "params": {"level": self._extract_number(lower, 50)}, "confidence": 0.8}
        if any(k in lower for k in ["mute", "coupe le son", "silence"]):
            return {"intent": "AUDIO_MUTE", "params": {}, "confidence": 0.85}
        if any(k in lower for k in ["joue", "play", "ecoute", "musique"]):
            query = self._extract_after(lower, ["joue ", "play ", "ecoute ", "lance "])
            return {"intent": "AUDIO_PLAY", "params": {"query": query}, "confidence": 0.75}

        # Applications
        if any(k in lower for k in ["ouvre", "lance", "démarre", "demarre", "mets", "start"]):
            app_name = self._extract_after(lower, ["ouvre ", "lance ", "demarre ", "démarre ", "mets ", "start "])
            if app_name:
                return {"intent": "APP_OPEN", "params": {"app_name": app_name, "args": []}, "confidence": 0.75}
        if any(k in lower for k in ["ferme", "referme", "close", "quitte", "quit"]):
            lower_cmd = lower
            # "ferme ça/là/cette fenêtre" -> WINDOW_CLOSE
            if any(k in lower_cmd for k in ["là", "la", "ça", "ca", "cette", "fenêtre", "fenetre", "ici"]):
                return {"intent": "WINDOW_CLOSE", "params": {"query": ""}, "confidence": 0.85}
            app_name = self._extract_after(lower_cmd, ["ferme ", "referme ", "close ", "quitte ", "quit "])
            return {"intent": "APP_CLOSE", "params": {"app_name": app_name}, "confidence": 0.75}

        # Fichiers
        if any(k in lower for k in ["cherche", "trouve", "search"]):
            query = self._extract_after(lower, ["cherche ", "trouve ", "search "])
            return {"intent": "FILE_SEARCH", "params": {"query": query}, "confidence": 0.7}

        # Navigateur
        if any(k in lower for k in ["recherche", "google", "web", "internet"]):
            query = self._extract_after(lower, ["recherche ", "google ", "cherche sur internet "])
            return {"intent": "BROWSER_SEARCH", "params": {"query": query}, "confidence": 0.75}
        
        # Salutations
        if any(k in lower for k in [
            "bonjour", "salut", "hello", "bonsoir", "coucou", "hey", "hi",
            "good morning", "good evening",
        ]):
            return {"intent": "GREETING", "params": {}, "confidence": 0.99}

        # Aide + identité
        if any(k in lower for k in [
            "aide", "help", "que peux-tu", "que sais-tu",
            "qui es-tu", "ton nom", "tu t'appelles", "what's your name",
            "que sais", "tes fonctionnalit", "tu peux faire", "tes capacit",
            "présente-toi", "parle-moi de toi", "tu es quoi", "what can you",
        ]):
            return {"intent": "HELP", "params": {}, "confidence": 0.9}

        return self._unknown(command, "Aucun mot-clé reconnu (Groq hors ligne).")

    # ──────────────────────────────────────────────────────────────────────────
    #  SEMANTIC GUARD — garde-fou minimal, ne court-circuite plus Groq
    # ──────────────────────────────────────────────────────────────────────────

    def _semantic_guard(self, command: str, result: dict) -> dict:
        """
        Garde-fou sémantique MINIMAL — corrige uniquement les erreurs critiques
        que Groq ferait en mode fallback ou avec très faible confiance.

        IMPORTANT : ce guard NE doit PAS réimposer des règles à mots-clés
        par-dessus une réponse Groq à haute confiance (>= 0.85).
        Si Groq est confiant, on lui fait confiance.
        """
        out        = dict(result or {})
        lower      = command.lower().strip()
        normalized = self._normalize_text(lower)
        intent     = out.get("intent", "UNKNOWN")
        confidence = float(out.get("confidence", 0.0))

        # Si Groq est confiant (>= 0.85), on ne touche à rien.
        if confidence >= 0.85:
            return out

        # Correction critique seulement si confiance faible :
        # "volume" ne doit JAMAIS devenir AUDIO_PLAY
        if "volume" in normalized and intent == "AUDIO_PLAY":
            if any(k in normalized for k in ["monte", "augmente", "plus"]):
                out["intent"] = "AUDIO_VOLUME_UP"
                out["params"] = {"step": self._extract_number(lower, 10)}
            elif any(k in normalized for k in ["baisse", "diminue", "moins"]):
                out["intent"] = "AUDIO_VOLUME_DOWN"
                out["params"] = {"step": self._extract_number(lower, 10)}
            else:
                out["intent"] = "AUDIO_VOLUME_SET"
                out["params"] = {"level": self._extract_number(lower, 50)}
            out["confidence"] = 0.88

        return out

    # ──────────────────────────────────────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────────────────────────────────────

    def _unknown(self, command: str, reason: str = "") -> dict:
        return {
            "intent":           "UNKNOWN",
            "params":           {},
            "confidence":       0.0,
            "response_message": "Je n'ai pas bien saisi. Tu peux reformuler ?",
            "raw":              command,
            "reason":           reason,
        }

    @staticmethod
    def _normalize_text(text: str) -> str:
        no_accents = "".join(
            c for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )
        return re.sub(r"\s+", " ", no_accents).strip()

    @staticmethod
    def _extract_number(text: str, default: int = 50) -> int:
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else default

    @staticmethod
    def _extract_after(text: str, prefixes: list) -> str:
        for prefix in sorted(prefixes, key=len, reverse=True):
            if prefix in text:
                after = text.split(prefix, 1)[1].strip()
                if after:
                    return after
        return ""

    # ── Méthodes conservées pour compatibilité avec agent.py ─────────────────

    def _extract_target(self, command: str, keywords: list) -> str:
        return self._extract_after(command, keywords)

    def _postprocess_result(self, command: str, result: dict) -> dict:
        """Conservé pour compatibilité — ne fait plus rien d'actif."""
        return result

    def _extract_open_target_params(self, text: str, base_params: dict | None = None) -> dict:
        params = dict(base_params or {})
        lower = text.lower().strip()
        target_type = params.get("target_type", "any")
        if any(token in lower for token in ["dossier", "répertoire", "repertoire"]):
            target_type = "directory"
        elif any(token in lower for token in ["fichier", "document"]):
            target_type = "file"
        cleaned = re.sub(
            r"^(ouvre|ouvrir|open|lis|affiche)\s+(moi\s+)?(le|la|les)?\s*(fichier|dossier|document|répertoire|repertoire)?\s*",
            "", lower,
        ).strip()
        cleaned = cleaned.strip('"').strip("'")
        if cleaned:
            params["path"] = cleaned
        params["target_type"] = target_type
        return params

    def _extract_location_context(self, text: str) -> dict:
        params = {}
        drive_match = re.search(r"(?:disque|disk|lecteur|drive)\s+([a-z])\b", text, re.IGNORECASE)
        if drive_match:
            params["search_dirs"] = [f"{drive_match.group(1).upper()}:\\"]
            return params
        folder_match = re.search(
            r"(?:dans|sur|sous)\s+(?:le|la|les)?\s*"
            r"(documents?|desktop|bureau|downloads|t[ée]l[ée]chargements|music|musique|pictures|images|videos?)",
            text, re.IGNORECASE,
        )
        if folder_match:
            params["search_dirs"] = [folder_match.group(1)]
        return params

    @staticmethod
    def _extract_wifi_connect_params(text: str) -> dict:
        raw = text.strip()
        quoted = re.search(r"wifi\s+[\"']([^\"']+)[\"']", raw, re.IGNORECASE)
        ssid = quoted.group(1).strip() if quoted else ""
        if not ssid:
            m = re.search(r"(?:connecte(?: toi)? au wifi|wifi connect)\s+(.+)$", raw, re.IGNORECASE)
            if m:
                ssid = m.group(1).strip(" \"'")
        pwd = ""
        m_pwd = re.search(r"(?:mot de passe|password|mdp)\s*[:=]?\s*([\S]+)$", raw, re.IGNORECASE)
        if m_pwd:
            pwd = m_pwd.group(1).strip("\"'")
        params = {}
        if ssid:
            params["ssid"] = ssid
        if pwd:
            params["password"] = pwd
        return params