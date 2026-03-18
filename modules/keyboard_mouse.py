"""
keyboard_mouse.py — Contrôle clavier et souris
Déplacer la souris, cliquer, taper du texte, raccourcis clavier.

⚠️  IMPLÉMENTATION COMPLÈTE : Semaine 6 (si besoin avancé)
"""

import pyautogui
from config.logger import get_logger
logger = get_logger(__name__)

# Sécurité pyautogui : pause entre les actions
pyautogui.PAUSE = 0.1
# Si la souris va dans le coin supérieur gauche → arrêt d'urgence
pyautogui.FAILSAFE = True


class KeyboardMouse:

    def type_text(self, text: str, interval: float = 0.05) -> dict:
        """Tape un texte dans la fenêtre active."""
        logger.info(f"[STUB] type_text : '{text[:30]}...'")
        return {"success": False, "message": "[STUB] À implémenter", "data": None}

    def click(self, x: int = None, y: int = None) -> dict:
        """Clic gauche à une position (ou position actuelle)."""
        logger.info(f"[STUB] click ({x}, {y})")
        return {"success": False, "message": "[STUB] À implémenter", "data": None}

    def double_click(self, x: int = None, y: int = None) -> dict:
        logger.info(f"[STUB] double_click ({x}, {y})")
        return {"success": False, "message": "[STUB] À implémenter", "data": None}

    def move_mouse(self, x: int, y: int, duration: float = 0.3) -> dict:
        logger.info(f"[STUB] move_mouse ({x}, {y})")
        return {"success": False, "message": "[STUB] À implémenter", "data": None}

    def hotkey(self, *keys) -> dict:
        """Appuie sur un raccourci clavier (ex: 'ctrl', 'c')."""
        logger.info(f"[STUB] hotkey : {keys}")
        return {"success": False, "message": "[STUB] À implémenter", "data": None}

    def copy(self) -> dict:
        return self.hotkey("ctrl", "c")

    def paste(self) -> dict:
        return self.hotkey("ctrl", "v")