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
USE_TOOL_CALLS    = os.getenv("USE_TOOL_CALLS", "false").lower() in ("true", "1", "yes")
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
AZURE_SPEECH_KEY    = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")

# ─────────────────────────────────────────
#  AZURE FUNCTION
# ─────────────────────────────────────────
AZURE_FUNCTION_URL = os.getenv("AZURE_FUNCTION_URL", "")
AZURE_FUNCTION_KEY = os.getenv("AZURE_FUNCTION_KEY", "")
AZURE_STREAM_URL   = os.getenv("AZURE_STREAM_URL", "")

# ─────────────────────────────────────────
#  AZURE NOTIFICATION HUB
# ─────────────────────────────────────────
AZURE_NOTIFICATION_HUB_CONNECTION = os.getenv("AZURE_NOTIFICATION_HUB_CONNECTION", "")
AZURE_NOTIFICATION_HUB_NAME       = os.getenv("AZURE_NOTIFICATION_HUB_NAME", "")

# ─────────────────────────────────────────
#  TELEGRAM BOT
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")  # ID ou username (ex: @nom)

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
#  AGENT AUTONOME (TONY STARK V2)
# ─────────────────────────────────────────
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", 4))
TOOLCALL_FALLBACK_ENABLED = os.getenv("TOOLCALL_FALLBACK_ENABLED", "true").lower() in ("1", "true", "yes", "on")

# ─────────────────────────────────────────
#  CONTEXTE SENSORIEL
# ─────────────────────────────────────────
SENSORY_CONTEXT_ENABLED = os.getenv("SENSORY_CONTEXT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
SENSORY_TIMEOUT_MS = int(os.getenv("SENSORY_TIMEOUT_MS", 150))

# ─────────────────────────────────────────
#  RAG / MÉMOIRE VECTORIELLE (TONY STARK V2)
# ─────────────────────────────────────────
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() in ("1", "true", "yes", "on")
RAG_BACKEND = os.getenv("RAG_BACKEND", "chromadb")
RAG_COLLECTION = os.getenv("RAG_COLLECTION", "jarvis_memory")
MEMORY_MIN_CONFIDENCE = float(os.getenv("MEMORY_MIN_CONFIDENCE", 0.7))
CHROMADB_PATH = BASE_DIR / "data" / "vector_store"

# ─────────────────────────────────────────
#  TOOL CALLING & MULTI-ACTION (TONY STARK V2)
# ─────────────────────────────────────────
USE_TOOL_CALLS = os.getenv("USE_TOOL_CALLS", "true").lower() in ("1", "true", "yes", "on")
TOOL_CALL_TIMEOUT = float(os.getenv("TOOL_CALL_TIMEOUT", 30.0))
AI_RESPONSE_TOKENS = int(os.getenv("AI_RESPONSE_TOKENS", 200))
AI_MIN_CONFIDENCE = float(os.getenv("AI_MIN_CONFIDENCE", 0.65))

# ─────────────────────────────────────────
#  CHEMINS LOCAUX
# ─────────────────────────────────────────
LOG_DIR     = BASE_DIR / "logs"
HISTORY_DIR = BASE_DIR / "data" / "history"
VECTOR_STORE_DIR = CHROMADB_PATH

# Créer les dossiers si inexistants
LOG_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────
#  IA LOCALE (TONY STARK V2)
# ─────────────────────────────────────────
DATASET_MODE         = os.getenv("DATASET_MODE", "true").lower() in ("1", "true", "yes")
LOCAL_LLM_ENABLED    = os.getenv("LOCAL_LLM_ENABLED", "false").lower() in ("1", "true", "yes")
OLLAMA_URL           = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL         = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_EMBED_MODEL   = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
LOCAL_LLM_CONFIDENCE = float(os.getenv("LOCAL_LLM_CONFIDENCE", "0.75"))
EMBED_CONFIDENCE     = float(os.getenv("EMBED_CONFIDENCE", "0.82"))
DATASET_FILE         = BASE_DIR / "data" / "dataset.jsonl"
EMBED_CACHE_FILE     = BASE_DIR / "data" / "intent_embeddings.json"


def check_config():
    """Vérifie que les clés critiques sont bien remplies."""
    missing = []
    critical_keys = {
        "GROQ_API_KEY": GROQ_API_KEY,
        "SECRET_TOKEN": SECRET_TOKEN,
    }
    for name, value in critical_keys.items():
        if not value or value.startswith("VOTRE") or value == "changeme":
            missing.append(name)

    if missing:
        print(f"⚠️  ATTENTION — Clés manquantes dans .env : {', '.join(missing)}")
        return False

    if not AZURE_SPEECH_KEY or AZURE_SPEECH_KEY.startswith("VOTRE"):
        print("⚠️  AZURE_SPEECH_KEY manquante — la page vocale ne pourra pas transcrire.")
    if not AZURE_SPEECH_REGION:
        print("⚠️  AZURE_SPEECH_REGION manquante — la page vocale ne pourra pas transcrire.")

    if AGENT_MAX_STEPS < 1:
        print("⚠️  AGENT_MAX_STEPS invalide — valeur minimale forcée à 1 recommandée.")
    if SENSORY_TIMEOUT_MS < 50:
        print("⚠️  SENSORY_TIMEOUT_MS très bas — risque d'échec de collecte du contexte.")
    if not (0.0 <= MEMORY_MIN_CONFIDENCE <= 1.0):
        print("⚠️  MEMORY_MIN_CONFIDENCE doit être entre 0.0 et 1.0.")

    print("✅ Configuration chargée avec succès.")
    return True


if __name__ == "__main__":
    check_config()