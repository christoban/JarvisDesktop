"""
command_parser.py — Parseur IA conversationnel JARVIS
Transforme n'importe quelle phrase en intention structurée via Groq (LLaMA 3.3 70B).

DIFFÉRENCE CLÉ vs l'ancienne version :
  - Groq reçoit UNE VRAIE CONVERSATION (messages user/assistant alternés)
    au lieu d'un dump de contexte dans un message système séparé.
  - Le system prompt définit JARVIS comme un assistant conversationnel.
  - Les few-shot examples montrent des échanges naturels.
  - Groq génère AUSSI le message de réponse naturelle de Jarvis (response_message).

SEMAINE 2 — CORRECTIONS :
  [B8]  Ajout des intents MUSIC_* dans le catalogue INTENTS.
  [B9]  Fallback keywords étendu — 25+ nouveaux patterns reconnus.
  [Fix] _postprocess_result() réactivé.
  [Fix] _semantic_guard étendu.

CORRECTION 2 (audit semaines 1-5) :
  _call_groq_ai() : restauration des messages "assistant" dans l'historique.
  Avant : `if not content or role != "user": continue`  ← filtrait tout sauf user
  Après : user ET assistant sont envoyés, avec troncature des réponses longues.
  Impact : Groq comprend maintenant les références contextuelles ("le même",
  "celui-là", "encore ça") car il voit les réponses précédentes de Jarvis.

CORRECTION 3 (audit semaines 1-5) :
  _postprocess_result() était défini mais jamais appelé.
  Maintenant appelé à la fin de parse() et parse_with_context() après
  _semantic_guard(), en dernière ligne de défense.
"""

import json
import re
import time
from typing import Optional
import unicodedata
from config.logger import get_logger
from config.settings import (
    GROQ_API_KEY,
    GROQ_MODEL_NAME,
    USE_TOOL_CALLS,
)

logger = get_logger(__name__)

# Longueur max d'un message assistant injecté dans l'historique Groq.
# Les réponses longues (listes de fichiers, rapport système) sont tronquées
# pour ne pas polluer le contexte JSON strict du parser.
_MAX_ASSISTANT_MSG_LEN = 200


# ══════════════════════════════════════════════════════════════════════════════
#  CONVERSION INTENTS → TOOL SCHEMAS (TONY STARK V2)
# ══════════════════════════════════════════════════════════════════════════════

