"""
core/smart_context_injector.py — LEVEL 3: MEMORY-AWARE PROMPT
==============================================================

Injecte contexte pertinent UNIQUEMENT dans le LLM fallback.
Réduit le prompt de 1200 → 400 tokens.

Prompt fallback ultra-minimal avec 12 règles essentielles.
"""

from typing import Optional
from config.logger import get_logger

logger = get_logger(__name__)


class SmartContextInjector:
    """Injecte intelligemment le contexte minimal."""
    
    def build_minimal_system_prompt(self, user_context: str = "") -> str:
        """
        Construit le prompt système MINIMAL (400 tokens).
        
        S'utilise UNIQUEMENT comme fallback LLM - 90% des commandes
        sont gérées par Router + SemanticRouter (0 tokens).
        """
        
        context_block = ""
        if user_context:
            context_block = f"\nCONTEXTE: {user_context[:200]}"

        return f"""Tu es JARVIS, assistant PC. JSON seulement.

RULES:
1. APP_OPEN: lancer/ouvrir app (ouvre Chrome) 
2. FILE_OPEN: ouvrir fichier
3. FILE_SEARCH: chercher/localiser fichier 
4. BROWSER_SEARCH: recherche web
5. BROWSER_OPEN: ouvrir navigateur
6. MUSIC_PLAY: jouer musique
7. AUDIO_VOLUME_SET: régler son
8. SCREEN_BRIGHTNESS: changer lumière
9. SYSTEM_SHUTDOWN: éteindre
10. WINDOW_CLOSE: fermer fenêtre
11. KNOWLEDGE_QA: question générale
12. MULTI_ACTION: plusieurs actions (et/puis){context_block}

OUTPUT: {{"intent": "NAME", "params": {{}}, "confidence": N}}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_injector = None

def get_context_injector() -> SmartContextInjector:
    """Retourne le context injector singleton."""
    global _injector
    if _injector is None:
        _injector = SmartContextInjector()
    return _injector
