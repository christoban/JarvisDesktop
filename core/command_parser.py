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
import unicodedata
from config.logger import get_logger
from config.settings import (
    GROQ_API_KEY,
    GROQ_MODEL_NAME,
)

logger = get_logger(__name__)

# Longueur max d'un message assistant injecté dans l'historique Groq.
# Les réponses longues (listes de fichiers, rapport système) sont tronquées
# pour ne pas polluer le contexte JSON strict du parser.
_MAX_ASSISTANT_MSG_LEN = 200


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

    # ── Écran ─────────────────────────────────────────────────────────────────
    "SCREEN_CAPTURE":      {"desc": "Capture d'écran",                       "params": {}},
    "SCREENSHOT_TO_PHONE": {"desc": "Envoyer une capture au téléphone",      "params": {}},
    "SCREEN_BRIGHTNESS":   {"desc": "Régler la luminosité",                  "params": {"level": "int 0-100"}},
    "SCREEN_INFO":         {"desc": "Infos sur l'écran (résolution, etc.)",  "params": {}},
    "SCREEN_RECORD":       {"desc": "Enregistrer l'écran",                   "params": {}},

    # ── Historique / Macros ───────────────────────────────────────────────────
    "REPEAT_LAST":    {"desc": "Répéter la dernière commande",               "params": {}},
    "HISTORY_SHOW":   {"desc": "Afficher l'historique des commandes",        "params": {"count": "int optionnel"}},
    "HISTORY_CLEAR":  {"desc": "Effacer l'historique",                       "params": {}},
    "HISTORY_SEARCH": {"desc": "Chercher dans l'historique",                 "params": {"keyword": "str"}},
    "MACRO_RUN":      {"desc": "Lancer une macro nommée",                    "params": {"name": "str"}},
    "MACRO_LIST":     {"desc": "Lister les macros disponibles",              "params": {}},
    "MACRO_SAVE":     {"desc": "Créer/sauvegarder une macro",                "params": {"name": "str", "commands": "list"}},
    "MACRO_DELETE":   {"desc": "Supprimer une macro",                        "params": {"name": "str"}},

    "GREETING":     {"desc": "Salutation ou message d'accueil",              "params": {}},
    "MEMORY_SHOW":  {"desc": "Afficher ce dont Jarvis se souvient",          "params": {}},
    "PREFERENCE_SET": {
        "desc": "Mémoriser une préférence utilisateur (playlist de travail, app favorite, volume préféré, etc.)",
        "params": {"label": "str (ex: travail, codage, détente)", "value": "str (valeur associée)", "category": "str optionnel (music, app, volume...)"},
    },
    "KNOWLEDGE_QA": {"desc": "Question de connaissance générale, réponse directe sans action système", "params": {}},
    "INCOMPLETE":   {"desc": "Commande incomplète — paramètre manquant",     "params": {"missing": "str", "suggested_intent": "str"}},

    # ── Aide / Inconnu ────────────────────────────────────────────────────────
    "HELP":    {"desc": "Afficher l'aide et les commandes disponibles",      "params": {}},
    "UNKNOWN": {"desc": "Intention non reconnue",                            "params": {}},
}


# ══════════════════════════════════════════════════════════════════════════════
#  FEW-SHOT EXAMPLES
# ══════════════════════════════════════════════════════════════════════════════

