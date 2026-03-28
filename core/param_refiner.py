"""
core/param_refiner.py — LEVEL 4: PARAMETER REFINEMENT
======================================================

Système pour affiner les params quand une action alternative est demandée.

Exemple:
  User: "ouvre chrome"
  Jarvis: APP_OPEN {"app_name": "chrome"}
  
  User: "non, nouvel onglet"
  Param Refiner: APP_OPEN {"app_name": "chrome", "action": "new_tab"}
"""

from typing import Dict, Any, Optional
from config.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  ACTION PATTERNS - Maps keywords to intent-specific actions
# ═══════════════════════════════════════════════════════════════════════════════

ACTION_REFINEMENTS = {
    "APP_OPEN": {
        # Keywords → action dict
        "nouvel onglet|onglet": {"action": "new_tab"},
        "nouvelle fenêtre|fenêtre": {"action": "new_window"},
        "fenêtre privée|incognito|privacy": {"action": "incognito"},
        "mode sombre|dark|sombre": {"action": "dark_mode"},
    },
    "BROWSER_SEARCH": {
        "youtube": {"engine": "youtube"},
        "github": {"engine": "github"},
        "stackoverflow": {"engine": "stackoverflow"},
    },
    "FILE_OPEN": {
        "nouvelle fenêtre": {"mode": "new_window"},
        "lecture seule|read-only": {"mode": "readonly"},
    },
    "MUSIC_PLAY": {
        "shuffle|aléatoire": {"shuffle": True},
        "répétition|repeat": {"repeat": True},
    },
}


def refine_params(intent: str, current_params: Dict[str, Any], command: str) -> Dict[str, Any]:
    """
    Affine les paramètres en fonction de mots-clés dans la commande.
    
    Args:
        intent: Intent Jarvis (ex: "APP_OPEN")
        current_params: Params courants (ex: {"app_name": "chrome"})
        command: Commande utilisateur (ex: "nouvel onglet")
        
    Returns:
        Params affinés (ex: {"app_name": "chrome", "action": "new_tab"})
    """
    
    if intent not in ACTION_REFINEMENTS:
        return current_params
    
    refined = current_params.copy()
    command_lower = command.lower()
    patterns = ACTION_REFINEMENTS[intent]
    
    for pattern_str, action_dict in patterns.items():
        # Split patterns par pipes (ex: "nouvel onglet|onglet")
        patterns_list = [p.strip() for p in pattern_str.split('|')]
        
        if any(p in command_lower for p in patterns_list):
            refined.update(action_dict)
            logger.info(f"[ParamRefiner] Refined {intent}: {action_dict}")
            return refined
    
    return refined


def extract_app_action(command: str) -> Optional[str]:
    """Extrait l'action spécifique pour APP_OPEN."""
    command_lower = command.lower()
    
    if "nouvel onglet" in command_lower or "new tab" in command_lower:
        return "new_tab"
    elif "nouvelle fenêtre" in command_lower or "new window" in command_lower:
        return "new_window"
    elif "incognito" in command_lower or "privé" in command_lower:
        return "incognito"
    
    return None
