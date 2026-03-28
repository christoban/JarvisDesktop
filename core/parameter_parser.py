"""
core/parameter_parser.py — Extraction intelligente des paramètres
==================================================================

Sépare l'extraction des paramètres de l'intent detection.
Transforme une commande brute en params structurés.

Exemple :
    "joue ma playlist gospel hit"
    → intent: MUSIC_PLAY
    → params: {type: "playlist", name: "gospel hit"}
"""

import re
from typing import Dict, Any, Optional
from config.logger import get_logger

logger = get_logger(__name__)


class ParameterParser:
    """Parseur intelligent de paramètres."""

    def parse_music_params(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Parse les paramètres musique.
        
        Exemples :
            "joue ma playlist gospel hit" → {type: "playlist", name: "gospel hit"}
            "joue john legend" → {type: "search", query: "john legend"}
            "play clair de lune" → {type: "search", query: "clair de lune"}
        """
        text_lower = text.lower().strip()

        # ── Playlist ───────────────────────────────────────────────────────────
        if "playlist" in text_lower:
            # Extraire le nom après "playlist"
            match = re.search(r"playlist\s+(.+?)(?:\s+sur|$)", text_lower)
            if match:
                name = match.group(1).strip()
                return {
                    "type": "playlist",
                    "name": name,
                    "query": name  # Pour compatibilité
                }

        # ── Artiste / chanson (défaut) ─────────────────────────────────────────
        keywords_to_remove = ["joue", "play", "mets", "lance", "écoute", "musique", "chanson"]
        query = text_lower
        for kw in keywords_to_remove:
            query = query.replace(kw, "").strip()

        return {
            "type": "search",
            "query": query if query else "music"
        }

    def parse_app_params(self, text: str) -> Dict[str, Any]:
        """
        Parse les paramètres application.
        
        Exemples :
            "ouvre chrome" → {app_name: "chrome"}
            "lance notepad" → {app_name: "notepad"}
        """
        text_lower = text.lower().strip()

        # Extraire le nom de l'appli après le verbe
        keywords = ["ouvre", "lance", "démarre", "lancer", "ouvrir", "startup"]
        app_name = text_lower

        for kw in keywords:
            if kw in app_name:
                app_name = app_name.replace(kw, "").strip()
                break

        # Nettoyer les articles
        for article in ["le ", "la ", "les ", "un ", "une ", "des ", "d'"]:
            if app_name.startswith(article):
                app_name = app_name[len(article):]

        return {
            "app_name": app_name if app_name else "unknown",
            "args": []
        }

    def parse_browser_params(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Parse les paramètres navigateur.
        
        Exemples :
            "recherche python tutorial" → {query: "python tutorial"}
            "ouvre google" → {url: "google.com"}
            "nouvel onglet" → {} (new tab)
        """
        text_lower = text.lower().strip()

        # ── Nouveau tab ────────────────────────────────────────────────────────
        if intent == "BROWSER_NEW_TAB":
            return {}

        # ── Recherche ──────────────────────────────────────────────────────────
        keywords_to_remove = ["recherche", "google", "cherche", "infos", "sur", "à propos de"]
        query = text_lower
        for kw in keywords_to_remove:
            query = query.replace(kw, "").strip()

        return {
            "query": query if query else ""
        }

    def parse_file_params(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Parse les paramètres fichiers.
        
        Exemples :
            "ouvre mon document" → {query: "mon document"}
            "cherche image.jpg" → {query: "image.jpg"}
        """
        text_lower = text.lower().strip()

        # Extraire ce qui vient après le verbe
        keywords = ["ouvre", "cherche", "localise", "trouve", "charge", "lit"]
        query = text_lower

        for kw in keywords:
            if kw in query:
                query = query.split(kw, 1)[-1].strip()
                break

        return {
            "query": query if query else ""
        }

    def parse_window_params(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Parse les paramètres fenêtre.
        
        Exemples :
            "combien de fenêtres ouvertes" → {} (just count)
            "ferme chrome" → {target: "chrome"}
        """
        text_lower = text.lower().strip()

        # ── Count / list ───────────────────────────────────────────────────────
        if intent in ("WINDOW_COUNT", "WINDOW_LIST"):
            return {}

        # ── Close ──────────────────────────────────────────────────────────────
        if intent == "WINDOW_CLOSE":
            query = text_lower
            for kw in ["ferme", "close", "quitte"]:
                if kw in query:
                    query = query.replace(kw, "").strip()
                    break
            return {"query": query if query else "current"}

        return {}

    def parse_volume_params(self, text: str) -> Dict[str, Any]:
        """
        Parse les paramètres volume.
        
        Exemples :
            "monte le volume à 50" → {level: 50}
            "mute" → {action: "mute"}
            "baisse le son" → {direction: "down"}
        """
        text_lower = text.lower().strip()

        # ── Mute ───────────────────────────────────────────────────────────────
        if "mute" in text_lower or "sourdine" in text_lower:
            return {"action": "mute"}

        # ── Niveau spécifique ──────────────────────────────────────────────────
        match = re.search(r"(\d+)", text_lower)
        if match:
            level = int(match.group(1))
            return {"level": min(max(level, 0), 100)}  # Clamp 0-100

        # ── Direction ──────────────────────────────────────────────────────────
        if "monte" in text_lower or "augmente" in text_lower or "plus" in text_lower:
            return {"direction": "up"}
        elif "baisse" in text_lower or "diminue" in text_lower or "moins" in text_lower:
            return {"direction": "down"}

        return {}

    def parse_brightness_params(self, text: str) -> Dict[str, Any]:
        """
        Parse les paramètres luminosité.
        
        Exemples :
            "luminosité 70" → {level: 70}
            "plus clair" → {direction: "up"}
        """
        text_lower = text.lower().strip()

        # ── Niveau spécifique ──────────────────────────────────────────────────
        match = re.search(r"(\d+)", text_lower)
        if match:
            level = int(match.group(1))
            return {"level": min(max(level, 0), 100)}

        # ── Direction ──────────────────────────────────────────────────────────
        if "plus" in text_lower or "augmente" in text_lower or "clair" in text_lower:
            return {"direction": "up"}
        elif "moins" in text_lower or "diminue" in text_lower or "sombre" in text_lower:
            return {"direction": "down"}

        return {}

    def parse(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Parse les paramètres selon l'intent.
        
        Args:
            text: commande brute
            intent: intent détecté
            
        Returns:
            dict des paramètres structurés
        """
        if not text or not intent:
            return {}

        # Router vers le parseur spécialisé
        if intent.startswith("MUSIC_"):
            return self.parse_music_params(text, intent)
        elif intent.startswith("APP_"):
            return self.parse_app_params(text)
        elif intent.startswith("BROWSER_"):
            return self.parse_browser_params(text, intent)
        elif intent.startswith("FILE_") or intent == "FOLDER_LIST":
            return self.parse_file_params(text, intent)
        elif intent.startswith("WINDOW_"):
            return self.parse_window_params(text, intent)
        elif intent == "AUDIO_VOLUME_SET":
            return self.parse_volume_params(text)
        elif intent == "SCREEN_BRIGHTNESS":
            return self.parse_brightness_params(text)

        return {}


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_parameter_parser = None


def get_parameter_parser() -> ParameterParser:
    """Retourne le parser de paramètres singleton."""
    global _parameter_parser
    if _parameter_parser is None:
        _parameter_parser = ParameterParser()
        logger.info("[ParameterParser] Initialized (intelligent extraction)")
    return _parameter_parser
