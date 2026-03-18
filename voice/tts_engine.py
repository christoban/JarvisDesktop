"""voice/tts_engine.py

Simple Text-to-Speech engine used by `jarvis_bridge.py`.
Expected API:
- class TTSEngine
- property: backend
- method: speak_result(result: dict, command: str)
"""

from __future__ import annotations

import threading
from typing import Optional, TYPE_CHECKING

from config.logger import get_logger

if TYPE_CHECKING:
    from pyttsx3.engine import Engine

logger = get_logger(__name__)

try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except ImportError:
    pyttsx3 = None
    _PYTTSX3_AVAILABLE = False


class TTSEngine:
    """TTS backend with `pyttsx3` fallback to silent mode."""

    def __init__(self, language: str = "fr"):
        self.language = language
        self._lock = threading.Lock()
        self._engine: Optional["Engine"] = None

        if _PYTTSX3_AVAILABLE:
            try:
                self._engine = pyttsx3.init()
                # Keep speech responsive for command feedback.
                self._engine.setProperty("rate", 175)
                self.backend = "pyttsx3"
                logger.info("TTS initialized with pyttsx3 backend")
            except Exception as e:
                self.backend = "silent"
                logger.warning(f"TTS pyttsx3 init failed: {e}")
        else:
            self.backend = "silent"
            logger.warning("TTS disabled: pyttsx3 not installed")

    def speak(self, text: str) -> bool:
        """Speak text synchronously. Returns True if spoken."""
        text = (text or "").strip()
        if not text or self.backend == "silent" or self._engine is None:
            return False

        try:
            with self._lock:
                self._engine.say(text)
                self._engine.runAndWait()
            return True
        except Exception as e:
            logger.error(f"TTS speak error: {e}")
            return False

    def speak_result(self, result: dict, command: str = "") -> bool:
        """Speak the standard result payload returned by Agent.handle_command."""
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or result.get("result") or "")

        if not message:
            message = "Commande terminee." if command else "Execution terminee."

        return self.speak(message)

    def health_check(self) -> dict:
        """Health payload for diagnostics screens/tests."""
        if self.backend == "silent":
            return {
                "available": False,
                "backend": "silent",
                "message": "TTS backend unavailable",
            }

        return {
            "available": True,
            "backend": self.backend,
            "message": f"TTS operational ({self.backend})",
            "language": self.language,
        }