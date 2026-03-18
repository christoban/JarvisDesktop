"""
logger.py — Système de logging centralisé pour tout le projet
Chaque module importe get_logger(__name__) pour avoir son propre logger
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config.settings import LOG_DIR, LOG_LEVEL


def get_logger(name: str) -> logging.Logger:
    """
    Retourne un logger configuré pour le module donné.
    
    Usage dans chaque module :
        from config.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Message")
    """
    logger = logging.getLogger(name)

    # Éviter les doublons si déjà configuré
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # ── Format des messages ──────────────────────────────────
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # ── Handler console (terminal) ───────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)

    # ── Handler fichier (rotation 5 Mo, 3 fichiers max) ──────
    log_file = LOG_DIR / "jarvis.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 Mo
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger