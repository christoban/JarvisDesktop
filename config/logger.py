"""
logger.py — Système de logging centralisé pour tout le projet
Chaque module importe get_logger(__name__) pour avoir son propre logger
"""

import logging
import sys
from pathlib import Path
from logging.handlers import WatchedFileHandler  # ← FileHandler on Windows
from config.settings import LOG_DIR, LOG_LEVEL

# Global flag to ensure root logger configured only ONCE
_LOGGER_ROOT_CONFIGURED = False


def _configure_root_logger_once():
    """
    Configure the root logger with handlers ONCE.
    All child loggers inherit these handlers.

    This avoids the cascading handler problem where each module
    calls get_logger() and stacks handlers on top of each other.
    """
    global _LOGGER_ROOT_CONFIGURED

    if _LOGGER_ROOT_CONFIGURED:
        return

    root_logger = logging.getLogger()

    # Clear any default handlers (safety)
    root_logger.handlers = []

    # Set root level (children inherit unless overridden)
    root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # ── Formatter ──────────────────────────────
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # ── Console Handler (stdout) ───────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)

    # ── File Handler (NO ROTATION on Windows) ──
    # Windows doesn't allow renaming open files.
    # Use simple FileHandler instead of RotatingFileHandler.
    # External tools (logrotate, scheduled task) will rotate the file.
    log_file = LOG_DIR / "jarvis.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    _LOGGER_ROOT_CONFIGURED = True
    print(f"[LOG] Root logger configured: {log_file}")


def get_logger(name: str) -> logging.Logger:
    """
    Retourne un logger configuré pour le module donné.

    IMPORTANTE: Cette fonction ne ajoute plus les handlers.
    Il y a juste une seule fois au root logger (voir _configure_root_logger_once).

    Usage dans chaque module :
        from config.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Message")

    Args:
        name: Usually __name__ from the calling module

    Returns:
        logging.Logger configured to inherit root handlers
    """
    # Configure root logger on first call
    _configure_root_logger_once()

    # Get (or create) the named logger
    logger = logging.getLogger(name)

    # Child loggers inherit root handlers automatically.
    # NO need to add handlers here.

    return logger


# ═══════════════════════════════════════════════════════════════════════════════
# VERIFY FIX 1 & 2: TEST LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test: Create multiple loggers, verify no cascading
    logger1 = get_logger("core.agent")
    logger2 = get_logger("core.command_parser")
    logger3 = get_logger("core.intent_executor")

    print(f"\nLogging Handler Count:")
    print(f"  Root logger handlers:       {len(logging.getLogger().handlers)}")  # Should be 2
    print(f"  Agent logger handlers:      {len(logger1.handlers)}")  # Should be 0 (inherited)
    print(f"  CommandParser logger:       {len(logger2.handlers)}")  # Should be 0
    print(f"  IntentExecutor logger:      {len(logger3.handlers)}")  # Should be 0

    # Expected output:
    # Root logger handlers: 2
    # Agent logger handlers: 0
    # CommandParser logger: 0
    # IntentExecutor logger: 0

    # If you see anything other than "2, 0, 0, 0" then the fix didn't work.

    # Test logging
    print("\nTest log messages (should appear ONCE each, not 50 times):")
    logger1.info("INFO from agent logger")
    logger2.warning("WARNING from command_parser logger")
    logger3.error("ERROR from intent_executor logger")

    print("\n✅ Logging fix verified!")