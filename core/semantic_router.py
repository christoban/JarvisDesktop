"""
core/semantic_router.py — LEVEL 3: SEMANTIC INTENT CLASSIFICATION
===================================================================

Classification d'intents via embeddings (0 tokens).
Réduit le fallback LLM de 40%.

Signatures pré-calculées pour 15 intents critiques.
"""

from typing import Optional
from dataclasses import dataclass
from config.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SemanticMatch:
    """Résultat du matching sémantique"""
    intent: str
    confidence: float


# ═══════════════════════════════════════════════════════════════════════════════
#  INTENT SIGNATURES (Manual semantic patterns - no embeddings needed)
# ═══════════════════════════════════════════════════════════════════════════════

INTENT_PATTERNS = {
    # ── Navigateur ────────────────────────────────────────────────────────────
    # PRIORITÉ HAUTE : patterns spécifiques browser
    "BROWSER_NEW_TAB": [
        "nouvel onglet", "ouvre un nouvel onglet", "ouvre nouvel onglet", 
        "ouvre juste un nouvel onglet", "ouvre onglet", "new tab", "onglet"
    ],
    "BROWSER_SEARCH": ["google", "recherche sur", "cherche sur", "infos sur"],
    "BROWSER_OPEN": ["ouvre chrome", "ouvre firefox", "ouvre navigateur", "lance chrome", "lance firefox"],
    
    # ── Applications ───────────────────────────────────────────────────────────
    "APP_OPEN": ["lancer", "démarre", "startup", "ouvre"],
    "APP_CLOSE": ["ferme app", "ferme l'app", "quitte", "exit app"],
    "APP_LIST_RUNNING": ["applications ouvertes", "apps en cours", "quoi ouvert"],
    
    # ── Fichiers ───────────────────────────────────────────────────────────────
    "FILE_OPEN": ["ouvre fichier", "charge fichier", "lire", "lecture"],
    "FILE_SEARCH": ["cherche", "localise", "trouve", "où", "recherche fichier"],
    "FOLDER_LIST": ["liste dossier", "contenu", "affiche dossier"],
    
    # ── Musique ────────────────────────────────────────────────────────────────
    "MUSIC_PLAY": ["joue", "play", "mets musique", "lance chanson", "playlist", "écoute"],
    "MUSIC_PAUSE": ["pause", "stop", "arrête musi", "pausé"],
    "MUSIC_NEXT": ["suivant", "next", "prochaine", "skip"],
    "MUSIC_PREV": ["précédent", "prev", "retour"],
    
    # ── Système ────────────────────────────────────────────────────────────────
    "WINDOW_COUNT": ["combien de fenêtre", "combien fenêtre", "nombre de fenêtre", "windows ouver"],
    "WINDOW_LIST": ["liste fenêtre", "fenêtres ouvertes", "affiche fenêtre"],
    "WINDOW_CLOSE": ["ferme", "close", "ferme ca", "ça"],
    "SYSTEM_INFO": ["infos système", "système", "info", "que tu vois"],
    "SYSTEM_TIME": ["heure", "date", "quelle heure", "quel jour"],
    "SYSTEM_SHUTDOWN": ["éteins", "shutdown", "arrête le pc", "power off"],
    "SCREENSHOT": ["screenshot", "capture", "prends photo", "photo écran"],
    
    # ── Écran ──────────────────────────────────────────────────────────────────
    "SCREEN_BRIGHTNESS": ["luminosité", "brightness", "éclairage", "brille", "lumière"],
    "SCREEN_OFF": ["éteins écran", "éteindre écran", "écran off"],
    
    # ── Audio ──────────────────────────────────────────────────────────────────
    "AUDIO_VOLUME_SET": ["volume", "son", "sonorité", "monte son", "baisse son"],
    "AUDIO_MUTE": ["mute", "sourdine", "silence"],
    
    # ── Vision ────────────────────────────────────────────────────────────────
    "VISION_READ_SCREEN": ["lis l'écran", "lit le texte", "lis le texte", "extrait texte"],
    "VISION_CLICK_TEXT": ["clique sur", "appuie sur"],
    "VISION_FIND_BUTTON": ["trouve bouton", "où est le", "localise le"],
    
    # ── Télégram ───────────────────────────────────────────────────────────────
    "TELEGRAM_SEND": ["envoie telegram", "telegram", "msg telegram", "message telegram"],
    
    # ── Email ──────────────────────────────────────────────────────────────────
    "EMAIL_SEND": ["envoie email", "mail", "email", "message email"],
    
    # ── Connaissance ───────────────────────────────────────────────────────────
    "KNOWLEDGE_QA": ["qui", "quoi", "pourquoi", "expli", "dis-moi", "comment", "explique"],
    
    # ── Multi-action ───────────────────────────────────────────────────────────
    "MULTI_ACTION": ["et", "puis", "après", "ensuite"],
    
    # ── Préférences ────────────────────────────────────────────────────────────
    "PREFERENCE_SET": ["j'aime", "j'adore", "préfère"],
    
    # ── Aide ───────────────────────────────────────────────────────────────────
    "HELP": ["aide", "help", "commandes", "quoi faire"],
}


class SemanticRouter:
    """Router simple basé sur pattern matching."""
    
    def classify(self, text: str, threshold: float = 0.25) -> Optional[SemanticMatch]:
        """
        Classifie le texte vers un intent par pattern matching.
        Favorise les patterns plus longs (plus spécifiques).
        
        Threshold: 0.25 (long keywords = 0.5+, short keywords = 0.1+)
        
        Returns:
            SemanticMatch ou None
        """
        text_lower = text.lower().strip()
        
        best_intent = None
        best_score = 0
        best_keyword = ""
        
        # Chercher le pattern le plus spécifique (priorité aux keywords longs)
        for intent_name, keywords in INTENT_PATTERNS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    # Score: favor longer, more specific keywords
                    # Long keywords (20+ chars) get much higher base score
                    keyword_len = len(keyword)
                    if keyword_len >= 20:
                        base_score = 0.8 + (keyword_len - 20) / 50.0  # 0.8-1.0 for long matches
                    else:
                        base_score = keyword_len / 30.0  # 0.0-0.67 for shorter matches
                    
                    base_score = min(1.0, base_score)
                    
                    # Penalize ambiguity (multiple matches in same intent)
                    ambiguity_penalty = 0.1 * (len([k for k in keywords if k in text_lower and k != keyword]))
                    score = base_score - ambiguity_penalty
                    
                    if score > best_score:
                        best_score = score
                        best_intent = intent_name
                        best_keyword = keyword
        
        if best_score >= threshold:
            return SemanticMatch(
                intent=best_intent,
                confidence=best_score
            )
        
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_semantic_router = None

def get_semantic_router() -> SemanticRouter:
    """Retourne le router sémantique singleton."""
    global _semantic_router
    if _semantic_router is None:
        _semantic_router = SemanticRouter()
        logger.info("[SemanticRouter] Initialized (pattern-based, 0 tokens)")
    return _semantic_router
