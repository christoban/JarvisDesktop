"""
settings.py — Chargement centralisé de toute la configuration
Lit le fichier .env et expose les variables à tout le projet
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Chemin racine du projet
BASE_DIR = Path(__file__).resolve().parent.parent

# Charger le fichier .env
load_dotenv(BASE_DIR / "config" / ".env")

# ─────────────────────────────────────────
#  IA & VOIX (Nouveaux moteurs prioritaires)
# ─────────────────────────────────────────
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL_NAME   = os.getenv("GROQ_MODEL_NAME", "llama-3.3-70b-versatile")
#GROQ_BASE_URL     = os.getenv("GROQ_BASE_URL", "")


OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_TTS_MODEL    = os.getenv("OPENAI_TTS_MODEL", "tts-1")
OPENAI_TTS_VOICE    = os.getenv("OPENAI_TTS_VOICE", "alloy")
OPENAI_WHISPER_MODEL= os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")

# ─────────────────────────────────────────
#  AZURE OPENAI
# ─────────────────────────────────────────
# AZURE_OPENAI_ENDPOINT        = os.getenv("AZURE_OPENAI_ENDPOINT", "")
# AZURE_OPENAI_API_KEY         = os.getenv("AZURE_OPENAI_API_KEY", "")
# AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4")
# AZURE_OPENAI_API_VERSION     = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

# ─────────────────────────────────────────
#  AZURE SPEECH
# ─────────────────────────────────────────
# AZURE_SPEECH_KEY    = os.getenv("AZURE_SPEECH_KEY", "")
# AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")

# ─────────────────────────────────────────
#  AZURE FUNCTION
# ─────────────────────────────────────────
AZURE_FUNCTION_URL = os.getenv("AZURE_FUNCTION_URL", "")
AZURE_FUNCTION_KEY = os.getenv("AZURE_FUNCTION_KEY", "")

# ─────────────────────────────────────────
#  AZURE NOTIFICATION HUB
# ─────────────────────────────────────────
AZURE_NOTIFICATION_HUB_CONNECTION = os.getenv("AZURE_NOTIFICATION_HUB_CONNECTION", "")
AZURE_NOTIFICATION_HUB_NAME       = os.getenv("AZURE_NOTIFICATION_HUB_NAME", "")

# ─────────────────────────────────────────
#  SÉCURITÉ
# ─────────────────────────────────────────
SECRET_TOKEN       = os.getenv("SECRET_TOKEN", "changeme")
DEVICE_ID          = os.getenv("DEVICE_ID", "")

# ─────────────────────────────────────────
#  AGENT PC
# ─────────────────────────────────────────
AGENT_HOST = os.getenv("AGENT_HOST", "0.0.0.0")
AGENT_PORT = int(os.getenv("AGENT_PORT", 8765))
LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO")

# ─────────────────────────────────────────
#  CHEMINS LOCAUX
# ─────────────────────────────────────────
LOG_DIR     = BASE_DIR / "logs"
HISTORY_DIR = BASE_DIR / "data" / "history"

# Créer les dossiers si inexistants
LOG_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def check_config():
    """Vérifie que les clés critiques sont bien remplies."""
    missing = []
    critical_keys = {
        "GROQ_API_KEY": GROQ_API_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "SECRET_TOKEN": SECRET_TOKEN,
    }
    for name, value in critical_keys.items():
        if not value or value.startswith("VOTRE") or value == "changeme":
            missing.append(name)

    if missing:
        print(f"⚠️  ATTENTION — Clés manquantes dans .env : {', '.join(missing)}")
        return False

    print("✅ Configuration chargée avec succès.")
    return True


if __name__ == "__main__":
    check_config()