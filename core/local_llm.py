"""
core/local_llm.py — Client Ollama pour parsing d'intents
=========================================================
Remplace Groq pour le parsing des commandes Jarvis.
Utilise le dataset collecté en Phase 1 comme few-shots dynamiques.

Usage :
    from core.local_llm import LocalLLMParser
    parser = LocalLLMParser()
    result = parser.parse("ouvre chrome et joue ma playlist")
    # → {"intent": "MULTI_ACTION", "params": {...}, "confidence": 0.93}
"""

import json
import re
import time
import requests
from config.logger import get_logger
from config.settings import OLLAMA_URL, OLLAMA_MODEL, LOCAL_LLM_CONFIDENCE

logger = get_logger(__name__)

# ── System prompt optimisé pour Jarvis ───────────────────────────────────────
# Ce prompt est LE cœur du système. Il programme le comportement du modèle.
# Ne pas le modifier sans tests approfondis.
SYSTEM_PROMPT = """Tu es le moteur d'intention de JARVIS, un assistant vocal pour Windows.

Ta seule mission : analyser la commande utilisateur et retourner l'intent et les paramètres en JSON strict.

RÈGLES ABSOLUES :
1. Tu réponds UNIQUEMENT en JSON valide — aucun texte avant ou après
2. Tu identifies l'intent le plus précis possible parmi les intents connus
3. Tu extrais TOUS les paramètres pertinents
4. Tu détectes les corrections ("non", "pas ça", "annule") → intent CORRECTION
5. Tu gères les multi-actions ("ouvre X ET joue Y") → intent MULTI_ACTION

FORMAT DE RÉPONSE OBLIGATOIRE :
{
  "intent": "NOM_INTENT",
  "confidence": 0.0-1.0,
  "params": {},
  "context": {
    "is_correction": false,
    "is_multi_action": false
  }
}

INTENTS DISPONIBLES (liste non exhaustive) :
APP_OPEN, APP_CLOSE, APP_LIST_RUNNING,
BROWSER_SEARCH, BROWSER_URL, BROWSER_NEW_TAB, BROWSER_CLOSE_TAB,
MUSIC_PLAYLIST_PLAY, MUSIC_STOP, MUSIC_NEXT, MUSIC_PREV, AUDIO_VOLUME_SET,
FILE_OPEN, FILE_SEARCH, FOLDER_CREATE, FOLDER_LIST,
SYSTEM_SHUTDOWN, SYSTEM_RESTART, SYSTEM_LOCK, SYSTEM_INFO,
WIFI_LIST, NETWORK_INFO, SCREEN_CAPTURE, SCREEN_BRIGHTNESS,
WORD_CREATE, CV_CREATE, EXCEL_CREATE, EXCEL_READ, PDF_MERGE, PDF_EXTRACT,
MULTI_ACTION, PREFERENCE_SET, MEMORY_SHOW, KNOWLEDGE_QA, UNKNOWN
"""


class LocalLLMParser:
    """
    Parser d'intents via Ollama (modèle local).
    Drop-in replacement pour les appels Groq dans command_parser.py.
    """

    def __init__(self):
        self._available = self._check_ollama()
        if self._available:
            logger.info(f"LocalLLMParser initialisé — modèle : {OLLAMA_MODEL}")
        else:
            logger.warning("Ollama non disponible — LocalLLMParser désactivé")

    def _check_ollama(self) -> bool:
        """Vérifie qu'Ollama est lancé et que le modèle est disponible."""
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                available = any(OLLAMA_MODEL in m for m in models)
                if not available:
                    logger.warning(f"Modèle '{OLLAMA_MODEL}' non trouvé. Lance : ollama pull {OLLAMA_MODEL}")
                return available
        except Exception:
            pass
        return False

    @property
    def is_available(self) -> bool:
        return self._available

    def parse(self, command: str, few_shot_examples: list[dict] = None) -> dict:
        """
        Parse une commande et retourne l'intent + params.

        Args:
            command            : commande utilisateur brute
            few_shot_examples  : exemples du dataset pour le few-shot
                                 (chargés depuis dataset_builder.load_examples())

        Returns:
            dict : {"intent": str, "confidence": float, "params": dict,
                    "source": "local_llm"}
        """
        if not self._available:
            return self._unknown(command)

        # Construire le prompt avec few-shots
        prompt = self._build_prompt(command, few_shot_examples or [])

        try:
            t_start = time.time()
            response = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model":   OLLAMA_MODEL,
                    "prompt":  prompt,
                    "system":  SYSTEM_PROMPT,
                    "stream":  False,
                    "options": {
                        "temperature":   0.1,   # Très bas = réponses cohérentes
                        "top_p":         0.9,
                        "num_predict":   200,   # Max tokens réponse
                    }
                },
                timeout=15
            )
            elapsed = int((time.time() - t_start) * 1000)

            if response.status_code != 200:
                logger.warning(f"Ollama HTTP {response.status_code}")
                return self._unknown(command)

            raw_text = response.json().get("response", "").strip()
            result   = self._parse_json_response(raw_text)
            result["source"]   = "local_llm"
            result["latency_ms"] = elapsed
            logger.info(f"LocalLLM: intent={result.get('intent')} conf={result.get('confidence'):.2f} ({elapsed}ms)")
            return result

        except requests.Timeout:
            logger.warning("Ollama timeout — fallback Groq")
            return self._unknown(command, reason="timeout")
        except Exception as e:
            logger.error(f"LocalLLM error: {e}")
            return self._unknown(command)

    def _build_prompt(self, command: str, examples: list[dict]) -> str:
        """Construit le prompt avec few-shots dynamiques depuis le dataset."""
        parts = []

        if examples:
            parts.append("=== EXEMPLES ===")
            for ex in examples[:40]:  # Max 40 exemples
                params_str = json.dumps(ex.get("params", {}), ensure_ascii=False)
                parts.append(
                    f'Input: "{ex["input"]}"\n'
                    f'Output: {{"intent":"{ex["intent"]}","confidence":0.99,"params":{params_str},"context":{{"is_correction":false,"is_multi_action":false}}}}'
                )
            parts.append("=== FIN EXEMPLES ===\n")

        parts.append(f'Input: "{command}"\nOutput:')
        return "\n\n".join(parts)

    def _parse_json_response(self, text: str) -> dict:
        """Extrait et valide le JSON de la réponse Ollama."""
        # Nettoyer : enlever les blocs markdown ```json ... ```
        text = re.sub(r"```(?:json)?", "", text).strip()

        # Trouver le premier JSON valide dans le texte
        for match in re.finditer(r"\{.*?\}", text, re.DOTALL):
            try:
                data = json.loads(match.group(0))
                if "intent" in data:
                    # Normaliser
                    return {
                        "intent":     str(data.get("intent", "UNKNOWN")).upper(),
                        "confidence": float(data.get("confidence", 0.7)),
                        "params":     data.get("params") or {},
                        "context":    data.get("context") or {},
                    }
            except json.JSONDecodeError:
                continue

        logger.warning(f"LocalLLM: JSON invalide dans réponse : {text[:100]}")
        return self._unknown("")

    @staticmethod
    def _unknown(command: str, reason: str = "") -> dict:
        return {
            "intent":     "UNKNOWN",
            "confidence": 0.0,
            "params":     {},
            "source":     "local_llm_failed",
            "reason":     reason,
        }