FEW_SHOT_EXAMPLES = [
    ("mets chrome",                       '{"intent":"APP_OPEN","params":{"app_name":"chrome","args":[]},"confidence":0.99,"response_message":"Je lance Chrome tout de suite."}'),
    ("monte le son un peu",               '{"intent":"AUDIO_VOLUME_UP","params":{"step":10},"confidence":0.98,"response_message":"Volume monté de 10%."}'),
    ("mets le volume à 70",               '{"intent":"AUDIO_VOLUME_SET","params":{"level":70},"confidence":0.99,"response_message":"Volume réglé à 70%."}'),
    ("cherche les dernières nouvelles sur python", '{"intent":"BROWSER_SEARCH","params":{"query":"dernières nouvelles python"},"confidence":0.98,"response_message":"Je lance la recherche."}'),
    ("éteins l\'ordi dans 5 minutes",    '{"intent":"SYSTEM_SHUTDOWN","params":{"delay_seconds":300},"confidence":0.99,"response_message":"J\'éteins le PC dans 5 minutes."}'),
    ("coupe le son",                      '{"intent":"AUDIO_MUTE","params":{},"confidence":0.99,"response_message":"Son coupé."}'),
    ("ouvre mes documents",               '{"intent":"FOLDER_LIST","params":{"path":"Documents"},"confidence":0.97,"response_message":"J\'ouvre ton dossier Documents."}'),
    ("va sur youtube et cherche Python tutorial", '{"intent":"BROWSER_GO_TO_SITE","params":{"site":"youtube","query":"Python tutorial"},"confidence":0.99,"response_message":"Je cherche Python tutorial sur YouTube."}'),
    ("referme là",                        '{"intent":"WINDOW_CLOSE","params":{"query":""},"confidence":0.97,"response_message":"Je ferme la fenêtre."}'),
    ("joue la playlist chill",            '{"intent":"MUSIC_PLAYLIST_PLAY","params":{"name":"chill"},"confidence":0.98,"response_message":"Je lance la playlist chill."}'),
    ("musique suivante",                  '{"intent":"MUSIC_NEXT","params":{},"confidence":0.99,"response_message":"Piste suivante."}'),
    ("luminosité à 70%",                  '{"intent":"SCREEN_BRIGHTNESS","params":{"level":70},"confidence":0.99,"response_message":"Luminosité réglée à 70%."}'),
    ("mode nuit",                         '{"intent":"MACRO_RUN","params":{"name":"mode nuit"},"confidence":0.98,"response_message":"Je lance la macro mode nuit."}'),
    ("répète la dernière commande",       '{"intent":"REPEAT_LAST","params":{},"confidence":0.99,"response_message":"Je répète la dernière commande."}'),
    ("liste les réseaux wifi",            '{"intent":"WIFI_LIST","params":{},"confidence":0.99,"response_message":"Je cherche les réseaux Wi-Fi disponibles."}'),
    ("donne moi les infos sur mon système", '{"intent":"SYSTEM_INFO","params":{},"confidence":0.99,"response_message":"Je récupère les informations système."}'),
    ("il me reste combien d'espace disque", '{"intent":"SYSTEM_DISK","params":{},"confidence":0.99,"response_message":"Je vérifie l\'espace disque disponible."}'),
    # Few-shots musique complexes — ajouter dossier à playlist
    ("ajoute le dossier Musique a ma playlist chill",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"chill","folder":""},"confidence":0.99,"response_message":"Ajout du dossier Musique a la playlist chill."}'),
    ("ajoute tous les songs du dossier Musique a ma playlist",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"ma playlist","folder":""},"confidence":0.99,"response_message":"Ajout de tous les fichiers musicaux a la playlist."}'),
    ("mets toute ma musique dans la playlist favoris",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"favoris","folder":""},"confidence":0.98,"response_message":"Ajout de toute la bibliotheque a la playlist favoris."}'),
    ("ajoute la chanson shape of you a ma playlist chill",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"chill","query":"shape of you"},"confidence":0.99,"response_message":"Ajout de shape of you a la playlist chill."}'),
    # [Fix A] Distinguer fichier spécifique vs dossier entier
    ("ajoute le fichier Boku mixed.mp3 du bureau a la playlist de travail",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"playlist de travail","query":"Boku mixed.mp3","song":"Boku mixed.mp3","folder":"bureau"},"confidence":0.99,"response_message":"Ajout du fichier Boku mixed.mp3 a la playlist de travail."}'),
    ("ajoute le fichier Boku mixed qui est sur le bureau a la playlist coding hit",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"coding hit","query":"Boku mixed","song":"Boku mixed","folder":"bureau"},"confidence":0.99,"response_message":"Ajout de Boku mixed a la playlist coding hit."}'),
    ("ajoute moi le fichier son lofi qui est dans les telechargements a ma playlist",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"ma playlist","query":"son lofi","song":"son lofi","folder":"téléchargements"},"confidence":0.98,"response_message":"Ajout du fichier son lofi a la playlist."}'),
    ("ajoute le titre relaxing beats de mes documents a la playlist detente",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"detente","query":"relaxing beats","song":"relaxing beats","folder":"documents"},"confidence":0.98,"response_message":"Ajout de relaxing beats a la playlist detente."}'),
    ("cree une playlist travail et ajoute s y le fichier son.mp3 sur le bureau",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"travail","query":"son.mp3","song":"son.mp3","folder":"bureau"},"confidence":0.99,"response_message":"Playlist creee et fichier ajoute."}'),
    ("ajoute ce fichier mp3 a ma playlist",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"ma playlist","query":"","song":""},"confidence":0.92,"response_message":"Ajout du fichier a la playlist."}'),
    ("ajoute le morceau lofi.flac de mes telechargements a la playlist detente",
     '{"intent":"MUSIC_PLAYLIST_ADD_SONG","params":{"name":"detente","query":"lofi.flac","song":"lofi.flac","folder":"téléchargements"},"confidence":0.99,"response_message":"Ajout de lofi.flac a la playlist detente."}'),
    # [Fix P1] Patterns avec structure différente que Groq rate
    ("va dans le dossier Musique tu ajoutes a ma playlist tous les songs qui s y trouve",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"ma playlist","folder":""},"confidence":0.99,"response_message":"J ajoute tous les fichiers du dossier Musique a la playlist."}'),
    ("remplis ma playlist avec tous les morceaux du dossier Musique",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"ma playlist","folder":""},"confidence":0.99,"response_message":"Remplissage de la playlist avec tous les morceaux."}'),
    ("importe les musiques du dossier Musique dans ma playlist",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"ma playlist","folder":""},"confidence":0.99,"response_message":"Import de tous les fichiers musicaux dans la playlist."}'),
    ("je t ai dit d ajouter a cette playlist ma liste de musique dans le dossier Musique",
     '{"intent":"MUSIC_PLAYLIST_ADD_FOLDER","params":{"name":"ma playlist","folder":""},"confidence":0.99,"response_message":"J ajoute tous les fichiers du dossier Musique a la playlist."}'),
    # [Fix] play_playlist existante sans préciser le nom
    ("joue la",
     '{"intent":"MUSIC_PLAYLIST_PLAY","params":{"name":"ma playlist"},"confidence":0.90,"response_message":"Je lance la playlist."}'),
    ("joue alors cette playlist maintenant",
     '{"intent":"MUSIC_PLAYLIST_PLAY","params":{"name":"ma playlist"},"confidence":0.95,"response_message":"Je lance la playlist maintenant."}'),
    # PREFERENCE_SET — mémorisation préférences utilisateur
    ("j aime jouer ma playlist quand je code",
     '{"intent":"PREFERENCE_SET","params":{"label":"codage","value":"ma playlist","category":"music"},"confidence":0.97,"response_message":"Je retiens que ta playlist de codage est ma playlist."}'),
    ("mon son de travail c est ma playlist",
     '{"intent":"PREFERENCE_SET","params":{"label":"travail","value":"ma playlist","category":"music"},"confidence":0.98,"response_message":"Je note que ta musique de travail est ma playlist."}'),
    ("quand je passe en mode travail je joue ma playlist",
     '{"intent":"PREFERENCE_SET","params":{"label":"travail","value":"ma playlist","category":"music"},"confidence":0.97,"response_message":"Je mémorise ca comme ta playlist de travail."}'),
    ("retiens que mon volume de travail c est 60 pourcent",
     '{"intent":"PREFERENCE_SET","params":{"label":"travail","value":"60","category":"volume"},"confidence":0.98,"response_message":"Volume de travail enregistre a 60 pourcent."}'),
    ("associe cette playlist au mode detente",
     '{"intent":"PREFERENCE_SET","params":{"label":"detente","value":"ma playlist","category":"music"},"confidence":0.97,"response_message":"Playlist associee au mode detente."}'),
    # [Bug6] Few-shots explicites pour REPEAT_LAST — évite la confusion avec MEMORY_SHOW
    ("répète la dernière commande", '{"intent":"REPEAT_LAST","params":{},"confidence":0.99,"response_message":"Je répète la dernière commande."}'),
    ("rejoue la commande précédente", '{"intent":"REPEAT_LAST","params":{},"confidence":0.99,"response_message":"Je relance la commande précédente."}'),
    ("refais la même chose", '{"intent":"REPEAT_LAST","params":{},"confidence":0.99,"response_message":"Je relance la même action."}'),
    ("encore une fois", '{"intent":"REPEAT_LAST","params":{},"confidence":0.99,"response_message":"Je répète."}'),
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
                    result           = self._call_groq_ai(command, history=[])
                    result           = self._semantic_guard(command, result)
                    # [C3] _postprocess_result appelé en dernière ligne de défense
                    result           = self._postprocess_result(command, result)
                    result["source"] = "groq"
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

        result           = self._semantic_guard(command, self._fallback_keywords(command))
        # [C3] _postprocess_result appelé aussi sur le fallback
        result           = self._postprocess_result(command, result)
        result["source"] = "fallback"
        return result

    def parse_with_context(self, command: str, history: list = None, retries: int = 2) -> dict:
        """Parse avec l'historique de conversation."""
        command = command.strip()
        if not command:
            return self._unknown(command, "Commande vide.")

        if self._can_use_groq():
            for attempt in range(retries + 1):
                try:
                    result           = self._call_groq_ai(command, history=history or [])
                    result           = self._semantic_guard(command, result)
                    # [C3] _postprocess_result appelé en dernière ligne de défense
                    result           = self._postprocess_result(command, result)
                    result["source"] = "groq"
                    logger.info(f"Intent: {result['intent']} (conf={result['confidence']:.2f}, src=groq+ctx)")
                    return result
                except Exception as e:
                    logger.warning(f"Groq+ctx tentative {attempt + 1} échouée : {e}")
                    self._set_groq_cooldown_from_error(e)
                    if "json_validate_failed" in str(e):
                        break
                    if time.time() < self._groq_cooldown_until:
                        break
                    if attempt < retries:
                        time.sleep(0.5 * (attempt + 1))

        result           = self._semantic_guard(command, self._fallback_keywords(command))
        # [C3] _postprocess_result appelé aussi sur le fallback
        result           = self._postprocess_result(command, result)
        result["source"] = "fallback"
        return result

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
        for user_msg, assistant_msg in FEW_SHOT_EXAMPLES[:8]:
            messages.append({"role": "user",      "content": user_msg})
            messages.append({"role": "assistant", "content": assistant_msg})

        # [C2] Historique : inclure user ET assistant, pas seulement user
        if history_to_use:
            for msg in history_to_use[-12:]:
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
        response = self.client.chat.completions.create(
            model=GROQ_MODEL_NAME,
            messages=messages,
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        raw_json = response.choices[0].message.content.strip()
        return self._parse_json_response(raw_json, command)

    # ──────────────────────────────────────────────────────────────────────────
    #  PROMPT SYSTÈME
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
MÉMOIRE :
{memory_summary}

Utilise cette mémoire pour résoudre "le", "ça", "celui-là".
"""

        return f"""Tu es JARVIS, l'assistant IA de contrôle PC. Tu es conversationnel et intelligent.
Tu comprends le français, l'anglais, les tournures naturelles et les références contextuelles.

INTENTIONS DISPONIBLES :
{intents_block}{memory_block}

RÈGLES :
1. Lis TOUTE la phrase — ne te base jamais sur un seul mot-clé.
2. "luminosité" / "luminos" → SCREEN_BRIGHTNESS, jamais AUDIO_PLAY.
3. "ferme ça/là/cette fenêtre" → WINDOW_CLOSE, jamais SCREEN_OFF.
4. "mode nuit/travail/cinéma" → MACRO_RUN avec le nom de la macro.
5. "répète/rejoue" → REPEAT_LAST.
6. "joue musique X" / "lecture X" → MUSIC_PLAY, pas AUDIO_PLAY.
5b. Déclarations de préférence ("j'aime X quand je Y", "mon son de X c'est Y",
    "quand je code je joue X", "retiens que mon X c'est Y", "associe X à Y",
    "j'ai une musique de X que j'aime") → PREFERENCE_SET avec label=contexte, value=ressource.
7. "joue playlist X" → MUSIC_PLAYLIST_PLAY.
8. "ajoute le dossier/tous les songs/toute ma musique/tous les fichiers/mets ma musique dans... à la playlist X" → MUSIC_PLAYLIST_ADD_FOLDER (name=playlist, folder="").
   "va dans le dossier Musique tu ajoutes à ma playlist" → aussi MUSIC_PLAYLIST_ADD_FOLDER.
   "je t'ai dit d'ajouter ma musique à la playlist" → aussi MUSIC_PLAYLIST_ADD_FOLDER.
   JAMAIS MUSIC_PLAYLIST_CREATE quand l'intention est d'ajouter des fichiers existants.
9. "ajoute la chanson X à la playlist Y" → MUSIC_PLAYLIST_ADD_SONG (query=X).
   "ajoute le fichier X.mp3 [du bureau/des téléchargements] à la playlist Y"
   → MUSIC_PLAYLIST_ADD_SONG avec query=X, song=X, folder=lieu.
   RÈGLE CRITIQUE : si la commande mentionne UN SEUL fichier (avec ou sans extension)
   ou "le fichier", "ce fichier", "ce morceau", "cette chanson", "qui est sur le bureau",
   "dans les téléchargements", "dans mes documents" → TOUJOURS MUSIC_PLAYLIST_ADD_SONG.
   TOUJOURS inclure folder dans params si un lieu est mentionné
   (bureau→"bureau", téléchargements→"téléchargements", documents→"documents", etc.).
   TOUJOURS inclure song=query (copie du champ query).
   Si elle mentionne "le dossier entier", "tous les fichiers", "tous mes songs" → MUSIC_PLAYLIST_ADD_FOLDER.
10. "joue la" après avoir parlé d'une playlist → MUSIC_PLAYLIST_PLAY avec le nom du contexte.
11. "renomme la playlist X en Y" → MUSIC_PLAYLIST_RENAME (old_name=X, new_name=Y).
12. "duplique la playlist X en Y" → MUSIC_PLAYLIST_DUPLICATE (source=X, target=Y).
13. "exporte la playlist X" → MUSIC_PLAYLIST_EXPORT (name=X, format=m3u par défaut).
14. "importe la playlist depuis <fichier>" → MUSIC_PLAYLIST_IMPORT (path=<fichier>, name optionnel).
15. "fusionne la playlist X avec Y [dans Z]" → MUSIC_PLAYLIST_MERGE (source=X, target=Y, output=Z optionnel).
16. "déplace la chanson X dans la playlist Y en position N" → MUSIC_PLAYLIST_MOVE_SONG (name=Y, query=X, to_index=N).
17. "ajoute X à la file d'attente" → MUSIC_QUEUE_ADD (query=X).
18. "ajoute la playlist X à la file d'attente" → MUSIC_QUEUE_ADD_PLAYLIST (name=X).
19. "liste/vide/lance la file d'attente" → MUSIC_QUEUE_LIST / MUSIC_QUEUE_CLEAR / MUSIC_QUEUE_PLAY.
20. Salutations → GREETING. Questions capacités → HELP.
21. Phrases multi-actions → retenir l'ACTION FINALE.
22. Si vraiment incompréhensible → UNKNOWN.
23. Si la requête est une question de connaissance générale (définition, explication, comparaison,
    culture générale, raisonnement) et ne demande pas d'action sur le PC → KNOWLEDGE_QA.
24. Pour KNOWLEDGE_QA, donne la réponse directement dans `response_message`.
25. SORTIE STRICTE : retourne UNIQUEMENT un objet JSON avec EXACTEMENT ces clés
    `intent`, `params`, `confidence`, `response_message`.
26. INTERDIT de retourner des objets métier (`cpu`, `ram`, `disk`, `system_info`, etc.).
27. `params` doit être un objet JSON ({{}} si vide), jamais du texte.
28. `confidence` doit être un nombre entre 0 et 1.
29. Si la demande concerne l'état/infos du système PC → `intent` = `SYSTEM_INFO`.

FORMAT (JSON uniquement) :
{{"intent": "NOM", "params": {{}}, "confidence": 0.95, "response_message": "Réponse naturelle."}}
"""

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
        if any(k in lower for k in ["joue playlist", "lance playlist", "joue la playlist", "play playlist"]):
            name = self._extract_after(lower, ["joue playlist ", "lance playlist ", "joue la playlist ", "play playlist "])
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
        if any(k in lower for k in ["ouvre", "lance", "demarre", "mets", "start"]) and "dossier" not in lower and "fichier" not in lower:
            app_name = self._extract_after(lower, ["ouvre ", "lance ", "demarre ", "mets ", "start "])
            if app_name and not any(c in app_name for c in ["/", "\\"]):
                return {"intent": "APP_OPEN", "params": {"app_name": app_name, "args": []}, "confidence": 0.75}
        if any(k in lower for k in ["ferme", "referme", "close", "quitte", "quit"]):
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
        if any(k in lower for k in ["ferme l onglet", "ferme cet onglet", "close tab", "ferme l'onglet actif"]):
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
        if any(k in lower for k in ["cherche", "trouve", "search"]):
            query = self._extract_after(lower, ["cherche ", "trouve ", "search "])
            if any(k in lower for k in ["sur le web", "sur google", "google", "internet",
                                         "en ligne", "sur bing", "sur duckduckgo", "tutorial", "tutoriel"]):
                return {"intent": "BROWSER_SEARCH", "params": {"query": query}, "confidence": 0.85}
            return {"intent": "FILE_SEARCH", "params": {"query": query}, "confidence": 0.7}
        if any(k in lower for k in ["recherche", "google", "web", "internet"]):
            query = self._extract_after(lower, ["recherche ", "google ", "cherche sur internet "])
            return {"intent": "BROWSER_SEARCH", "params": {"query": query}, "confidence": 0.75}
        if any(k in lower for k in ["youtube"]):
            query = self._extract_after(lower, ["youtube ", "sur youtube "])
            return {"intent": "BROWSER_SEARCH_YOUTUBE", "params": {"query": query}, "confidence": 0.85}

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