"""
core/jarvis_memory.py — Mémoire persistante universelle de Jarvis
==================================================================

Jarvis se souvient de tout — entre les sessions, les redémarrages,
les jours et les semaines.

Ce qu'il mémorise automatiquement :
  - Tout ce qu'il a fait (fichiers, apps, navigateur, audio, système...)
  - Tes préférences (volume habituel, apps favorites, dossiers fréquents)
  - Le résumé de chaque conversation
  - Les faits importants que tu lui dis ("mon prénom est X", "je travaille sur Y")

Stockage : data/jarvis_memory.json (JSON lisible, modifiable)
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config.logger import get_logger

logger = get_logger(__name__)

MEMORY_FILE = Path(__file__).resolve().parent.parent / "data" / "jarvis_memory.json"
MAX_EVENTS_PER_CATEGORY = 50
MAX_FACTS = 100
MAX_EVENT_AGE_DAYS = 30   # ← oublie les événements de plus d'un mois

class JarvisMemory:
    """
    Mémoire persistante universelle de Jarvis.
    Survit aux redémarrages. Thread-safe.

    Structure du fichier JSON :
    {
        "last_session": "2026-03-18 14:32",
        "preferences": {
            "volume": 70,
            "default_browser": "chrome",
            "favorite_apps": ["chrome", "vscode"],
            ...
        },
        "facts": {
            "user_name": "Christian",
            "work_project": "JarvisWindows",
            ...
        },
        "events": {
            "file":    [...50 derniers...],
            "app":     [...],
            "browser": [...],
            "audio":   [...],
            ...
        },
        "stats": {
            "total_commands": 342,
            "most_used_app": "chrome",
            "most_searched": "Python",
            ...
        }
    }
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "last_session":  "",
            "preferences":   {},
            "facts":         {},
            "events":        defaultdict(list),
            "stats":         defaultdict(int),
            "session_count": 0,
        }
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self._mark_session_start()
        self._cleanup_old_events()
        logger.info(f"JarvisMemory initialisée — {self._count_events()} événements mémorisés.")

    # ══════════════════════════════════════════════════════════════════════════
    #  API PRINCIPALE
    # ══════════════════════════════════════════════════════════════════════════

    def remember_event(self, category: str, data: dict):
        """
        Mémorise un événement dans une catégorie.
        Appelé automatiquement après chaque action réussie.

        Catégories : file, folder, app, browser, audio,
                     document, system, network, screen, macro, search
        """
        if not category or not data:
            return

        event = {
            **{k: v for k, v in data.items() if not k.startswith("_")},
            "timestamp": int(time.time()),
            "datetime":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        with self._lock:
            events = self._data["events"]
            if category not in events:
                events[category] = []
            events[category].insert(0, event)
            # Garder seulement les N derniers
            events[category] = events[category][:MAX_EVENTS_PER_CATEGORY]

            # Supprimer les événements de plus d'un mois
            cutoff = time.time() - (MAX_EVENT_AGE_DAYS * 86400)
            events[category] = [
                e for e in events[category]
                if e.get("timestamp", 0) >= cutoff
            ]

            # Mettre à jour les préférences automatiquement
            self._update_preferences(category, data)

            # Mettre à jour les stats
            self._data["stats"]["total_commands"] = \
                self._data["stats"].get("total_commands", 0) + 1

        self._save_async()

    def remember_fact(self, key: str, value):
        """
        Mémorise un fait personnel important.

        Exemples :
          memory.remember_fact("user_name", "Christian")
          memory.remember_fact("work_project", "JarvisWindows")
          memory.remember_fact("preferred_language", "français")
        """
        if not key or value is None:
            return
        with self._lock:
            self._data["facts"][key] = {
                "value":    value,
                "learned":  datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            # Appliquer la limite MAX_FACTS
            if len(self._data["facts"]) > MAX_FACTS:
                # Supprimer le fait le plus ancien
                oldest_key = min(
                    self._data["facts"],
                    key=lambda k: (
                        self._data["facts"][k].get("learned", "")
                        if isinstance(self._data["facts"][k], dict)
                        else ""
                    )
                )
                del self._data["facts"][oldest_key]
                logger.debug(f"MAX_FACTS atteint — fait supprimé : {oldest_key}")
        self._save_async()
        logger.info(f"Jarvis a appris : {key} = {value}")

    def recall_last(self, category: str) -> dict:
        """
        Retourne le dernier événement d'une catégorie.

        Exemple :
          last_file = memory.recall_last("file")
          → {"path": "E:/films", "name": "films", ...}
        """
        with self._lock:
            events = self._data["events"].get(category, [])
            return dict(events[0]) if events else {}

    def recall_recent(self, category: str, max_age_minutes: int = 30) -> list:
        """
        Retourne les événements récents d'une catégorie.
        """
        cutoff = time.time() - (max_age_minutes * 60)
        with self._lock:
            events = self._data["events"].get(category, [])
            return [e for e in events if e.get("timestamp", 0) >= cutoff]

    def recall_fact(self, key: str):
        """Retourne un fait mémorisé."""
        with self._lock:
            fact = self._data["facts"].get(key)
            if fact is None:
                return None
            # Compatibilité : ancien format peut être une string brute
            if isinstance(fact, dict):
                return fact.get("value")
            return fact

    def get_preference(self, key: str, default=None):
        """Retourne une préférence mémorisée."""
        with self._lock:
            return self._data["preferences"].get(key, default)

    def get_context_summary(self, max_age_minutes: int = 60) -> str:
        """
        Génère un résumé du contexte récent pour Groq.
        Groq reçoit ça et comprend ce qui s'est passé récemment.
        """
        lines = []
        cutoff = time.time() - (max_age_minutes * 60)

        with self._lock:
            events = self._data["events"]
            prefs  = self._data["preferences"]
            facts  = self._data["facts"]

            # Faits personnels importants
            if facts:
                fact_lines = []
                for k, v in list(facts.items())[:5]:
                    val = v.get("value") if isinstance(v, dict) else v
                    fact_lines.append(f"{k}: {val}")
                if fact_lines:
                    lines.append("À propos de l'utilisateur : " + ", ".join(fact_lines))

            # Préférences
            if prefs.get("volume") is not None:
                lines.append(f"Volume habituel : {prefs['volume']}%")
            if prefs.get("default_browser"):
                lines.append(f"Navigateur favori : {prefs['default_browser']}")
            if prefs.get("favorite_apps"):
                lines.append(f"Apps favorites : {', '.join(prefs['favorite_apps'][:3])}")

            # Événements récents par catégorie
            for category, event_list in events.items():
                recent = [e for e in event_list if e.get("timestamp", 0) >= cutoff]
                if not recent:
                    continue
                last = recent[0]

                if category == "file":
                    lines.append(f"Dernier fichier : {last.get('name')} ({last.get('path', '')})")
                elif category == "folder":
                    lines.append(f"Dernier dossier : {last.get('name')} ({last.get('path', '')})")
                elif category == "app":
                    lines.append(f"Dernière app lancée : {last.get('name')}")
                elif category == "browser":
                    url = last.get('url') or last.get('query', '')
                    if url:
                        lines.append(f"Navigateur sur : {url}")
                elif category == "audio":
                    vol = last.get('volume')
                    track = last.get('track', '')
                    if vol:
                        lines.append(f"Volume : {vol}%")
                    if track:
                        lines.append(f"Musique jouée : {track}")
                elif category == "document":
                    lines.append(f"Document ouvert : {last.get('name')} ({last.get('path', '')})")
                elif category == "search":
                    lines.append(f"Dernière recherche : '{last.get('query')}' ({last.get('type', '')})")

        return "\n".join(lines) if lines else ""

    def get_full_summary(self) -> str:
        """
        Résumé complet de toute la mémoire — pour la commande
        "tu te souviens de ce qu'on a fait ?" ou "résume notre historique".
        """
        lines = ["Voici ce dont je me souviens :", ""]

        with self._lock:
            # Stats générales
            total = self._data["stats"].get("total_commands", 0)
            sessions = self._data["stats"].get("session_count", 0)
            last = self._data.get("last_session", "")
            lines.append(f"  {total} commandes exécutées au total, {sessions} session(s).")
            if last:
                lines.append(f"  Dernière session : {last}")
            lines.append("")

            # Faits personnels
            facts = self._data["facts"]
            if facts:
                lines.append("  Ce que je sais sur toi :")
                for k, v in facts.items():
                    val = v.get("value") if isinstance(v, dict) else v
                    lines.append(f"    → {k} : {val}")
                lines.append("")

            # Préférences apprises
            prefs = self._data["preferences"]
            if prefs:
                lines.append("  Tes préférences apprises :")
                if prefs.get("volume") is not None:
                    lines.append(f"    → Volume habituel : {prefs['volume']}%")
                if prefs.get("favorite_apps"):
                    lines.append(f"    → Apps favorites : {', '.join(prefs['favorite_apps'])}")
                if prefs.get("default_browser"):
                    lines.append(f"    → Navigateur : {prefs['default_browser']}")
                lines.append("")

            # Derniers événements par catégorie
            lines.append("  Dernières actions :")
            for category, event_list in self._data["events"].items():
                if event_list:
                    last_event = event_list[0]
                    dt = last_event.get("datetime", "")
                    if category == "file":
                        lines.append(f"    → [{dt}] Fichier : {last_event.get('name')}")
                    elif category == "app":
                        lines.append(f"    → [{dt}] App : {last_event.get('name')}")
                    elif category == "browser":
                        lines.append(f"    → [{dt}] Web : {last_event.get('url') or last_event.get('query', '')}")
                    elif category == "audio":
                        lines.append(f"    → [{dt}] Audio : volume {last_event.get('volume', '')}%")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════════
    #  APPRENTISSAGE AUTOMATIQUE DES PRÉFÉRENCES
    # ══════════════════════════════════════════════════════════════════════════

    def _update_preferences(self, category: str, data: dict):
        """
        Apprend automatiquement les préférences de l'utilisateur
        en analysant les patterns d'usage.
        """
        prefs = self._data["preferences"]

        # Volume le plus souvent utilisé
        if category == "audio" and data.get("volume") is not None:
            prefs["volume"] = data["volume"]

        # App la plus lancée
        if category == "app" and data.get("name"):
            app_name = data["name"].lower()
            counts = prefs.setdefault("app_counts", {})
            counts[app_name] = counts.get(app_name, 0) + 1
            # Top 3 apps favorites
            sorted_apps = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            prefs["favorite_apps"] = [a[0] for a in sorted_apps[:3]]

        # Navigateur par défaut
        if category == "browser" and data.get("name"):
            prefs["default_browser"] = data["name"]

        # Dossier de travail habituel
        if category == "folder" and data.get("path"):
            folder_counts = prefs.setdefault("folder_counts", {})
            path = data["path"]
            folder_counts[path] = folder_counts.get(path, 0) + 1
            sorted_folders = sorted(folder_counts.items(), key=lambda x: x[1], reverse=True)
            prefs["favorite_folders"] = [f[0] for f in sorted_folders[:3]]

    def _cleanup_old_events(self):
        """
        Supprime tous les événements de plus d'un mois au démarrage.
        Les faits personnels (facts) et préférences ne sont JAMAIS supprimés.
        """
        cutoff = time.time() - (MAX_EVENT_AGE_DAYS * 86400)
        deleted_total = 0

        with self._lock:
            for category in list(self._data["events"].keys()):
                before = len(self._data["events"][category])
                self._data["events"][category] = [
                    e for e in self._data["events"][category]
                    if e.get("timestamp", 0) >= cutoff
                ]
                deleted = before - len(self._data["events"][category])
                deleted_total += deleted

        if deleted_total > 0:
            logger.info(f"Mémoire nettoyée — {deleted_total} événement(s) de plus d'un mois supprimés.")
            self._save_sync()

    # ══════════════════════════════════════════════════════════════════════════
    #  DÉTECTION DE FAITS PERSONNELS
    # ══════════════════════════════════════════════════════════════════════════

    def extract_facts_from_command(self, command: str):
        """
        Détecte et mémorise automatiquement n'importe quel fait personnel
        ou préférence dans les phrases de l'utilisateur.

        Générique — fonctionne pour TOUT sans modifier le code :
                    "mon dossier musique est E:\\Musiques"
          "j'aime le lofi"
          "mon projet s'appelle JarvisWindows"
          "je préfère travailler le matin"
                    "mon dossier films est D:\\Films"
          "ma couleur préférée est le bleu"
          ... n'importe quoi
        """
        lower = command.lower().strip()

        # ── Patterns génériques — capturent TOUT ─────────────────────────────

        import re

        # Pattern 1 : "mon/ma/mes X est/sont/c'est Y"
        # Capture : "mon dossier musique est E:\\Musiques"
        #           "mon prénom est Christian"
        #           "ma ville c'est Yaoundé"
        match = re.search(
            r"(?:mon|ma|mes)\s+(.+?)\s+(?:est|sont|c'est|c est|se trouve|se trouvent)\s+(.+)",
            lower
        )
        if match:
            key   = match.group(1).strip()
            value = match.group(2).strip().strip(".,!?")
            # Récupérer la valeur originale (avec majuscules et chemins)
            original_value = command[command.lower().index(match.group(2)):].strip().strip(".,!?")
            if key and value and len(key) < 50 and len(value) < 200:
                self.remember_fact(key, original_value)
            return

        # Pattern 2 : "je m'appelle / je suis X"
        match = re.search(
            r"(?:je m'appelle|je suis|my name is|i am)\s+(.+)",
            lower
        )
        if match:
            value = match.group(1).strip().strip(".,!?")
            original = command[command.lower().index(match.group(1)):].strip().strip(".,!?")
            if value:
                self.remember_fact("prénom", original)
            return

        # Pattern 3 : "j'aime / j'adore / je préfère X"
        match = re.search(
            r"(?:j'aime|j adore|je préfère|je prefere|i love|i like|i prefer)\s+(.+)",
            lower
        )
        if match:
            value = match.group(1).strip().strip(".,!?")
            original = command[command.lower().index(match.group(1)):].strip().strip(".,!?")
            if value and len(value) < 100:
                # Clé basée sur le contexte précédent si possible
                key = "préférence générale"
                # Essayer de trouver un contexte (musique, film, sport...)
                context_words = [
                    "musique", "music", "film", "série", "sport",
                    "langue", "couleur", "nourriture", "food",
                ]
                for ctx in context_words:
                    if ctx in lower:
                        key = f"préférence {ctx}"
                        break
                self.remember_fact(key, original)
            return

        # Pattern 4 : "ne touche jamais à / ne modifie pas X"
        match = re.search(
            r"(?:ne touche jamais|ne modifie pas|ne supprime pas|ne change pas)\s+(?:à\s+)?(.+)",
            lower
        )
        if match:
            value = match.group(1).strip().strip(".,!?")
            original = command[command.lower().index(match.group(1)):].strip().strip(".,!?")
            if value:
                self.remember_fact(f"règle: ne pas toucher", original)
            return

        # Pattern 5 : "j'habite à / je travaille à / je suis basé à X"
        match = re.search(
            r"(?:j'habite|je vis|je travaille|je suis basé|i live|i work)\s+(?:à|à|at|in)?\s*(.+)",
            lower
        )
        if match:
            value = match.group(1).strip().strip(".,!?")
            original = command[command.lower().index(match.group(1)):].strip().strip(".,!?")
            if value and len(value) < 100:
                key = "lieu de travail" if "travaille" in lower else "ville"
                self.remember_fact(key, original)
            return

        # Pattern 6 : "souviens-toi que / retiens que / note que X"
        match = re.search(
            r"(?:souviens-toi que|retiens que|note que|remember that|n'oublie pas que)\s+(.+)",
            lower
        )
        if match:
            value = match.group(1).strip().strip(".,!?")
            original = command[command.lower().index(match.group(1)):].strip().strip(".,!?")
            if value and len(value) < 200:
                self.remember_fact(f"note", original)
            return

    # ══════════════════════════════════════════════════════════════════════════
    #  PERSISTANCE
    # ══════════════════════════════════════════════════════════════════════════

    def _mark_session_start(self):
        with self._lock:
            self._data["last_session"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._data["stats"]["session_count"] = \
                self._data["stats"].get("session_count", 0) + 1
        self._save_sync()

    def _load(self):
        if not MEMORY_FILE.exists():
            logger.info("Première utilisation — mémoire vierge.")
            return
        try:
            raw = MEMORY_FILE.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            with self._lock:
                self._data["last_session"]  = loaded.get("last_session", "")
                self._data["preferences"]   = loaded.get("preferences", {})
                self._data["facts"]         = loaded.get("facts", {})
                self._data["stats"]         = defaultdict(int, loaded.get("stats", {}))
                self._data["session_count"] = loaded.get("session_count", 0)
                # Reconstruire les événements
                events_raw = loaded.get("events", {})
                self._data["events"] = defaultdict(
                    list,
                    {k: v for k, v in events_raw.items()}
                )
            logger.info(f"Mémoire chargée — {self._count_events()} événements.")
        except Exception as e:
            logger.error(f"Chargement mémoire échoué: {e}")

    def _save_async(self):
        threading.Thread(target=self._save_sync, daemon=True).start()

    def _save_sync(self):
        with self._lock:
            payload = {
                "last_session":  self._data["last_session"],
                "preferences":   self._data["preferences"],
                "facts":         self._data["facts"],
                "events":        dict(self._data["events"]),
                "stats":         dict(self._data["stats"]),
                "session_count": self._data["stats"].get("session_count", 0),
                "_saved_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        try:
            MEMORY_FILE.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Sauvegarde mémoire échouée: {e}")

    def _count_events(self) -> int:
        return sum(len(v) for v in self._data["events"].values())