"""
core/context_memory.py — LEVEL 4: CONTEXTUAL MEMORY ENGINE
===========================================================

Mémoire intelligente pour traiter les corrections et les modifications.

Permet à Jarvis de comprendre :
  "ouvre chrome"
  "non, nouvel onglet"
  "plutôt chrome incognito"

au lieu de retraiter chaque commande indépendamment.
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from config.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ContextFrame:
    """Frame conversationnel - dernière intention + params"""
    intent: str
    params: Dict[str, Any]
    timestamp: float
    command_text: str
    confidence: float = 1.0


class ContextMemory:
    """Moteur de mémoire contextuelle pour corrections."""
    
    def __init__(self):
        self.current_frame: Optional[ContextFrame] = None
        self.previous_frames: list[ContextFrame] = []
        self.max_history = 5
        
    def push_frame(self, intent: str, params: Dict[str, Any], command: str, confidence: float = 1.0):
        """Enregistre une nouvelle intention."""
        new_frame = ContextFrame(
            intent=intent,
            params=params.copy() if params else {},
            timestamp=datetime.now().timestamp(),
            command_text=command,
            confidence=confidence
        )
        
        if self.current_frame:
            self.previous_frames.insert(0, self.current_frame)
            if len(self.previous_frames) > self.max_history:
                self.previous_frames.pop()
        
        self.current_frame = new_frame
        logger.info(f"[ContextMemory] Push: {intent} (conf={confidence:.2f})")
    
    def get_current_frame(self) -> Optional[ContextFrame]:
        """Retourne le frame courant."""
        return self.current_frame
    
    def get_previous_frame(self, steps_back: int = 1) -> Optional[ContextFrame]:
        """Retourne un frame antérieur."""
        if steps_back <= 0 or steps_back > len(self.previous_frames):
            return None
        return self.previous_frames[steps_back - 1]
    
    def clear(self):
        """Vide la mémoire."""
        self.current_frame = None
        self.previous_frames = []
    
    def get_summary(self) -> str:
        """Résumé du contexte pour injection dans prompt."""
        if not self.current_frame:
            return ""
        
        summary = f"Dernière intention: {self.current_frame.intent}\n"
        if self.current_frame.params:
            params_str = ", ".join(f"{k}={v}" for k, v in self.current_frame.params.items())
            summary += f"Paramètres: {params_str}\n"
        summary += f"Commande: {self.current_frame.command_text}"
        
        return summary


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON GLOBAL
# ═══════════════════════════════════════════════════════════════════════════════

_context_memory = None

def get_context_memory() -> ContextMemory:
    """Retourne la mémoire contextuelle singleton."""
    global _context_memory
    if _context_memory is None:
        _context_memory = ContextMemory()
    return _context_memory
