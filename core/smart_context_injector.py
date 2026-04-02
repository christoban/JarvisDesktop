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
        Construit le prompt système MINIMAL (~200 tokens).
        
        S'utilise UNIQUEMENT comme fallback LLM - 90% des commandes
        sont gérées par Router + SemanticRouter (0 tokens).
        """
        
        context_block = ""
        if user_context:
            context_block = f" | CTX: {user_context[:100]}"

        return f"""JARVIS PC Assistant. JSON output only.

INTENTS: APP_OPEN, FILE_OPEN, FILE_SEARCH, BROWSER_SEARCH, MUSIC_PLAY, AUDIO_VOLUME_SET, SYSTEM_SHUTDOWN, WINDOW_CLOSE, MULTI_ACTION, KNOWLEDGE_QA{context_block}

OUTPUT: {{"intent":"NAME","params":{{}},"confidence":N}}
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