def convert_intents_to_tools(enabled: bool = None) -> list:
    """
    Convertit le catalogue INTENTS en tool schemas JSON pour Groq.
    
    TONY STARK V2 : Support des tool_calls natifs de Groq pour une
    meilleure robustesse et précision.
    
    Si enabled=False ou USE_TOOL_CALLS=False, retourne [] (pas de tools).
    LIMITATION : Groq accepte max 128 outils. On ne génère que les 50 
    outils non-système pour économiser les tokens (énorme problème).
    """
    if enabled is None:
        enabled = USE_TOOL_CALLS
    
    if not enabled:
        return []
    
    tools = []
    
    critical_intents = {
        "APP_OPEN", "APP_CLOSE", "BROWSER_SEARCH", "BROWSER_OPEN",
        "FILE_OPEN", "FILE_SEARCH", "FOLDER_LIST",
        "AUDIO_VOLUME_SET", "AUDIO_MUTE",
        "MUSIC_PLAY", "MUSIC_PAUSE", "MUSIC_NEXT", "MUSIC_PLAYLIST_PLAY",
        "SYSTEM_SHUTDOWN", "SYSTEM_INFO",
        "WINDOW_CLOSE", "SCREEN_CAPTURE", "SCREEN_BRIGHTNESS",
        "MULTI_ACTION", "KNOWLEDGE_QA", "HELP",
    }
    
    for intent_name, intent_config in INTENTS.items():
        if intent_name == "UNKNOWN":
            continue
        
        # Ne générer les tools que pour les intents critiques
        # (+ économiser énormément de tokens)
        if intent_name not in critical_intents:
            continue
        
        tool = {
            "type": "function",
            "function": {
                "name": intent_name,
                "description": intent_config.get("desc", ""),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
        
        # Construire les propriétés depuis les params
        params = intent_config.get("params", {})
        for param_name, param_type_str in params.items():
            param_type_str_lower = str(param_type_str).lower().strip()
            is_optional = "optionnel" in param_type_str_lower or "optional" in param_type_str_lower
            
            # Déterminer le type JSON Schema
            json_type = "string"  # Default
            if "int" in param_type_str_lower:
                json_type = "integer"
            elif "float" in param_type_str_lower or "number" in param_type_str_lower:
                json_type = "number"
            elif "bool" in param_type_str_lower:
                json_type = "boolean"
            elif "list" in param_type_str_lower or "array" in param_type_str_lower:
                json_type = "array"
            
            prop = {
                "type": json_type,
                "description": param_name
            }
            
            # Pour les arrays, préciser le type des items
            if json_type == "array":
                if "int" in param_type_str_lower:
                    prop["items"] = {"type": "integer"}
                else:
                    prop["items"] = {"type": "string"}
            
            tool["function"]["parameters"]["properties"][param_name] = prop
            
            # Ajouter aux required si pas optionnel
            if not is_optional:
                tool["function"]["parameters"]["required"].append(param_name)
        
        tools.append(tool)
    
    logger.info(f"Generated {len(tools)} critical tools for Groq (max {len(critical_intents)})")
    return tools


# ══════════════════════════════════════════════════════════════════════════════
#  CATALOGUE DES INTENTIONS
# ══════════════════════════════════════════════════════════════════════════════

INTENTS = {
    # ── Système ───────────────────────────────────────────────────────────────
    "SYSTEM_SHUTDOWN":        {"desc": "Éteindre l'ordinateur",              "params": {"delay_seconds": "int"}},
    "SYSTEM_RESTART":         {"desc": "Redémarrer l'ordinateur",            "params": {"delay_seconds": "int"}},
    "SYSTEM_SLEEP":           {"desc": "Mettre en veille",                   "params": {}},
    "SYSTEM_HIBERNATE":       {"desc": "Mettre en hibernation",              "params": {}},
    "SYSTEM_LOCK":            {"desc": "Verrouiller l'écran",                "params": {}},
    "SYSTEM_UNLOCK":          {"desc": "Déverrouiller l'écran",              "params": {}},
    "SYSTEM_LOGOUT":          {"desc": "Déconnecter l'utilisateur",          "params": {}},
    "SYSTEM_TIME":            {"desc": "Donner l'heure et la date",          "params": {"timezone": "str optionnel"}},
    "SYSTEM_INFO":            {"desc": "Infos CPU, RAM, uptime",             "params": {}},
    "SYSTEM_DISK":            {"desc": "Infos disque et stockage",           "params": {}},
    "SYSTEM_PROCESSES":       {"desc": "Lister les processus",               "params": {"sort_by": "str"}},
    "SYSTEM_KILL_PROCESS":    {"desc": "Fermer un processus",                "params": {"target": "str"}},
    "SYSTEM_NETWORK":         {"desc": "Infos réseau et IP",                 "params": {}},
    "SYSTEM_TEMPERATURE":     {"desc": "Températures des composants",        "params": {}},
    "SYSTEM_FULL_REPORT":     {"desc": "Rapport système complet",            "params": {}},
    "SYSTEM_TASK_MANAGER":    {"desc": "Ouvrir le gestionnaire des tâches",  "params": {}},
    "SYSTEM_CANCEL_SHUTDOWN": {"desc": "Annuler une extinction programmée",  "params": {}},
    "POWER_SLEEP":            {"desc": "Mettre le PC en veille",             "params": {}},
    "POWER_HIBERNATE":        {"desc": "Mettre le PC en hibernation",        "params": {}},
    "POWER_CANCEL":           {"desc": "Annuler extinction/redémarrage planifié", "params": {}},
    "POWER_STATE":            {"desc": "Afficher l'état d'alimentation",     "params": {}},
    "SCREEN_UNLOCK":          {"desc": "Déverrouiller l'écran",              "params": {"password": "str optionnel"}},
    "SCREEN_OFF":             {"desc": "Éteindre l'écran sans verrouiller",  "params": {}},
    "WAKE_ON_LAN":            {"desc": "Réveiller un PC par Wake-on-LAN",    "params": {"mac_address": "str"}},

    # ── Réseau ────────────────────────────────────────────────────────────────
    "WIFI_LIST":        {"desc": "Lister les réseaux Wi-Fi",                 "params": {}},
    "WIFI_CONNECT":     {"desc": "Se connecter à un réseau Wi-Fi",           "params": {"ssid": "str", "password": "str optionnel"}},
    "WIFI_DISCONNECT":  {"desc": "Se déconnecter du Wi-Fi",                  "params": {}},
    "WIFI_ENABLE":      {"desc": "Activer le Wi-Fi",                         "params": {}},
    "WIFI_DISABLE":     {"desc": "Désactiver le Wi-Fi",                      "params": {}},
    "BLUETOOTH_ENABLE": {"desc": "Activer Bluetooth",                        "params": {}},
    "BLUETOOTH_DISABLE":{"desc": "Désactiver Bluetooth",                     "params": {}},
    "BLUETOOTH_LIST":   {"desc": "Lister les appareils Bluetooth",           "params": {}},
    "NETWORK_INFO":     {"desc": "Afficher les informations réseau",         "params": {}},

    # ── Applications ──────────────────────────────────────────────────────────
    "APP_OPEN":         {"desc": "Ouvrir une application",                   "params": {"app_name": "str", "args": "list"}},
    "APP_CLOSE":        {"desc": "Fermer une application",                   "params": {"app_name": "str"}},
    "APP_RESTART":      {"desc": "Redémarrer une application",               "params": {"app_name": "str"}},
    "APP_CHECK":        {"desc": "Vérifier si une application est ouverte",  "params": {"app_name": "str"}},
    "APP_LIST_RUNNING": {"desc": "Lister les applications ouvertes",         "params": {}},
    "APP_LIST_KNOWN":   {"desc": "Lister les applications connues",          "params": {}},

    # ── Fichiers & Dossiers ───────────────────────────────────────────────────
    "FILE_SEARCH":         {"desc": "Rechercher un fichier par nom",         "params": {"query": "str", "search_dirs": "list optionnel"}},
    "FILE_SEARCH_TYPE":    {"desc": "Rechercher des fichiers par type",      "params": {"extension": "str"}},
    "FILE_SEARCH_CONTENT": {"desc": "Rechercher un mot dans les fichiers",   "params": {"keyword": "str"}},
    "FILE_OPEN":           {"desc": "Ouvrir un fichier",                     "params": {"path": "str", "target_type": "str optionnel"}},
    "FILE_CLOSE":          {"desc": "Fermer un fichier ouvert",              "params": {"path": "str"}},
    "FILE_COPY":           {"desc": "Copier un fichier",                     "params": {"src": "str", "dst": "str"}},
    "FILE_MOVE":           {"desc": "Déplacer un fichier",                   "params": {"src": "str", "dst": "str"}},
    "FILE_RENAME":         {"desc": "Renommer un fichier",                   "params": {"path": "str", "new_name": "str"}},
    "FILE_DELETE":         {"desc": "Supprimer un fichier",                  "params": {"path": "str"}},
    "FILE_INFO":           {"desc": "Informations sur un fichier",           "params": {"path": "str"}},
    "FOLDER_LIST":         {"desc": "Lister le contenu d'un dossier",        "params": {"path": "str"}},
    "FOLDER_CREATE":       {"desc": "Créer un dossier",                      "params": {"path": "str"}},
    "FILE_SEARCH_DATE":    {"desc": "Rechercher des fichiers par date",      "params": {"period": "str optionnel", "search_dirs": "list optionnel", "extension": "str optionnel"}},
    "FILE_SEARCH_SIZE":    {"desc": "Rechercher des fichiers par taille",    "params": {"min_size": "float", "max_size": "float optionnel", "unit": "str optionnel", "search_dirs": "list optionnel", "extension": "str optionnel"}},
    "FILE_SEARCH_ADVANCED": {"desc": "Recherche fichiers multi-critères",    "params": {"name": "str optionnel", "extension": "str optionnel", "period": "str optionnel", "min_size": "float optionnel", "max_size": "float optionnel", "size_unit": "str optionnel", "search_dirs": "list optionnel"}},
    "FILE_ORGANIZE":       {"desc": "Organiser automatiquement un dossier",  "params": {"folder": "str optionnel", "dry_run": "bool optionnel"}},
    "FILE_BULK_RENAME":    {"desc": "Renommer des fichiers en masse",        "params": {"folder": "str", "pattern": "str optionnel", "replacement": "str optionnel", "prefix": "str optionnel", "suffix": "str optionnel", "dry_run": "bool optionnel"}},
    "FILE_FIND_DUPLICATES": {"desc": "Trouver les doublons",                 "params": {"search_dirs": "list optionnel", "extension": "str optionnel", "min_size": "int optionnel"}},
    "FILE_DELETE_DUPLICATES": {"desc": "Supprimer les doublons",             "params": {"search_dirs": "list optionnel", "strategy": "str optionnel", "dry_run": "bool optionnel"}},
    "FILE_CLEAN":          {"desc": "Nettoyer les dossiers vides",           "params": {"folder": "str optionnel", "dry_run": "bool optionnel"}},
    "FILE_CLASSIFY":       {"desc": "Classer intelligemment des documents",  "params": {"search_dirs": "list optionnel", "move_files": "bool optionnel"}},
    "FILE_PREPARE_APPLICATION": {"desc": "Préparer un dossier de candidature (CV, lettre, ZIP)", "params": {"search_dirs": "list optionnel", "output_dir": "str optionnel", "dry_run": "bool optionnel"}},
    "FILE_SYNC_DRIVE":     {"desc": "Synchroniser un dossier vers Google Drive", "params": {"source": "str", "drive_folder": "str optionnel", "mode": "copy|mirror", "dry_run": "bool optionnel"}},
    "WINDOW_CLOSE":        {"desc": "Fermer une fenêtre ouverte",            "params": {"query": "str"}},

    # ── Navigateur ────────────────────────────────────────────────────────────
    "BROWSER_OPEN":           {"desc": "Ouvrir le navigateur",               "params": {"url": "str optionnel"}},
    "BROWSER_CLOSE":          {"desc": "Fermer le navigateur",               "params": {}},
    "BROWSER_URL":            {"desc": "Ouvrir une URL",                     "params": {"url": "str"}},
    "BROWSER_NEW_TAB":        {"desc": "Ouvrir un nouvel onglet",            "params": {"url": "str optionnel"}},
    "BROWSER_BACK":           {"desc": "Page précédente",                    "params": {}},
    "BROWSER_FORWARD":        {"desc": "Page suivante",                      "params": {}},
    "BROWSER_RELOAD":         {"desc": "Recharger la page",                  "params": {}},
    "BROWSER_CLOSE_TAB":      {"desc": "Fermer un onglet",                   "params": {"index": "int optionnel"}},
    "BROWSER_SEARCH":         {"desc": "Rechercher sur le web",              "params": {"query": "str", "engine": "str optionnel"}},
    "BROWSER_SEARCH_YOUTUBE": {"desc": "Chercher sur YouTube",               "params": {"query": "str"}},
    "BROWSER_SEARCH_GITHUB":  {"desc": "Chercher sur GitHub",                "params": {"query": "str"}},
    "BROWSER_OPEN_RESULT":    {"desc": "Ouvrir un résultat de recherche",    "params": {"rank": "int"}},
    "BROWSER_LIST_RESULTS":   {"desc": "Lister les résultats de recherche",  "params": {}},
    "BROWSER_GO_TO_SITE":     {"desc": "Naviguer vers un site connu",        "params": {"site": "str", "query": "str optionnel"}},
    "BROWSER_NAVIGATE":       {"desc": "Naviguer vers une URL",              "params": {"url": "str"}},
    "BROWSER_READ":           {"desc": "Lire le contenu de la page",         "params": {}},
    "BROWSER_PAGE_INFO":      {"desc": "Titre et URL de la page active",     "params": {}},
    "BROWSER_EXTRACT_LINKS":  {"desc": "Extraire les liens de la page",      "params": {}},
    "BROWSER_SUMMARIZE":      {"desc": "Résumer la page active via IA",      "params": {}},
    "BROWSER_SCROLL":         {"desc": "Scroller la page",                   "params": {"direction": "str"}},
    "BROWSER_CLICK_TEXT":     {"desc": "Cliquer sur un élément",             "params": {"text": "str"}},
    "BROWSER_FILL_FIELD":     {"desc": "Remplir un champ de formulaire",     "params": {"selector": "str", "value": "str"}},
    "BROWSER_TYPE":           {"desc": "Taper du texte dans la page",        "params": {"text": "str"}},
    "BROWSER_DOWNLOAD":       {"desc": "Télécharger un fichier",             "params": {"url": "str optionnel"}},
    "BROWSER_LIST_TABS":      {"desc": "Lister les onglets ouverts",         "params": {}},
    "BROWSER_SWITCH_TAB":     {"desc": "Basculer sur un onglet",             "params": {"index": "int optionnel"}},
    "BROWSER_FIND_AND_OPEN":  {"desc": "Trouver le meilleur résultat et l'ouvrir", "params": {"query": "str"}},
    "BROWSER_CONTEXT":        {"desc": "État actuel du navigateur",          "params": {}},
    "BROWSER_SAVE_SESSION":   {"desc": "Sauvegarder les cookies/session d'un site", "params": {"site": "str"}},
    "BROWSER_CHECK_LOGIN":    {"desc": "Vérifier l'état de connexion à un site",    "params": {"site": "str"}},
    "BROWSER_EXTRACT_SUMMARY":{"desc": "Résumé structuré du contenu de la page",    "params": {}},
    "BROWSER_COMPOSE_EMAIL":  {"desc": "Composer et envoyer un email",              "params": {"to": "str optionnel", "subject": "str optionnel", "body": "str optionnel"}},
    "TELEGRAM_SEND":          {"desc": "Envoyer un message via Telegram",           "params": {"to": "str", "message": "str"}},
    "BROWSER_MULTISTEP":      {"desc": "Exécuter plusieurs commandes en séquence",  "params": {"steps": "list[str]"}},

    # ── Audio ─────────────────────────────────────────────────────────────────
    "AUDIO_VOLUME_UP":   {"desc": "Monter le volume",                        "params": {"step": "int"}},
    "AUDIO_VOLUME_DOWN": {"desc": "Baisser le volume",                       "params": {"step": "int"}},
    "AUDIO_VOLUME_SET":  {"desc": "Définir le volume",                       "params": {"level": "int 0-100"}},
    "AUDIO_MUTE":        {"desc": "Couper/rétablir le son",                  "params": {}},
    "AUDIO_PLAY":        {"desc": "Jouer une musique locale",                "params": {"query": "str"}},

    # ── [B8] Musique ──────────────────────────────────────────────────────────
    "MUSIC_PLAY":            {"desc": "Jouer une musique, chanson ou artiste", "params": {"query": "str, titre ou artiste ou playlist"}},
    "MUSIC_PAUSE":           {"desc": "Mettre la musique en pause",           "params": {}},
    "MUSIC_RESUME":          {"desc": "Reprendre la lecture musicale",        "params": {}},
    "MUSIC_STOP":            {"desc": "Arrêter complètement la musique",      "params": {}},
    "MUSIC_NEXT":            {"desc": "Passer à la musique suivante",         "params": {}},
    "MUSIC_PREV":            {"desc": "Revenir à la musique précédente",      "params": {}},
    "MUSIC_VOLUME":          {"desc": "Régler le volume de la musique",       "params": {"level": "int 0-100"}},
    "MUSIC_SHUFFLE":         {"desc": "Activer/désactiver lecture aléatoire", "params": {}},
    "MUSIC_REPEAT":          {"desc": "Activer/désactiver répétition",        "params": {}},
    "MUSIC_CURRENT":         {"desc": "Quelle musique joue en ce moment",    "params": {}},
    "MUSIC_PLAYLIST_CREATE": {"desc": "Créer une playlist",                   "params": {"name": "str"}},
    "MUSIC_PLAYLIST_PLAY":   {"desc": "Jouer une playlist",                   "params": {"name": "str"}},
    "MUSIC_PLAYLIST_LIST":   {"desc": "Lister les playlists disponibles",     "params": {}},
    "MUSIC_PLAYLIST_DELETE": {"desc": "Supprimer une playlist",               "params": {"name": "str"}},
    "MUSIC_PLAYLIST_CLEAR":  {"desc": "Vider une playlist (garder la playlist, enlever les chansons)", "params": {"name": "str"}},
    "MUSIC_PLAYLIST_REMOVE_SONG": {"desc": "Enlever une chanson d'une playlist", "params": {"name": "str", "query": "str"}},
    "MUSIC_PLAYLIST_RENAME": {"desc": "Renommer une playlist",                "params": {"old_name": "str", "new_name": "str"}},
    "MUSIC_PLAYLIST_DUPLICATE": {"desc": "Dupliquer une playlist",            "params": {"source": "str", "target": "str"}},
    "MUSIC_PLAYLIST_EXPORT": {"desc": "Exporter une playlist (m3u/json)",     "params": {"name": "str", "format": "str optionnel", "path": "str optionnel"}},
    "MUSIC_PLAYLIST_IMPORT": {"desc": "Importer une playlist depuis fichier", "params": {"name": "str optionnel", "path": "str", "mode": "replace|append optionnel"}},
    "MUSIC_PLAYLIST_MERGE": {"desc": "Fusionner deux playlists",               "params": {"source": "str", "target": "str", "output": "str optionnel"}},
    "MUSIC_PLAYLIST_MOVE_SONG": {"desc": "Déplacer une chanson dans une playlist", "params": {"name": "str", "query": "str optionnel", "from_index": "int optionnel", "to_index": "int"}},
    "MUSIC_QUEUE_ADD": {"desc": "Ajouter une chanson à la file d'attente",     "params": {"query": "str"}},
    "MUSIC_QUEUE_ADD_PLAYLIST": {"desc": "Ajouter une playlist à la file d'attente", "params": {"name": "str"}},
    "MUSIC_QUEUE_LIST": {"desc": "Lister la file d'attente",                    "params": {}},
    "MUSIC_QUEUE_CLEAR": {"desc": "Vider la file d'attente",                    "params": {}},
    "MUSIC_QUEUE_PLAY": {"desc": "Lire la file d'attente",                       "params": {}},
    "MUSIC_LIBRARY_SCAN":         {"desc": "Scanner la bibliothèque musicale",              "params": {"path": "str optionnel"}},
    "MUSIC_PLAYLIST_ADD_FOLDER":  {"desc": "Ajouter un dossier entier à une playlist",        "params": {"name": "str", "folder": "str optionnel"}},
    "MUSIC_PLAYLIST_ADD_SONG":    {
        "desc": "Ajouter un fichier audio spécifique à une playlist",
        "params": {
            "name":   "str (nom de la playlist)",
            "query":  "str (nom du fichier ou de la chanson)",
            "song":   "str (nom du fichier, identique à query)",
            "folder": "str optionnel (localisation : bureau, téléchargements, documents...)",
        },
    },

    # ── Documents ─────────────────────────────────────────────────────────────
    "DOC_READ":        {"desc": "Lire un document Word ou PDF",              "params": {"path": "str"}},
    "DOC_SUMMARIZE":   {"desc": "Résumer un document",                       "params": {"path": "str"}},
    "DOC_SEARCH_WORD": {"desc": "Chercher un mot dans un document",          "params": {"path": "str", "keyword": "str"}},
    "DOC_QA":          {"desc": "Poser une question sur un document",        "params": {"path": "str", "question": "str"}},
    # ── Word (Semaine 10) ─────────────────────────────────────────────────────
    "WORD_CREATE":     {
        "desc": "Créer un document Word structuré",
        "params": {"title": "str", "sections": "list optionnel", "content": "str optionnel",
                   "filename": "str optionnel", "style": "str optionnel"}
    },
    "WORD_EDIT":       {
        "desc": "Modifier un document Word existant (ajouter ou remplacer texte)",
        "params": {"path": "str", "action": "str (append|replace|add_heading)",
                   "content": "str optionnel", "search": "str optionnel", "replace": "str optionnel"}
    },
    "WORD_EXPORT_PDF": {"desc": "Exporter un .docx en PDF",                 "params": {"path": "str"}},
    "CV_CREATE":       {
        "desc": "Créer un CV professionnel complet au format Word",
        "params": {"name": "str", "title": "str optionnel", "email": "str optionnel",
                   "phone": "str optionnel", "summary": "str optionnel",
                   "experience": "list optionnel", "education": "list optionnel",
                   "skills": "dict optionnel", "languages": "list optionnel"}
    },
    "REPORT_CREATE":   {
        "desc": "Créer un rapport Word professionnel",
        "params": {"title": "str", "content": "str ou dict", "filename": "str optionnel"}
    },
    # ── Excel (Semaine 10) ────────────────────────────────────────────────────
    "EXCEL_CREATE":    {
        "desc": "Créer un fichier Excel avec données",
        "params": {"title": "str", "headers": "list optionnel", "rows": "list optionnel",
                   "sheets": "list optionnel", "filename": "str optionnel"}
    },
    "EXCEL_READ":      {"desc": "Lire un fichier Excel",                     "params": {"path": "str", "sheet": "str optionnel"}},
    "EXCEL_REPORT":    {
        "desc": "Générer un rapport Excel depuis des données",
        "params": {"title": "str", "data": "list", "filename": "str optionnel"}
    },
    # ── PDF (Semaine 10) ──────────────────────────────────────────────────────
    "PDF_EXTRACT":     {"desc": "Extraire texte ou pages d'un PDF",          "params": {"path": "str", "pages": "str optionnel", "mode": "str optionnel"}},
    "PDF_MERGE":       {"desc": "Fusionner plusieurs PDF en un seul",        "params": {"paths": "list", "output": "str optionnel"}},
    "PDF_SPLIT":       {"desc": "Découper un PDF",                           "params": {"path": "str", "split_at": "int ou list optionnel"}},
    "PDF_SEARCH":      {"desc": "Rechercher un mot dans un PDF",             "params": {"path": "str", "keyword": "str"}},
    "PDF_INFO":        {"desc": "Obtenir les informations/métadonnées d'un PDF", "params": {"path": "str"}},

    # ── Écran ─────────────────────────────────────────────────────────────────
    "SCREEN_CAPTURE":      {"desc": "Capture d'écran",                       "params": {}},
    "SCREENSHOT_TO_PHONE": {"desc": "Envoyer une capture au téléphone",      "params": {}},
    "SCREEN_BRIGHTNESS":   {"desc": "Régler la luminosité",                  "params": {"level": "int 0-100"}},
    "SCREEN_INFO":         {"desc": "Infos sur l'écran (résolution, etc.)",  "params": {}},
    "SCREEN_RECORD":       {"desc": "Enregistrer l'écran",                   "params": {}},

    # ── Vision (Semaine 13) ────────────────────────────────────────────────────
    "VISION_READ_SCREEN":    {"desc": "Lire le texte à l'écran (OCR)",          "params": {}},
    "VISION_CLICK_TEXT":     {"desc": "Cliquer sur un élément par son texte",    "params": {"text": "str", "fuzzy": "bool optionnel"}},
    "VISION_SUMMARIZE":      {"desc": "Résumer le contenu de l'écran",          "params": {}},
    "VISION_FIND_BUTTON":    {"desc": "Trouver un bouton à l'écran",             "params": {"button": "str"}},
    "VISION_EXTRACT_LINKS":  {"desc": "Extraire les liens de l'écran",          "params": {}},

    # ── Historique / Macros ───────────────────────────────────────────────────
    "REPEAT_LAST":    {"desc": "Répéter la dernière commande",               "params": {}},
    "HISTORY_SHOW":   {"desc": "Afficher l'historique des commandes",        "params": {"count": "int optionnel"}},
    "HISTORY_CLEAR":  {"desc": "Effacer l'historique",                       "params": {}},
    "HISTORY_SEARCH": {"desc": "Chercher dans l'historique",                 "params": {"keyword": "str"}},
    "MACRO_RUN":      {"desc": "Lancer une macro nommée",                    "params": {"name": "str"}},
    "MACRO_LIST":     {"desc": "Lister les macros disponibles",              "params": {}},
    "MACRO_SAVE":     {"desc": "Créer/sauvegarder une macro",                "params": {"name": "str", "commands": "list"}},
    "MACRO_DELETE":   {"desc": "Supprimer une macro",                        "params": {"name": "str"}},

    # ── Workflows (Semaine 12) ──────────────────────────────────────────────────
    "WORKFLOW_RUN":        {"desc": "Exécuter un workflow automatisé multi-apps", "params": {"name": "str", "context": "dict optionnel"}},
    "WORKFLOW_LIST":      {"desc": "Lister les workflows disponibles",         "params": {}},
    "WORKFLOW_REGISTER":  {"desc": "Créer un nouveau workflow",               "params": {"name": "str", "steps": "list", "description": "str optionnel"}},

    # ── Macro Recording (Semaine 12) ──────────────────────────────────────────
    "RECORD_START":  {"desc": "Démarrer l'enregistrement d'une macro",    "params": {"name": "str"}},
    "RECORD_STOP":   {"desc": "Arrêter l'enregistrement et sauvegarder",  "params": {"name": "str optionnel", "description": "str optionnel"}},

    "GREETING":     {"desc": "Salutation ou message d'accueil",              "params": {}},
    "MEMORY_SHOW":  {"desc": "Afficher ce dont Jarvis se souvient",          "params": {}},
    "PREFERENCE_SET": {
        "desc": "Mémoriser une préférence utilisateur (playlist de travail, app favorite, volume préféré, etc.)",
        "params": {"label": "str (ex: travail, codage, détente)", "value": "str (valeur associée)", "category": "str optionnel (music, app, volume...)"},
    },
    "MULTI_ACTION":  {
        "desc": "Exécuter plusieurs actions en séquence dans une seule commande composée",
        "params": {
            "actions": "list de {intent: str, params: dict} — max 4 actions",
        },
    },
    "KNOWLEDGE_QA": {"desc": "Question de connaissance générale, réponse directe sans action système", "params": {}},
    "INCOMPLETE":   {"desc": "Commande incomplète — paramètre manquant",     "params": {"missing": "str", "suggested_intent": "str"}},

    # ── Email (Outlook) ────────────────────────────────────────────────────────
    "EMAIL_INBOX":         {"desc": "Lire la boîte de réception",             "params": {"limit": "int optionnel", "unread_only": "bool optionnel"}},
    "EMAIL_SEND":          {"desc": "Envoyer un email",                      "params": {"to": "str", "subject": "str optionnel", "body": "str optionnel", "cc": "str optionnel"}},
    "EMAIL_REPLY":         {"desc": "Répondre à un email",                   "params": {"email_id": "str", "body": "str optionnel", "to_all": "bool optionnel"}},
    "EMAIL_FORWARD":       {"desc": "Transférer un email",                   "params": {"email_id": "str", "to": "str", "body": "str optionnel"}},
    "EMAIL_SEARCH":         {"desc": "Rechercher dans les emails",           "params": {"query": "str", "folder": "str optionnel"}},
    "EMAIL_MARK_READ":      {"desc": "Marquer un email comme lu",             "params": {"email_id": "str"}},
    "EMAIL_MARK_UNREAD":    {"desc": "Marquer un email comme non lu",        "params": {"email_id": "str"}},
    "EMAIL_DRAFT":         {"desc": "Créer un brouillon d'email",           "params": {"to": "str optionnel", "subject": "str optionnel", "body": "str optionnel"}},
    "EMAIL_ATTACH_FILE":    {"desc": "Joindre un fichier à un email",       "params": {"email_id": "str", "file_path": "str"}},
    "EMAIL_SUMMARY":        {"desc": "Résumé des emails non lus",             "params": {}},
    "EMAIL_IMPORTANT":     {"desc": "Afficher les emails importants",        "params": {"hours": "int optionnel"}},

    # ── Telegram ───────────────────────────────────────────────────────────────
    "TELEGRAM_SEND":        {"desc": "Envoyer un message via Telegram",     "params": {"message": "str", "chat_id": "str optionnel"}},
    "TELEGRAM_NOTIFY":      {"desc": "Envoyer une notification Telegram",   "params": {"title": "str optionnel", "message": "str"}},
    "TELEGRAM_ALERT":       {"desc": "Envoyer une alerte systeme Telegram", "params": {"alert_type": "str", "message": "str"}},
    "TELEGRAM_STATUS":      {"desc": "Verifier le statut du bot Telegram",    "params": {}},

    # ── Aide / Inconnu ────────────────────────────────────────────────────────
    "HELP":    {"desc": "Afficher l'aide et les commandes disponibles",      "params": {}},
    "UNKNOWN": {"desc": "Intention non reconnue",                            "params": {}},
}

# Alias pour compatibilité — utilisé dans les tests
INTENT_SCHEMA = INTENTS


# ══════════════════════════════════════════════════════════════════════════════
#  FORMATTED FEW-SHOT EXAMPLES HELPER
# ══════════════════════════════════════════════════════════════════════════════

def format_few_shot_examples(examples: list) -> list:
    """
    Convertit une liste de tuples (user_command, json_response) en messages
    format Groq pour les few-shot examples.
    
    Args:
        examples: liste de (user_command_str, json_response_str)
        
    Returns:
        Liste de dicts {"role": "user|assistant", "content": "..."}
    """
    messages = []
    for user_command, json_response in examples:
        messages.append({"role": "user", "content": user_command})
        messages.append({"role": "assistant", "content": json_response})
    return messages


# ══════════════════════════════════════════════════════════════════════════════
#  FEW-SHOT EXAMPLES
# ══════════════════════════════════════════════════════════════════════════════

FEW_SHOT_EXAMPLES = [
    ("ouvre chrome",            '{"intent":"APP_OPEN","params":{"app_name":"chrome"},"confidence":0.99}'),
    ("ferme notepad",          '{"intent":"APP_CLOSE","params":{"app_name":"notepad"},"confidence":0.95}'),
    ("cherche python",         '{"intent":"BROWSER_SEARCH","params":{"query":"python"},"confidence":0.99}'),
    ("nouvel onglet google",   '{"intent":"BROWSER_NEW_TAB","params":{"url":"google.com"},"confidence":0.95}'),
    ("volume 70",             '{"intent":"AUDIO_VOLUME_SET","params":{"level":70},"confidence":0.99}'),
    ("monte le son",           '{"intent":"AUDIO_VOLUME_UP","params":{"step":10},"confidence":0.95}'),
    ("cherche fichier test",   '{"intent":"FILE_SEARCH","params":{"query":"test"},"confidence":0.95}'),
    ("ouvre document.pdf",    '{"intent":"FILE_OPEN","params":{"path":"document.pdf"},"confidence":0.95}'),
    ("éteins PC",             '{"intent":"SYSTEM_SHUTDOWN","params":{},"confidence":0.99}'),
    ("informations système",   '{"intent":"SYSTEM_INFO","params":{},"confidence":0.95}'),
    ("joue Relax",            '{"intent":"MUSIC_PLAY","params":{"query":"Relax"},"confidence":0.95}'),
    ("pause musique",          '{"intent":"MUSIC_PAUSE","params":{},"confidence":0.98}'),
    ("ferme cette fenêtre",   '{"intent":"WINDOW_CLOSE","params":{"query":""},"confidence":0.95}'),
    ("capture d'écran",       '{"intent":"SCREEN_CAPTURE","params":{},"confidence":0.98}'),
    ("ouvre chrome et cherche météo puis mets le volume à 50", '{"intent":"MULTI_ACTION","params":{"actions":[{"intent":"APP_OPEN","params":{"app_name":"chrome"}},{"intent":"BROWSER_SEARCH","params":{"query":"météo"}},{"intent":"AUDIO_VOLUME_SET","params":{"level":50}}]},"confidence":0.90}'),
]


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND PARSER
# ══════════════════════════════════════════════════════════════════════════════

class CommandParser:
    """
    Parse une commande en langage naturel via Groq (LLaMA 3.3 70B).
    Fallback par mots-clés si Groq est hors ligne.
    """

    def __init__(self):
        self.client           = None
        self.ai_available     = False
        self._groq_cooldown_until = 0.0
        self._init_client()

    def _can_use_groq(self) -> bool:
        return self.ai_available and time.time() >= self._groq_cooldown_until

    def _set_groq_cooldown_from_error(self, error: Exception):
        msg = str(error)
        if "rate_limit_exceeded" not in msg and "Rate limit reached" not in msg:
            return
        wait_s = 600.0
        m = re.search(r"Please try again in\s+(?:(\d+)m)?([\d\.]+)s", msg)
        if m:
            minutes = float(m.group(1) or 0)
            seconds = float(m.group(2) or 0)
            wait_s  = (minutes * 60.0) + seconds
        self._groq_cooldown_until = time.time() + wait_s
        logger.warning(f"Groq cooldown parser ~{int(wait_s)}s (fallback actif).")

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
                    result = self._finalize_parse_result(
                        command=command,
                        result=self._call_groq_ai(command, history=[]),
                        source="groq",
                    )
                    logger.info(f"Intent: {result['intent']} (conf={result['confidence']:.2f}, src=groq)")
                    return result
                except Exception as e:
                    logger.warning(f"Groq tentative {attempt + 1} échouée : {e}")
                    self._set_groq_cooldown_from_error(e)
                    if "json_validate_failed" in str(e):
                        break
                    if time.time() < self._groq_cooldown_until:
                        break
                    if attempt < retries:
                        time.sleep(0.5 * (attempt + 1))

        return self._finalize_parse_result(
            command=command,
            result=self._fallback_keywords(command),
            source="fallback",
        )

    def parse_with_context(self, command: str, history: list = None, retries: int = 2) -> dict:
        """
        QUALITY-FIRST HYBRID PIPELINE pour données d'entraînement "premium"
        
        Stratégie:
          - Priorité: Comprendre correctement avec contexte (quitte à prendre du temps)
          - Groq + contexte historique = meilleure compréhension
          - Validation stricte: ≥0.95 = "premium training data"
          - < 0.95 = "uncertain, needs admin review"
        
        Cette approche maximise la QUALITÉ du dataset pour fine-tuning local
        plutôt que la rapidité de réponse.
        """
        from config.settings import LOCAL_LLM_ENABLED

        # ── NIVEAU 1 : GROQ AVEC CONTEXTE COMPLET (slow but sure) ─────────
        # C'est le cœur de votre stratégie: laisser Groq utiliser l'HISTORIQUE
        # pour vraiment comprendre l'intention, même si ça prend 1-2 secondes
        
        if self._can_use_groq():
            try:
                groq_result = self._call_groq_ai(command, history or [])
                result = self._finalize_parse_result(
                    command=command,
                    result=groq_result,
                    source="groq",
                )
                
                # VALIDATION STRICTE pour dataset
                confidence = result.get("confidence", 0)
                if confidence >= 0.95:
                    # ✅ EXCELLENT: données d'entraînement premium
                    logger.info(
                        f"[PREMIUM DATA] Groq {result['intent']} "
                        f"(conf={confidence:.2f}, source=groq+context)"
                    )
                    return result
                else:
                    # ⚠️ BON SIGNAL: confiance < 0.95 → tenter fallback pour améliorer
                    logger.info(
                        f"[QUALITY CHECK] Groq partiellement confiant "
                        f"({confidence:.2f}), tentant amélioration via fallbacks"
                    )
                    
                    # Essayer améliorer avec embeds ou local LLM
                    improved = self._try_improve_confidence(command, result, 0.95)
                    if improved:
                        logger.info(
                            f"[IMPROVED] Intent {improved['intent']} "
                            f"({improved['confidence']:.2f})"
                        )
                        return improved
                    
                    # Si pas d'amélioration, retourner Groq mais marquer "uncertain"
                    result["quality_flag"] = "uncertain_needs_review"
                    return result
            except Exception as e:
                logger.warning(f"Groq failed: {e}, falling back")
                self._set_groq_cooldown_from_error(e)

        # ── NIVEAU 2 : FALLBACK (si Groq indisponible) ────────────────────
        # Seulement utilisé pour rate-limit / downtime Groq
        logger.warning("[FALLBACK MODE] Groq unavailable, using fallback chain")
        
        # Embedding Router
        if LOCAL_LLM_ENABLED:
            try:
                from core.embedding_router import EmbeddingRouter
                if not hasattr(self, '_embed_router'):
                    self._embed_router = EmbeddingRouter()
                embed_result = self._embed_router.route(command)
                if embed_result and embed_result.get("confidence", 0) >= 0.85:
                    result = self._finalize_parse_result(
                        command=command,
                        result=embed_result,
                        source="embedding",
                    )
                    result["quality_flag"] = "fallback_embedding"
                    return result
            except Exception as e:
                logger.debug(f"EmbeddingRouter skipped: {e}")

        # Local LLM
        if LOCAL_LLM_ENABLED:
            try:
                from core.local_llm import LocalLLMParser
                from core.dataset_builder import load_examples
                if not hasattr(self, '_local_llm'):
                    self._local_llm = LocalLLMParser()
                if self._local_llm.is_available:
                    examples = load_examples(n=40, min_confidence=0.85)
                    local_result = self._local_llm.parse(command, examples)
                    if local_result.get("confidence", 0) >= 0.80:
                        result = self._finalize_parse_result(
                            command=command,
                            result=local_result,
                            source="local_llm",
                        )
                        result["quality_flag"] = "fallback_local_llm"
                        return result
            except Exception as e:
                logger.debug(f"LocalLLM skipped: {e}")

        # Fast rules (dernier recours)
        fallback_result = self._fallback_keywords(command)
        if fallback_result and fallback_result.get("confidence", 0) >= 0.85:
            result = self._finalize_parse_result(
                command=command,
                result=fallback_result,
                source="fast_rules",
            )
            result["quality_flag"] = "fallback_fast_rules"
            return result

        # Unknown
        result = self._finalize_parse_result(
            command=command,
            result=self._unknown(command, "No parser available"),
            source="fallback",
        )
        result["quality_flag"] = "unknown"
        return result
    
    def _try_improve_confidence(self, command: str, base_result: dict, target_conf: float):
        """
        Tente d'améliorer la confiance d'un résultat Groq < 0.95
        en utilisant des engines complémentaires.
        Retourne le résultat amélioré si possible, None sinon.
        """
        from config.settings import LOCAL_LLM_ENABLED
        
        base_intent = base_result.get("intent", "UNKNOWN")
        base_conf = base_result.get("confidence", 0)
        
        # Essayer embeddings
        if LOCAL_LLM_ENABLED:
            try:
                from core.embedding_router import EmbeddingRouter
                if not hasattr(self, '_embed_router'):
                    self._embed_router = EmbeddingRouter()
                embed_result = self._embed_router.route(command)
                
                if (embed_result and 
                    embed_result.get("intent") == base_intent and
                    embed_result.get("confidence", 0) > base_conf):
                    
                    logger.info(
                        f"[IMPROVE] Embedding confirmed {base_intent} "
                        f"({embed_result['confidence']:.2f} > {base_conf:.2f})"
                    )
                    
                    # Fusionner: prendre Groq mais boost confiance avec embedding
                    improved = base_result.copy()
                    improved["confidence"] = max(base_conf, embed_result.get("confidence", base_conf))
                    improved["secondary_source"] = "embedding_confirmed"
                    return improved
            except Exception as e:
                logger.debug(f"Improvement via embedding failed: {e}")
        
        return None

    def _finalize_parse_result(self, command: str, result: dict, source: str) -> dict:
        """
        Applique systématiquement les garde-fous finaux pour tous les moteurs
        de parsing (Groq, embedding, local llm, fast rules).
        """
        out = result if isinstance(result, dict) else self._unknown(command, "Invalid parser result")
        out = self._semantic_guard(command, out)
        out = self._postprocess_result(command, out)
        out["source"] = source
        return out

    # ──────────────────────────────────────────────────────────────────────────
    #  APPEL GROQ — [C2] CORRIGÉ
    # ──────────────────────────────────────────────────────────────────────────

    def _call_groq_ai(self, command: str, history: list = None) -> dict:
        """
        Construit les messages et appelle Groq.

        [C2] CORRECTION : L'historique envoie maintenant user ET assistant.
        Avant : seuls les messages role="user" étaient inclus.
              → Groq ne voyait jamais les réponses de Jarvis.
              → Les références contextuelles ("le même", "encore", "celui-là")
                étaient non résolues car Groq ignorait ce qui avait été dit.
        Après : user et assistant alternent correctement.
              → Les réponses assistant longues sont tronquées à
                _MAX_ASSISTANT_MSG_LEN caractères pour ne pas polluer
                le contexte JSON strict du parser d'intent.
        """
        memory_summary = ""
        history_to_use = []

        if history:
            for msg in history:
                if msg.get("role") == "system" and msg.get("memory"):
                    memory_summary = msg["memory"]
                else:
                    history_to_use.append(msg)
        else:
            history_to_use = []

        # ── Construction des messages ─────────────────────────────────────────
        messages = [{"role": "system", "content": self._build_system_prompt(memory_summary)}]

        # Few-shot examples (toujours en tête pour ancrer le format JSON)
        messages.extend(format_few_shot_examples(FEW_SHOT_EXAMPLES))

        # [C2] Historique : inclure user ET assistant (limité à 6 pour <6000 tokens)
        if history_to_use:
            for msg in history_to_use[-6:]:
                role    = msg.get("role", "user")
                content = str(msg.get("content", "")).strip()

                # Ignorer les messages vides ou les rôles inconnus
                if not content or role not in ("user", "assistant"):
                    continue

                # [C2] Tronquer les messages assistant trop longs.
                # Les réponses qui contiennent des listes (fichiers, processus,
                # rapport système) peuvent faire plusieurs centaines de caractères.
                # On garde seulement le début — suffisant pour que Groq comprenne
                # le contexte sans être perturbé dans sa génération JSON stricte.
                if role == "assistant" and len(content) > _MAX_ASSISTANT_MSG_LEN:
                    content = content[:_MAX_ASSISTANT_MSG_LEN] + "…"

                messages.append({"role": role, "content": content})

        # Commande courante — toujours en dernier
        messages.append({"role": "user", "content": command})

        # ── Appel API ─────────────────────────────────────────────────────────
        # LEVEL 3: Tools schemas via tool_schema.py
        from core.tool_schema import get_tool_schemas_for_groq
        tools = get_tool_schemas_for_groq()

        if len(tools) > 128:
            logger.warning(f"Trop de tools ({len(tools)}) pour Groq; utilisation d'un sous-ensemble critique")
            tools = tools[:120]

        response = self.client.chat.completions.create(
            model=GROQ_MODEL_NAME,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
        )

        msg = response.choices[0].message
        if msg.tool_calls:
            # Si Groq utilise un outil, on formate le résultat comme attendu par l'agent
            tc = msg.tool_calls[0]
            try:
                params = json.loads(tc.function.arguments)
                result = {
                    "intent": tc.function.name,
                    "params": params,
                    "confidence": 1.0,
                    "response_message": f"Exécution de {tc.function.name}...",
                    "raw": command
                }
                return result
            except json.JSONDecodeError as e:
                logger.warning(f"Tool call JSON parse failed: {e}, intent={tc.function.name}, falling back to text response")
            except Exception as e:
                logger.error(f"Tool call extraction error: {e}")

        raw_json = response.choices[0].message.content.strip()
        result = self._parse_json_response(raw_json, command)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    #  PROMPT SYSTÈME
    # ──────────────────────────────────────────────────────────────────────────

    def _build_system_prompt(self, memory_summary: str = "") -> str:
        """
        LEVEL 3 OPTIMIZED — Ultra-minimal system prompt (~400 tokens).
        
        Le Router gère 90% des commandes (0 tokens).
        Ce prompt ne s'utilise que comme fallback pour les cas complexes.
        """
        
        # LEVEL 3: Use minimal system prompt
        from core.smart_context_injector import get_context_injector
        injector = get_context_injector()
        return injector.build_minimal_system_prompt(user_context=memory_summary[:100])

    def _parse_json_response(self, raw_json: str, original_command: str) -> dict:
        clean = re.sub(r"```(?:json)?", "", raw_json).strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"JSON invalide : {e}  Brut: {raw_json}")
            return self._unknown(original_command, f"JSON invalide : {e}")

        intent           = data.get("intent", "UNKNOWN")
        params           = data.get("params", {})
        confidence       = float(data.get("confidence", 0.5))
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
    #  FALLBACK KEYWORDS — [B9] ÉTENDU
    # ──────────────────────────────────────────────────────────────────────────

    def _fallback_keywords(self, command: str) -> dict:
        """Fallback par mots-clés — utilisé SEULEMENT si Groq est hors ligne."""
        lower = self._normalize_text(command.lower())

        # ── Heure / date ──────────────────────────────────────────────────────
        if any(k in lower for k in ["heure", "time is", "quelle heure", "date", "quel jour"]):
            return {"intent": "SYSTEM_TIME", "params": {}, "confidence": 0.95}

        # ── Système ───────────────────────────────────────────────────────────
        if any(k in lower for k in ["eteins", "eteinds", "shutdown", "poweroff", "coupe le pc", "arrete le pc"]):
            return {"intent": "SYSTEM_SHUTDOWN", "params": {"delay_seconds": 10}, "confidence": 0.8}
        if any(k in lower for k in ["redemarre", "restart", "reboot", "redemarrage"]):
            return {"intent": "SYSTEM_RESTART", "params": {"delay_seconds": 10}, "confidence": 0.8}
        if any(k in lower for k in ["veille", "sleep mode", "en veille"]) and "bluetooth" not in lower:
            return {"intent": "POWER_SLEEP", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["hiberne", "hibernate", "hibernation"]):
            return {"intent": "POWER_HIBERNATE", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["verrouille", "lock screen", "verrouiller"]) and "deverrouille" not in lower:
            return {"intent": "SYSTEM_LOCK", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["deverrouille", "deverrouiller", "unlock"]):
            return {"intent": "SCREEN_UNLOCK", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["eteins l ecran", "eteins ecran", "ecran off", "screen off", "coupe l ecran"]):
            return {"intent": "SCREEN_OFF", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["logout", "deconnecte", "deconnexion"]) and "wifi" not in lower and "bluetooth" not in lower:
            return {"intent": "SYSTEM_LOGOUT", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["annule extinction", "annule arret", "annule shutdown", "power cancel", "annule le redemarrage"]):
            return {"intent": "POWER_CANCEL", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["etat alimentation", "etat d alimentation", "power state", "batterie"]):
            return {"intent": "POWER_STATE", "params": {}, "confidence": 0.85}
        if any(k in lower for k in ["infos systeme", "info systeme", "system info", "etat du pc", "etat pc", "infos pc"]):
            return {"intent": "SYSTEM_INFO", "params": {}, "confidence": 0.8}
        if (("systeme" in lower or "pc" in lower or "ordinateur" in lower)
                and any(k in lower for k in ["info", "infos", "etat", "spec", "specs", "configuration"])):
            return {"intent": "SYSTEM_INFO", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["disque", "stockage", "disk info", "espace disque"]):
            return {"intent": "SYSTEM_DISK", "params": {}, "confidence": 0.8}
        if any(k in lower for k in ["processus", "process", "taches en cours"]):
            return {"intent": "SYSTEM_PROCESSES", "params": {"sort_by": "cpu"}, "confidence": 0.8}
        if any(k in lower for k in ["gestionnaire des taches", "task manager"]):
            return {"intent": "SYSTEM_TASK_MANAGER", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["reveiller", "wake on lan", "wake-on-lan"]):
            return {"intent": "WAKE_ON_LAN", "params": {}, "confidence": 0.85}

        # ── Écran / Luminosité ────────────────────────────────────────────────
        if any(k in lower for k in ["luminosite", "luminosity", "brightness", "brillo"]):
            return {"intent": "SCREEN_BRIGHTNESS", "params": {"level": self._extract_number(lower, 70)}, "confidence": 0.9}
        if any(k in lower for k in ["capture", "screenshot", "screen capture", "photo ecran", "prendre ecran"]):
            if any(k in lower for k in ["telephone", "phone", "mobile", "envoie", "partage"]):
                return {"intent": "SCREENSHOT_TO_PHONE", "params": {}, "confidence": 0.85}
            return {"intent": "SCREEN_CAPTURE", "params": {}, "confidence": 0.85}
        if any(k in lower for k in ["partager ecran", "partage ecran", "envoie capture", "capture au telephone"]):
            return {"intent": "SCREENSHOT_TO_PHONE", "params": {}, "confidence": 0.85}
        if any(k in lower for k in ["resolution", "infos ecran", "info ecran", "taille ecran", "screen info"]):
            return {"intent": "SCREEN_INFO", "params": {}, "confidence": 0.85}
        if any(k in lower for k in ["enregistre ecran", "enregistre l ecran", "record screen"]):
            return {"intent": "SCREEN_RECORD", "params": {}, "confidence": 0.85}

        # ── Audio ─────────────────────────────────────────────────────────────
        if "volume" in lower:
            if any(k in lower for k in ["monte", "augmente", "hausse", "up", "plus fort"]):
                return {"intent": "AUDIO_VOLUME_UP",   "params": {"step": self._extract_number(lower, 10)}, "confidence": 0.85}
            if any(k in lower for k in ["baisse", "diminue", "descends", "down", "moins fort"]):
                return {"intent": "AUDIO_VOLUME_DOWN", "params": {"step": self._extract_number(lower, 10)}, "confidence": 0.85}
            return {"intent": "AUDIO_VOLUME_SET", "params": {"level": self._extract_number(lower, 50)}, "confidence": 0.8}
        if any(k in lower for k in ["mute", "coupe le son", "silence", "muet"]):
            return {"intent": "AUDIO_MUTE", "params": {}, "confidence": 0.85}

        # ── Musique [B8] ──────────────────────────────────────────────────────
        if any(k in lower for k in ["musique suivante", "chanson suivante", "piste suivante", "next track", "suivant"]):
            if any(k in lower for k in ["page suivante", "onglet suivant", "navigateur", "browser"]):
                pass
            else:
                return {"intent": "MUSIC_NEXT", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["musique precedente", "chanson precedente", "piste precedente", "previous track"]):
            return {"intent": "MUSIC_PREV", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["mets en pause", "pause musique", "pause la musique", "stoppe la musique"]):
            return {"intent": "MUSIC_PAUSE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["reprends la musique", "reprends", "continue la musique"]):
            return {"intent": "MUSIC_RESUME", "params": {}, "confidence": 0.85}
        if "resume" in lower and any(k in lower for k in ["musique", "chanson", "piste"]):
            return {"intent": "MUSIC_RESUME", "params": {}, "confidence": 0.85}
        if any(k in lower for k in ["arrete la musique", "stop musique", "coupe la musique"]):
            return {"intent": "MUSIC_STOP", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["quelle musique", "qu est ce qui joue", "musique actuelle", "c est quoi cette musique"]):
            return {"intent": "MUSIC_CURRENT", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["cree playlist", "creer playlist", "nouvelle playlist", "create playlist"]):
            name = self._extract_after(lower, ["cree playlist ", "creer playlist ", "nouvelle playlist ", "create playlist "])
            return {"intent": "MUSIC_PLAYLIST_CREATE", "params": {"name": name or "ma playlist"}, "confidence": 0.85}
        if any(k in lower for k in ["joue playlist", "lance playlist", "joue la playlist", "joue ma playlist", "play playlist"]):
            name = self._extract_after(lower, ["joue playlist ", "lance playlist ", "joue la playlist ", "joue ma playlist ", "play playlist "])
            return {"intent": "MUSIC_PLAYLIST_PLAY", "params": {"name": name or ""}, "confidence": 0.85}
        if any(k in lower for k in ["liste mes playlists", "mes playlists", "affiche playlists", "list playlists"]):
            return {"intent": "MUSIC_PLAYLIST_LIST", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["supprime la playlist", "supprime une playlist", "delete playlist", "efface la playlist"]):
            name = self._extract_after(lower, ["supprime la playlist ", "supprime la  playlist ", "delete playlist ", "efface la playlist "])
            return {"intent": "MUSIC_PLAYLIST_DELETE", "params": {"name": name or ""}, "confidence": 0.85}

        # Renommer playlist: "renomme la playlist X en Y"
        m_rename = re.search(r"(?:renomme|renommer|rename|change le nom de)\s+(?:la|ma)?\s*playlist\s+(.+?)\s+(?:en|to|comme)\s+(.+)$", lower)
        if m_rename:
            old_name = m_rename.group(1).strip()
            new_name = m_rename.group(2).strip()
            return {
                "intent": "MUSIC_PLAYLIST_RENAME",
                "params": {"old_name": old_name, "new_name": new_name},
                "confidence": 0.88,
            }

        # Dupliquer playlist: "duplique la playlist X en Y"
        m_dup = re.search(r"(?:duplique|dupliquer|copie|duplicate)\s+(?:la|ma)?\s*playlist\s+(.+?)\s+(?:en|vers|to)\s+(.+)$", lower)
        if m_dup:
            source = m_dup.group(1).strip()
            target = m_dup.group(2).strip()
            return {
                "intent": "MUSIC_PLAYLIST_DUPLICATE",
                "params": {"source": source, "target": target},
                "confidence": 0.86,
            }

        # Export playlist: "exporte la playlist X [en json] [vers path]"
        m_export = re.search(r"(?:exporte|exporter|export)\s+(?:la|ma)?\s*playlist\s+(.+?)(?:\s+vers\s+(.+))?$", lower)
        if m_export and "import" not in lower:
            chunk = m_export.group(1).strip()
            out_path = (m_export.group(2) or "").strip()
            fmt = "json" if " json" in f" {chunk} " or "json" in out_path else "m3u"
            name = chunk.replace(" en json", "").replace(" en m3u", "").strip()
            return {
                "intent": "MUSIC_PLAYLIST_EXPORT",
                "params": {"name": name, "format": fmt, "path": out_path},
                "confidence": 0.84,
            }

        # Import playlist: "importe playlist X depuis C:/..." ou "importe playlist depuis C:/..."
        m_import = re.search(r"(?:importe|importer|import)\s+(?:la|ma)?\s*playlist(?:\s+(.+?))?\s+(?:depuis|from)\s+(.+)$", lower)
        if m_import:
            name = (m_import.group(1) or "").strip()
            in_path = m_import.group(2).strip()
            mode = "append" if "ajoute" in lower or "append" in lower else "replace"
            return {
                "intent": "MUSIC_PLAYLIST_IMPORT",
                "params": {"name": name, "path": in_path, "mode": mode},
                "confidence": 0.84,
            }

        # Fusion playlist: "fusionne la playlist A avec B [dans C]"
        m_merge = re.search(r"(?:fusionne|fusionner|merge)\s+(?:la|ma)?\s*playlist\s+(.+?)\s+(?:avec|and)\s+(.+?)(?:\s+(?:dans|en|to)\s+(.+))?$", lower)
        if m_merge:
            source = m_merge.group(1).strip()
            target = m_merge.group(2).strip()
            output = (m_merge.group(3) or "").strip()
            return {
                "intent": "MUSIC_PLAYLIST_MERGE",
                "params": {"source": source, "target": target, "output": output},
                "confidence": 0.84,
            }

        # Déplacer chanson dans playlist
        m_move = re.search(r"(?:deplace|déplace|move)\s+(.+?)\s+(?:dans|de la playlist|de ma playlist)\s+(.+?)\s+(?:a la position|en position|position)\s+(\d+)$", lower)
        if m_move:
            query = m_move.group(1).strip()
            name = m_move.group(2).strip()
            to_index = int(m_move.group(3))
            return {
                "intent": "MUSIC_PLAYLIST_MOVE_SONG",
                "params": {"name": name, "query": query, "to_index": to_index},
                "confidence": 0.84,
            }

        queue_tokens = ["file d attente", "file d'attente", "queue"]

        # Queue: add playlist
        if any(k in lower for k in ["ajoute la playlist", "ajoute ma playlist"]) and any(k in lower for k in queue_tokens):
            m_qpl = re.search(r"ajoute\s+(?:la|ma)?\s*playlist\s+(.+?)\s+(?:a|dans)\s+(?:la\s+)?(?:file d attente|file d'attente|queue)", lower)
            if m_qpl:
                return {
                    "intent": "MUSIC_QUEUE_ADD_PLAYLIST",
                    "params": {"name": m_qpl.group(1).strip()},
                    "confidence": 0.84,
                }

        # Queue: add song
        if any(k in lower for k in queue_tokens) and any(k in lower for k in ["ajoute", "mets"]):
            m_q = re.search(r"(?:ajoute|mets)\s+(.+?)\s+(?:a|dans)\s+(?:la\s+)?(?:file d attente|file d'attente|queue)", lower)
            if m_q:
                return {
                    "intent": "MUSIC_QUEUE_ADD",
                    "params": {"query": m_q.group(1).strip()},
                    "confidence": 0.84,
                }

        # Queue: list / clear / play
        if any(k in lower for k in ["liste la file d attente", "liste la file d'attente", "affiche la file d attente", "affiche la file d'attente", "queue list", "montre la queue"]):
            return {"intent": "MUSIC_QUEUE_LIST", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["vide la file d attente", "vide la file d'attente", "efface la file d attente", "efface la file d'attente", "clear queue"]):
            return {"intent": "MUSIC_QUEUE_CLEAR", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["lance la file d attente", "lance la file d'attente", "joue la file d attente", "joue la file d'attente", "play queue"]):
            return {"intent": "MUSIC_QUEUE_PLAY", "params": {}, "confidence": 0.9}

        if any(k in lower for k in ["vide la playlist", "vide ma playlist", "vide une playlist", "clear playlist", "vide le contenu", "efface tout de la playlist"]):
            name = self._extract_after(lower, [
                "vide la playlist ",
                "vide ma playlist ",
                "vide une playlist ",
                "clear playlist ",
                "vide le contenu de la playlist ",
            ])
            return {"intent": "MUSIC_PLAYLIST_CLEAR", "params": {"name": name or ""}, "confidence": 0.85}
        if any(k in lower for k in ["enleve", "enlève", "retire", "supprime la chanson", "remove song", "efface la musique"]):
            # Extraire le nom de la playlist
            pl_name = ""
            for prefix in [
                "de la playlist ",
                "de ma playlist ",
                "dans la playlist ",
                "dans ma playlist ",
                "de la  playlist ",
                "de la playlist",
                "from playlist ",
            ]:
                if prefix in lower:
                    pl_name = lower.split(prefix)[-1].strip()
                    break
            # Extraire le titre de la chanson (entre "enleve" et "de la playlist")
            song_query = ""
            for trigger in ["enleve ", "enlève ", "retire ", "supprime la chanson ", "remove "]:
                if trigger in lower:
                    before = lower.split(trigger)[-1]
                    if "de la playlist" in before:
                        song_query = before.split("de la playlist")[0].strip()
                    elif "de ma playlist" in before:
                        song_query = before.split("de ma playlist")[0].strip()
                    elif "dans la playlist" in before:
                        song_query = before.split("dans la playlist")[0].strip()
                    elif "dans ma playlist" in before:
                        song_query = before.split("dans ma playlist")[0].strip()
                    else:
                        song_query = before.strip()
                    break
            if pl_name and song_query:
                return {"intent": "MUSIC_PLAYLIST_REMOVE_SONG", 
                        "params": {"name": pl_name, "query": song_query}, 
                        "confidence": 0.85}
        if any(k in lower for k in ["lecture aleatoire", "shuffle", "mode aleatoire"]):
            return {"intent": "MUSIC_SHUFFLE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["repete cette musique", "repete la musique", "repeat"]):
            return {"intent": "MUSIC_REPEAT", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["scanne la musique", "analyse musique", "scan musique", "bibliotheque musicale"]):
            return {"intent": "MUSIC_LIBRARY_SCAN", "params": {}, "confidence": 0.85}

        # Ajout dossier/fichiers à une playlist
        if any(k in lower for k in ["ajoute le dossier", "ajoute tous les", "ajoute toute ma musique",
                                     "met le dossier", "mets le dossier", "remplis la playlist",
                                     "remplie la playlist", "tous les songs", "tous les fichiers musique"]):
            # Extraire le nom de playlist
            pl_name = ""
            for prefix in ["a la playlist ", "à la playlist ", "dans la playlist ", "dans ma playlist "]:
                if prefix in lower:
                    pl_name = lower.split(prefix, 1)[-1].strip()
                    break
            # Extraire le dossier
            folder = ""
            if "musique" in lower:
                folder = ""  # dossier Musique par défaut
            return {"intent": "MUSIC_PLAYLIST_ADD_FOLDER",
                    "params": {"name": pl_name or "ma playlist", "folder": folder},
                    "confidence": 0.88}

        # Ajout chanson spécifique à une playlist
        if any(k in lower for k in ["ajoute la chanson", "ajoute ce morceau", "ajoute cette chanson"]):
            pl_name = ""
            for prefix in ["a la playlist ", "à la playlist ", "dans la playlist "]:
                if prefix in lower:
                    pl_name = lower.split(prefix, 1)[-1].strip()
                    break
            query = self._extract_after(lower, ["ajoute la chanson ", "ajoute ce morceau ", "ajoute cette chanson "])
            # Nettoyer le query (retirer "à la playlist X")
            for prefix in [" a la playlist", " à la playlist", " dans la playlist"]:
                if prefix in query:
                    query = query.split(prefix)[0].strip()
                    break
            return {"intent": "MUSIC_PLAYLIST_ADD_SONG",
                    "params": {"name": pl_name or "ma playlist", "query": query},
                    "confidence": 0.85}
        if any(k in lower for k in ["joue", "play", "ecoute", "lecture"]) and any(k in lower for k in ["musique", "chanson", "artiste", "titre", "son"]):
            query = self._extract_after(lower, ["joue ", "play ", "ecoute ", "lecture de "])
            return {"intent": "MUSIC_PLAY", "params": {"query": query}, "confidence": 0.75}

        # ── Applications ──────────────────────────────────────────────────────
        if any(k in lower for k in ["ouvre", "lance", "demarre", "mets", "start"]) and "dossier" not in lower and "fichier" not in lower and not any(k in lower for k in ["navigateur", "browser", "onglet", "page web", "sur internet", "site", "youtube", "github", "google", "bing", "wikipedia", "reddit", "stackoverflow", "gmail", "duckduckgo", "amazon", "http", "www.", ".com", ".fr", ".org", ".net", "resultat", "résultat"]):
            app_name = self._extract_after(lower, ["ouvre ", "lance ", "demarre ", "mets ", "start "])
            if app_name and not any(c in app_name for c in ["/", "\\"]):
                return {"intent": "APP_OPEN", "params": {"app_name": app_name, "args": []}, "confidence": 0.75}
        if any(k in lower for k in ["ferme", "referme", "close", "quitte", "quit"]) and not any(k in lower for k in ["onglet", "navigateur", "browser", "page", "chrome", "firefox", "edge", "brave"]):
            if any(k in lower for k in ["la", "ca", "ça", "cette", "fenetre", "fenêtre", "ici"]):
                return {"intent": "WINDOW_CLOSE", "params": {"query": ""}, "confidence": 0.85}
            app_name = self._extract_after(lower, ["ferme ", "referme ", "close ", "quitte ", "quit "])
            return {"intent": "APP_CLOSE", "params": {"app_name": app_name}, "confidence": 0.75}
        if any(k in lower for k in ["quelles applis", "applis ouvertes", "applications ouvertes", "liste les apps"]):
            return {"intent": "APP_LIST_RUNNING", "params": {}, "confidence": 0.9}

        # ── Réseau ────────────────────────────────────────────────────────────
        if any(k in lower for k in ["liste reseaux", "reseaux wifi", "reseaux disponibles", "wifi disponibles"]):
            return {"intent": "WIFI_LIST", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["connecte au wifi", "connect wifi", "rejoins le wifi"]):
            return {"intent": "WIFI_CONNECT", "params": self._extract_wifi_connect_params(command), "confidence": 0.85}
        if any(k in lower for k in ["deconnecte du wifi", "deconnecte wifi", "disconnect wifi"]):
            return {"intent": "WIFI_DISCONNECT", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["active le wifi", "active wifi", "enable wifi", "allume wifi"]):
            return {"intent": "WIFI_ENABLE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["desactive le wifi", "desactive wifi", "disable wifi", "eteins wifi"]):
            return {"intent": "WIFI_DISABLE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["active le bluetooth", "active bluetooth", "enable bluetooth"]):
            return {"intent": "BLUETOOTH_ENABLE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["desactive le bluetooth", "desactive bluetooth", "disable bluetooth"]):
            return {"intent": "BLUETOOTH_DISABLE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["liste appareils bluetooth", "appareils bluetooth", "bluetooth devices"]):
            return {"intent": "BLUETOOTH_LIST", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["infos reseau", "info reseau", "network info", "ip locale", "mon ip"]):
            return {"intent": "NETWORK_INFO", "params": {}, "confidence": 0.85}

        # ── Navigateur ────────────────────────────────────────────────────────
        if any(k in lower for k in ["resume cette page", "resume la page", "resumer la page", "summarize", "faire un resume"]):
            return {"intent": "BROWSER_SUMMARIZE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["lis la page", "lire la page", "lire le contenu", "lis le contenu", "affiche le texte"]):
            return {"intent": "BROWSER_READ", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["liste les onglets", "onglets ouverts", "mes onglets", "quels onglets", "affiche les onglets"]):
            return {"intent": "BROWSER_LIST_TABS", "params": {}, "confidence": 0.95}
        if any(k in lower for k in ["nouvel onglet", "ouvre un onglet", "new tab", "ouvre un nouvel onglet"]):
            return {"intent": "BROWSER_NEW_TAB", "params": {}, "confidence": 0.95}
        if any(k in lower for k in ["ferme l onglet", "ferme l'onglet", "ferme cet onglet", "close tab", "ferme l'onglet actif"]):
            return {"intent": "BROWSER_CLOSE_TAB", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["recharge la page", "actualise la page", "refresh", "recharger la page", "actualiser"]):
            return {"intent": "BROWSER_RELOAD", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["page precedente", "retour navigateur", "go back", "reviens en arriere", "retourne en arriere"]):
            return {"intent": "BROWSER_BACK", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["page suivante", "go forward", "avance", "aller a la page suivante"]):
            return {"intent": "BROWSER_FORWARD", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["extrait les liens", "liste les liens", "liens de la page"]):
            return {"intent": "BROWSER_EXTRACT_LINKS", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["ferme le navigateur", "ferme chrome", "ferme firefox", "ferme edge", "ferme brave", "close browser"]):
            return {"intent": "BROWSER_CLOSE", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["scrolle", "scroll", "defile", "défile"]):
            direction = "down"
            if any(k in lower for k in ["haut", "up", "top"]):
                direction = "up"
            elif any(k in lower for k in ["bas", "down", "bottom"]):
                direction = "down"
            return {"intent": "BROWSER_SCROLL", "params": {"direction": direction}, "confidence": 0.9}
        open_site_match = re.search(
            r"^ouvre\s+(youtube|github|google|bing|wikipedia|reddit|stackoverflow|gmail|amazon|duckduckgo)$",
            lower,
        )
        if open_site_match:
            site = open_site_match.group(1)
            open_urls = {
                "youtube": "https://www.youtube.com",
                "github": "https://github.com",
                "google": "https://www.google.com",
                "bing": "https://www.bing.com",
                "wikipedia": "https://fr.wikipedia.org",
                "reddit": "https://www.reddit.com",
                "stackoverflow": "https://stackoverflow.com",
                "gmail": "https://mail.google.com",
                "amazon": "https://www.amazon.fr",
                "duckduckgo": "https://duckduckgo.com",
            }
            return {
                "intent": "BROWSER_OPEN",
                "params": {"url": open_urls.get(site, "")},
                "confidence": 0.92,
            }

        site_match = re.search(
            r"(?:va sur|ouvre|go to|visite)\s+(youtube|github|google|bing|wikipedia|reddit|stackoverflow|gmail|amazon|duckduckgo)(?:\s+et\s+cherche\s+(.+))?$",
            lower,
        )
        if site_match:
            return {
                "intent": "BROWSER_GO_TO_SITE",
                "params": {
                    "site": site_match.group(1),
                    "query": (site_match.group(2) or "").strip(),
                },
                "confidence": 0.92,
            }
        if any(k in lower for k in ["ouvre le resultat", "ouvre le premier", "ouvre le 1",
                                     "ouvre le deuxieme", "ouvre le 2", "ouvre le 2e",
                                     "ouvre le troisieme", "ouvre le 3"]):
            rank = 1
            if any(k in lower for k in ["deuxieme", "2e", "2eme", "second"]):
                rank = 2
            elif any(k in lower for k in ["troisieme", "3e", "3eme"]):
                rank = 3
            return {"intent": "BROWSER_OPEN_RESULT", "params": {"rank": rank}, "confidence": 0.9}

        # ── Fichiers ou web ───────────────────────────────────────────────────
        ext_map = {
            "pdf": "pdf", "doc": "doc", "docx": "docx", "txt": "txt", "md": "md",
            "xls": "xls", "xlsx": "xlsx", "csv": "csv", "ppt": "ppt", "pptx": "pptx",
            "jpg": "jpg", "jpeg": "jpeg", "png": "png", "gif": "gif", "zip": "zip",
        }
        ext_match = re.search(r"\b(pdf|docx?|txt|md|xlsx?|csv|pptx?|jpg|jpeg|png|gif|zip)\b", lower)
        ext = ext_map.get(ext_match.group(1), "") if ext_match else ""

        if any(k in lower for k in ["doublon", "fichier en double", "duplicate"]) and any(k in lower for k in ["supprime", "efface", "delete", "nettoie"]):
            strategy = "keep_newest"
            if any(k in lower for k in ["plus ancien", "oldest"]):
                strategy = "keep_oldest"
            elif any(k in lower for k in ["chemin court", "shortest path"]):
                strategy = "keep_shortest_path"
            dry_run = not any(k in lower for k in ["maintenant", "execute", "exécute", "go", "confirme"])
            return {
                "intent": "FILE_DELETE_DUPLICATES",
                "params": {"strategy": strategy, "extension": ext, "dry_run": dry_run},
                "confidence": 0.9,
            }

        if any(k in lower for k in ["doublon", "fichier en double", "duplicate"]):
            return {
                "intent": "FILE_FIND_DUPLICATES",
                "params": {"extension": ext, "min_size": 1},
                "confidence": 0.9,
            }

        if any(k in lower for k in ["nettoie", "clean"]) and any(k in lower for k in ["dossier vide", "dossiers vides", "empty folder"]):
            folder = "downloads"
            for token in ["downloads", "telechargements", "téléchargements", "documents", "bureau", "desktop"]:
                if token in lower:
                    folder = token
                    break
            dry_run = not any(k in lower for k in ["maintenant", "execute", "exécute", "go", "confirme"])
            return {
                "intent": "FILE_CLEAN",
                "params": {"folder": folder, "dry_run": dry_run},
                "confidence": 0.88,
            }

        if any(k in lower for k in ["renomme", "rename"]) and any(k in lower for k in ["tous les fichiers", "en masse", "bulk", "lot"]):
            folder = ""
            for token in ["downloads", "telechargements", "téléchargements", "documents", "bureau", "desktop"]:
                if token in lower:
                    folder = token
                    break
            pattern = self._extract_after(lower, ["remplace ", "replace "])
            replacement = ""
            if " par " in pattern:
                parts = pattern.split(" par ", 1)
                pattern = parts[0].strip(" '\"")
                replacement = parts[1].strip(" '\"")
            dry_run = not any(k in lower for k in ["maintenant", "execute", "exécute", "go", "confirme"])
            return {
                "intent": "FILE_BULK_RENAME",
                "params": {
                    "folder": folder,
                    "pattern": pattern.strip(" '\""),
                    "replacement": replacement,
                    "extension_filter": ext,
                    "dry_run": dry_run,
                },
                "confidence": 0.86,
            }

        if any(k in lower for k in ["organise", "organize", "range"]) and "dossier" in lower:
            folder = "downloads"
            for token in ["downloads", "telechargements", "téléchargements", "documents", "bureau", "desktop"]:
                if token in lower:
                    folder = token
                    break
            dry_run = not any(k in lower for k in ["maintenant", "execute", "exécute", "go", "confirme"])
            return {
                "intent": "FILE_ORGANIZE",
                "params": {"folder": folder, "dry_run": dry_run},
                "confidence": 0.88,
            }

        has_date_hint = any(k in lower for k in [
            "aujourd", "hier", "semaine", "mois", "annee", "année", "last_7", "last_30", "last_90",
        ])
        has_size_hint = any(k in lower for k in [
            "plus de", "moins de", "entre", "mo", "mb", "go", "gb", "ko", "kb", "taille",
        ])
        has_file_query = any(k in lower for k in ["fichier", "fichiers", "document", "documents", "trouve", "cherche", "recherche"])

        if has_file_query and has_date_hint and has_size_hint:
            period = "week"
            if any(k in lower for k in ["aujourd", "today"]):
                period = "today"
            elif any(k in lower for k in ["hier", "yesterday"]):
                period = "yesterday"
            elif any(k in lower for k in ["mois", "month", "last_30"]):
                period = "month"
            elif any(k in lower for k in ["annee", "année", "year", "last_90"]):
                period = "year"
            size_value = self._extract_number(lower, default=10)
            size_unit = "MB"
            if any(k in lower for k in ["go", "gb"]):
                size_unit = "GB"
            elif any(k in lower for k in ["ko", "kb"]):
                size_unit = "KB"
            return {
                "intent": "FILE_SEARCH_ADVANCED",
                "params": {
                    "extension": ext,
                    "period": period,
                    "min_size": size_value,
                    "size_unit": size_unit,
                },
                "confidence": 0.87,
            }

        if has_file_query and has_date_hint:
            period = "week"
            if any(k in lower for k in ["aujourd", "today"]):
                period = "today"
            elif any(k in lower for k in ["hier", "yesterday"]):
                period = "yesterday"
            elif any(k in lower for k in ["mois", "month", "last_30"]):
                period = "month"
            elif any(k in lower for k in ["annee", "année", "year", "last_90"]):
                period = "year"
            return {
                "intent": "FILE_SEARCH_DATE",
                "params": {"period": period, "extension": ext},
                "confidence": 0.86,
            }

        if has_file_query and has_size_hint:
            min_size = self._extract_number(lower, default=10)
            unit = "MB"
            if any(k in lower for k in ["go", "gb"]):
                unit = "GB"
            elif any(k in lower for k in ["ko", "kb"]):
                unit = "KB"
            operator = "gt"
            if any(k in lower for k in ["moins de", "inferieur", "inférieur", "lt"]):
                operator = "lt"
            return {
                "intent": "FILE_SEARCH_SIZE",
                "params": {"min_size": min_size, "operator": operator, "unit": unit, "extension": ext},
                "confidence": 0.86,
            }

        if re.search(r"(prepare|prépare|prepare moi|prepare mon).*(dossier).*(candidature|job|emploi)", lower):
            dry_run = not any(k in lower for k in ["execute", "exécute", "fais le", "cree le zip", "crée le zip", "go"])
            return {
                "intent": "FILE_PREPARE_APPLICATION",
                "params": {"dry_run": dry_run, "package_name": "dossier_candidature"},
                "confidence": 0.9,
            }

        if re.search(r"(classe|classifie|classifie|catégorise|categorise).*(document|fichier)", lower):
            move_files = any(k in lower for k in ["range", "deplace", "déplace", "organise physiquement"])
            return {
                "intent": "FILE_CLASSIFY",
                "params": {"move_files": move_files, "max_results": 120},
                "confidence": 0.88,
            }

        if ("google drive" in lower or "gdrive" in lower or "mon drive" in lower) and any(k in lower for k in ["sync", "synchronise", "synchroniser", "sauvegarde", "backup", "copie"]):
            source = "documents"
            for token in ["downloads", "telechargements", "téléchargements", "documents", "bureau", "desktop"]:
                if token in lower:
                    source = token
                    break
            dry_run = not any(k in lower for k in ["maintenant", "execute", "exécute", "fais le", "go"])
            mode = "mirror" if any(k in lower for k in ["miroir", "mirror", "exactement pareil"]) else "copy"
            return {
                "intent": "FILE_SYNC_DRIVE",
                "params": {"source": source, "mode": mode, "dry_run": dry_run},
                "confidence": 0.88,
            }

        if any(k in lower for k in ["cherche sur youtube", "recherche sur youtube", "search youtube"]) or ("youtube" in lower and re.search(r"\b(cherche|recherche|search)\b", lower)):
            query = self._extract_after(lower, ["cherche sur youtube ", "recherche sur youtube ", "search youtube ", "youtube "])
            return {"intent": "BROWSER_SEARCH_YOUTUBE", "params": {"query": query}, "confidence": 0.9}
        # Email search BEFORE generic search to avoid FILE_SEARCH conflict
        if any(k in lower for k in ["cherche email", "recherche email", "trouve email", "email de", "mail de", "messages de"]):
            query = self._extract_after(lower, ["cherche email ", "recherche email ", "trouve email ", "email de ", "mail de ", "messages de "])
            return {"intent": "EMAIL_SEARCH", "params": {"query": query or lower}, "confidence": 0.9}
        if re.search(r"\b(cherche|trouve|search)\b", lower):
            query = self._extract_after(lower, ["cherche ", "trouve ", "search "])
            if any(k in lower for k in ["sur le web", "sur google", "google", "internet",
                                         "en ligne", "sur bing", "sur duckduckgo", "sur youtube", "tutorial", "tutoriel", "news", "nouvelles"]):
                return {"intent": "BROWSER_SEARCH", "params": {"query": query}, "confidence": 0.85}
            return {"intent": "FILE_SEARCH", "params": {"query": query}, "confidence": 0.7}
        if any(k in lower for k in ["recherche", "google", "web", "internet"]):
            query = self._extract_after(lower, ["recherche ", "google ", "cherche sur internet "])
            return {"intent": "BROWSER_SEARCH", "params": {"query": query}, "confidence": 0.75}
        if any(k in lower for k in ["youtube"]):
            query = self._extract_after(lower, ["youtube ", "sur youtube "])
            return {"intent": "BROWSER_SEARCH_YOUTUBE", "params": {"query": query}, "confidence": 0.85}

        # ── BROWSER Sessions & Advanced (S6+) ──────────────────────────────────
        if re.search(r"(sauvegarde|save).*(session|cookie|connexion|login)", lower):
            site = ""
            for kw in ["gmail", "github", "linkedin", "facebook", "twitter", "outlook", "notion", "discord"]:
                if kw in lower:
                    site = kw
                    break
            return {"intent": "BROWSER_SAVE_SESSION", "params": {"site": site or "site"}, "confidence": 0.88}

        if re.search(r"(verifie|verify|check).*(connect|login|session|connexion)", lower):
            site = ""
            for kw in ["gmail", "github", "linkedin", "facebook", "twitter", "outlook", "notion", "discord"]:
                if kw in lower:
                    site = kw
                    break
            return {"intent": "BROWSER_CHECK_LOGIN", "params": {"site": site or "site"}, "confidence": 0.88}

        if re.search(r"(resume|resumé|summary).*(structuré|structure|structured)", lower):
            return {"intent": "BROWSER_EXTRACT_SUMMARY", "params": {}, "confidence": 0.9}

        if re.search(r"(redige|rediges|compose|rédige).*(email|mail|message)", lower) or \
           re.search(r"(email|mail|message).*(à|to).+avec", lower):
            to = self._extract_email_address(lower) or ""
            subject = self._extract_after(lower, ["objet ", "subject ", "sujet "])
            body = self._extract_quoted_text(lower) or ""
            return {"intent": "BROWSER_COMPOSE_EMAIL", 
                   "params": {"to": to, "subject": subject, "body": body}, 
                   "confidence": 0.85}

        # ── Documents ─────────────────────────────────────────────────────────
        if any(k in lower for k in ["lis le document", "lis le fichier", "lire le document", "ouvre le pdf"]):
            path = self._extract_after(lower, ["lis le document ", "lis le fichier ", "lire le document "])
            return {"intent": "DOC_READ", "params": {"path": path}, "confidence": 0.8}
        if any(k in lower for k in ["resume le document", "resumé le document", "summarize"]):
            path = self._extract_after(lower, ["resume le document ", "resumé le document "])
            return {"intent": "DOC_SUMMARIZE", "params": {"path": path}, "confidence": 0.8}

        # ── Historique / Macros ───────────────────────────────────────────────
        if any(k in lower for k in ["repete", "rejoue", "repeter", "last command", "derniere commande"]):
            return {"intent": "REPEAT_LAST", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["historique", "mes commandes", "dernieres commandes"]):
            count = self._extract_number(lower, 10)
            if any(k in lower for k in ["efface", "supprime", "vide", "clear"]):
                return {"intent": "HISTORY_CLEAR", "params": {}, "confidence": 0.9}
            if any(k in lower for k in ["cherche", "trouve", "search"]):
                keyword = self._extract_after(lower, ["cherche ", "trouve ", "search "])
                return {"intent": "HISTORY_SEARCH", "params": {"keyword": keyword}, "confidence": 0.85}
            return {"intent": "HISTORY_SHOW", "params": {"count": count}, "confidence": 0.85}
        if any(k in lower for k in ["cherche dans l historique", "cherche historique"]):
            keyword = self._extract_after(lower, ["cherche dans l historique ", "cherche historique "])
            return {"intent": "HISTORY_SEARCH", "params": {"keyword": keyword}, "confidence": 0.85}
        if any(k in lower for k in ["liste les macros", "mes macros", "affiche macros"]):
            return {"intent": "MACRO_LIST", "params": {}, "confidence": 0.9}
        if any(k in lower for k in ["lance la macro", "lance macro", "execute macro", "run macro"]):
            name = self._extract_after(lower, ["lance la macro ", "lance macro ", "execute macro ", "run macro "])
            return {"intent": "MACRO_RUN", "params": {"name": name or ""}, "confidence": 0.85}
        for macro_name in ["mode travail", "mode nuit", "mode cinema", "mode film", "demarrage", "startup"]:
            if macro_name in lower:
                return {"intent": "MACRO_RUN", "params": {"name": macro_name}, "confidence": 0.9}

        # ── Préférences utilisateur ──────────────────────────────────────────
        pref_triggers = [
            "mon son de ", "ma musique de ", "j aime jouer quand je ",
            "quand je code je joue", "quand je travaille je joue",
            "retiens que mon ", "retiens que ma ", "associe cette",
            "associe ma playlist", "mon volume de travail",
            "ma playlist de travail", "ma playlist de codage",
            "ma playlist de detente", "mon son de codage",
        ]
        if any(t in lower for t in pref_triggers):
            label, value, category = "", "", "music"
            for ctx in ["travail", "codage", "code", "detente", "relaxation", "sport", "concentration"]:
                if ctx in lower:
                    label = ctx
                    break
            for v_kw in ["ma playlist", "cette playlist", "mon son", "ma musique"]:
                if v_kw in lower:
                    value = v_kw
                    break
            return {
                "intent": "PREFERENCE_SET",
                "params": {"label": label or "travail", "value": value or "ma playlist", "category": category},
                "confidence": 0.88,
            }

        # ── Mémoire ───────────────────────────────────────────────────────────
        if any(k in lower for k in ["souviens", "memoire jarvis", "ce dont tu te souviens", "tu te rappelles"]):
            return {"intent": "MEMORY_SHOW", "params": {}, "confidence": 0.85}

        # ── Salutations ───────────────────────────────────────────────────────
        if any(k in lower for k in ["bonjour", "salut", "hello", "bonsoir", "coucou", "hey", "hi"]):
            return {"intent": "GREETING", "params": {}, "confidence": 0.99}

        # ── Aide ──────────────────────────────────────────────────────────────
        if any(k in lower for k in ["aide", "help", "que peux-tu", "que sais-tu", "tes capacites",
                                     "tu peux faire", "qui es-tu", "ton nom"]):
            return {"intent": "HELP", "params": {}, "confidence": 0.9}

        # ── Questions de connaissance générale ────────────────────────────────
        if (
            any(k in lower for k in ["c est quoi", "cest quoi", "qu est ce que", "quest ce que",
                                      "explique", "pourquoi", "comment", "difference entre",
                                      "definition", "definition"])
            and not any(k in lower for k in [
                "ouvre", "lance", "ferme", "mets", "volume", "wifi", "bluetooth", "disque",
                "systeme", "processus", "reseau", "fichier", "dossier", "capture"
            ])
        ):
            return {"intent": "KNOWLEDGE_QA", "params": {}, "confidence": 0.75}

        # ── Telegram (Semaine 11) ─────────────────────────────────────────────
        if "telegram" in lower and any(k in lower for k in ["envoie", "message", "à", "a"]):
            # Exemple: "envoie un message à Paul sur Telegram"
            to = ""
            message = ""
            if "envoie un message à" in lower:
                content = self._extract_after(lower, ["envoie un message à "])
                if " sur telegram" in content:
                    to = content.split(" sur telegram", 1)[0].strip()
                    message = self._extract_after(lower, ["sur telegram", "via telegram"]).strip()
                else:
                    to = content.strip()
            if not message:
                # mode fallback: texte entier comme message
                message = command

            # Nettoyage minimal
            to = to.strip().strip("'\"")
            message = message.strip().strip("'\"")
            return {"intent": "TELEGRAM_SEND", "params": {"to": to or "", "message": message or command}, "confidence": 0.88}

        # ── Email ─────────────────────────────────────────────────────────────
        if any(k in lower for k in ["mes emails", "mes messages", "boite de reception", "inbox", " lis mes emails", "affiche mes emails"]):
            return {"intent": "EMAIL_INBOX", "params": {"limit": 10}, "confidence": 0.9}
        if any(k in lower for k in ["emails non lus", "emails pas lus", "nouveaux emails"]):
            return {"intent": "EMAIL_INBOX", "params": {"limit": 10, "unread_only": True}, "confidence": 0.9}
        if any(k in lower for k in ["envoie un email", "envoie un mail", "envoie un message", "envoi email", "envoi mail", "compose email"]):
            to = self._extract_email_address(command)
            return {"intent": "EMAIL_SEND", "params": {"to": to or ""}, "confidence": 0.85}
        if any(k in lower for k in ["reponds a l email", "reponds a mon mail", "reponds a ce message"]):
            return {"intent": "EMAIL_REPLY", "params": {"email_id": "", "body": ""}, "confidence": 0.8}
        if any(k in lower for k in ["transfer this email", "transmet l email", "forward email"]):
            to = self._extract_email_address(command)
            return {"intent": "EMAIL_FORWARD", "params": {"email_id": "", "to": to or ""}, "confidence": 0.8}
        if any(k in lower for k in ["cherche email", "recherche email", "trouve email", "email de", "mail de"]):
            query = self._extract_after(lower, ["cherche email ", "recherche email ", "trouve email ", "email de ", "mail de "])
            return {"intent": "EMAIL_SEARCH", "params": {"query": query or ""}, "confidence": 0.85}
        if any(k in lower for k in ["email important", "mail important", "emails urgents"]):
            return {"intent": "EMAIL_IMPORTANT", "params": {"hours": 24}, "confidence": 0.9}
        if any(k in lower for k in ["resume mes emails", "resume email", "synopsis emails"]):
            return {"intent": "EMAIL_SUMMARY", "params": {}, "confidence": 0.85}

        return self._unknown(command, "Aucun mot-clé reconnu (Groq hors ligne).")

    # ──────────────────────────────────────────────────────────────────────────
    #  SEMANTIC GUARD
    # ──────────────────────────────────────────────────────────────────────────

    def _semantic_guard(self, command: str, result: dict) -> dict:
        """Garde-fou sémantique — corrige les erreurs critiques de parsing."""
        out        = dict(result or {})
        lower      = command.lower().strip()
        normalized = self._normalize_text(lower)
        intent     = out.get("intent", "UNKNOWN")
        confidence = float(out.get("confidence", 0.0))

        # [FIX CONTEXT BLEEDING] Reject generic words as music commands
        # Simple affirmations like "oui", "liste les", "liste" should not become
        # MUSIC_PLAYLIST_LIST unless explicitly about music
        generic_words = {"oui", "non", "ok", "okay", "liste", "liste les", "affiche", 
                        "yes", "no", "display", "show", "liste moi"}
        if intent == "MUSIC_PLAYLIST_LIST" and lower in generic_words:
            # Only allow if explicitly mentioning playlists/musique/music
            if not any(k in normalized for k in ["playlist", "musique", "music", "chanson", "song"]):
                out["intent"]     = "UNKNOWN"
                out["confidence"] = 0.3  # Low confidence to trigger clarification
                return out

        # Priorité absolue 1 : requête mémoire ne doit pas annuler une extinction
        if any(k in normalized for k in ["tu te souviens", "souviens toi", "tu te rappelles", "rappelle toi"]) \
                and intent in {"SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL"}:
            out["intent"]     = "MEMORY_SHOW"
            out["params"]     = {}
            out["confidence"] = max(confidence, 0.9)
            return out

        # Priorité absolue 2 : négation explicite "n'annule pas"
        if intent in {"SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL"}:
            has_negated_cancel = (
                "n'annule pas" in lower
                or "ne l'annule pas" in lower
                or "n annule pas" in normalized
                or ("annule" in normalized and "pas" in normalized)
            )
            if has_negated_cancel:
                out["intent"]     = "UNKNOWN"
                out["params"]     = {}
                out["confidence"] = max(confidence, 0.9)
                return out

        # Correction racine: Groq peut renvoyer un placeholder ('ma playlist')
        # alors que la commande contient un nom explicite après "playlist".
        if intent in {"MUSIC_PLAYLIST_PLAY", "MUSIC_PLAYLIST_DELETE", "MUSIC_PLAYLIST_CLEAR"}:
            params = out.get("params") if isinstance(out.get("params"), dict) else {}
            parsed_name = str(params.get("name", "")).strip().lower()
            placeholders = {"", "ma playlist", "playlist", "la playlist", "ma"}
            if parsed_name in placeholders:
                patterns = {
                    "MUSIC_PLAYLIST_PLAY": r"(?:joue|lance)\s+(?:la|ma)?\s*playlist\s+(.+)$",
                    "MUSIC_PLAYLIST_DELETE": r"(?:supprime|efface|delete)\s+(?:la|ma|une)?\s*playlist\s+(.+)$",
                    "MUSIC_PLAYLIST_CLEAR": r"(?:vide|clear)\s+(?:la|ma|une)?\s*playlist\s+(.+)$",
                }
                m = re.search(patterns[intent], lower)
                if m:
                    extracted = m.group(1).strip()
                    if extracted and extracted not in placeholders:
                        out["params"] = dict(params)
                        out["params"]["name"] = extracted
                        out["confidence"] = max(confidence, 0.9)
                        return out

        # Correction navigateur : "onglet" dans la commande mais intent = APP_OPEN/BROWSER_OPEN
        # Groq confond souvent "ouvre un nouvel onglet" avec APP_OPEN ou BROWSER_OPEN
        if any(k in lower for k in ["nouvel onglet", "ouvre un onglet", "ouvre onglet", "new tab"]):
            if intent in ("APP_OPEN", "BROWSER_OPEN", "UNKNOWN"):
                out["intent"]     = "BROWSER_NEW_TAB"
                out["params"]     = {}
                out["confidence"] = max(confidence, 0.95)
                return out

        # Correction navigateur : "ferme l'onglet" confondu avec WINDOW_CLOSE/APP_CLOSE
        if any(k in lower for k in ["ferme l'onglet", "ferme cet onglet", "ferme l onglet", "close tab"]):
            if intent in ("WINDOW_CLOSE", "APP_CLOSE", "UNKNOWN"):
                out["intent"]     = "BROWSER_CLOSE_TAB"
                out["params"]     = {}
                out["confidence"] = max(confidence, 0.95)
                return out

        # Si Groq est très confiant, ne pas toucher
        if confidence >= 0.85:
            return out

        # Correction : volume ≠ AUDIO_PLAY
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

        # Correction : luminosité → SCREEN_BRIGHTNESS
        if any(k in normalized for k in ["luminosite", "luminosity", "brightness", "eclairage"]):
            if intent in ("AUDIO_PLAY", "AUDIO_VOLUME_SET", "UNKNOWN", "APP_OPEN"):
                out["intent"]     = "SCREEN_BRIGHTNESS"
                out["params"]     = {"level": self._extract_number(lower, 70)}
                out["confidence"] = 0.92

        # Correction : "ferme ça" ≠ SCREEN_OFF
        close_terms = ["ferme", "referme", "fermer", "refermer", "close", "quitte"]
        if any(k in normalized for k in close_terms) and intent == "SCREEN_OFF":
            out["intent"]     = "WINDOW_CLOSE"
            out["params"]     = {"query": ""}
            out["confidence"] = 0.88

        # [Bug6] REPEAT_LAST confondu avec MEMORY_SHOW
        repeat_triggers = ["repete", "rejoue", "refais", "encore une fois",
                           "meme chose", "derniere commande", "last command", "recommence"]
        if intent == "MEMORY_SHOW" and any(k in normalized for k in repeat_triggers):
            out["intent"]     = "REPEAT_LAST"
            out["params"]     = {}
            out["confidence"] = 0.92

        return out

    # ──────────────────────────────────────────────────────────────────────────
    #  _postprocess_result — [C3] MAINTENANT APPELÉ dans parse() et
    #                              parse_with_context()
    # ──────────────────────────────────────────────────────────────────────────

    def _postprocess_result(self, command: str, result: dict) -> dict:
        """
        Post-traitement final — dernière ligne de défense après _semantic_guard.

        [C3] Cette méthode était définie mais jamais appelée. Elle est maintenant
        invoquée systématiquement à la fin de parse() et parse_with_context(),
        que la source soit "groq" ou "fallback".

        Corrections appliquées :
        - AUDIO_PLAY / AUDIO_VOLUME_SET / UNKNOWN / APP_OPEN + "luminosité"
          → SCREEN_BRIGHTNESS (indépendamment de la confiance Groq)
        - SCREEN_OFF + verbe de fermeture → WINDOW_CLOSE
        """
        out    = dict(result or {})
        lower  = self._normalize_text((command or "").lower())
        intent = out.get("intent", "UNKNOWN")

        # Correction prioritaire : luminosité (indépendante de la confiance)
        if any(k in lower for k in ["luminosite", "luminosity", "brightness", "eclairage"]):
            if intent in ("AUDIO_PLAY", "AUDIO_VOLUME_SET", "UNKNOWN", "APP_OPEN"):
                out["intent"]     = "SCREEN_BRIGHTNESS"
                out["params"]     = {"level": self._extract_number(command, 70)}
                out["confidence"] = 0.92
                return out

        # Correction secondaire : "ferme/referme" → WINDOW_CLOSE
        if any(k in lower for k in ["ferme", "referme", "close", "quitte"]) and intent == "SCREEN_OFF":
            out["intent"]     = "WINDOW_CLOSE"
            out["params"]     = {"query": ""}
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

    def _extract_target(self, command: str, keywords: list) -> str:
        return self._extract_after(command, keywords)

    def _extract_open_target_params(self, text: str, base_params: dict | None = None) -> dict:
        params      = dict(base_params or {})
        lower       = text.lower().strip()
        target_type = params.get("target_type", "any")
        if any(token in lower for token in ["dossier", "répertoire", "repertoire"]):
            target_type = "directory"
        elif any(token in lower for token in ["fichier", "document"]):
            target_type = "file"
        cleaned = re.sub(
            r"^(ouvre|ouvrir|open|lis|affiche)\s+(moi\s+)?(le|la|les)?\s*(fichier|dossier|document|répertoire|repertoire)?\s*",
            "", lower,
        ).strip().strip('"').strip("'")
        if cleaned:
            params["path"] = cleaned
        params["target_type"] = target_type
        return params

    def _extract_location_context(self, text: str) -> dict:
        params      = {}
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
        raw    = text.strip()
        quoted = re.search(r"wifi\s+[\"']([^\"']+)[\"']", raw, re.IGNORECASE)
        ssid   = quoted.group(1).strip() if quoted else ""
        if not ssid:
            m = re.search(r"(?:connecte(?: toi)? au wifi|wifi connect)\s+(.+)$", raw, re.IGNORECASE)
            if m:
                ssid = m.group(1).strip(" \"'")
        pwd   = ""
        m_pwd = re.search(r"(?:mot de passe|password|mdp)\s*[:=]?\s*([\S]+)$", raw, re.IGNORECASE)
        if m_pwd:
            pwd = m_pwd.group(1).strip("\"'")
        params = {}  
        if ssid:
            params["ssid"] = ssid
        if pwd:
            params["password"] = pwd
        return params

    @staticmethod
    def _extract_email_address(text: str) -> str:
        """
        Extrait une adresse email depuis le texte.
        Patterns: email@domain.com, to: email@domain.com, etc.
        """
        m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
        return m.group(0) if m else ""

    @staticmethod
    def _extract_quoted_text(text: str) -> str:
        """
        Extrait le texte entre guillemets (simple ou double).
        Patterns: "texte", 'texte'
        """
        m = re.search(r'["\']([^"\']*)["\']\'', text)
        return m.group(1) if m else ""
