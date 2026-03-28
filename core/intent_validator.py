"""
core/intent_validator.py — Groq validator for routed intents
============================================================

Validate/correct fast router predictions before execution to reduce
wrong actions and dataset pollution.
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Optional

from config.logger import get_logger
from config.settings import GROQ_API_KEY, GROQ_MODEL_NAME

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    corrected_intent: Optional[str]
    corrected_params: dict
    confidence: float
    reason: str


class IntentValidator:
    """Validate or correct intent candidates with Groq."""

    def __init__(self):
        self.client = None
        self.ai_available = False
        self._groq_cooldown_until = 0.0
        self._init_client()

    def _init_client(self):
        try:
            if not GROQ_API_KEY or GROQ_API_KEY.startswith("VOTRE"):
                logger.warning("IntentValidator: GROQ_API_KEY not configured")
                return

            from groq import Groq

            self.client = Groq(api_key=GROQ_API_KEY)
            self.ai_available = True
            logger.info(f"IntentValidator initialized with {GROQ_MODEL_NAME}")
        except Exception as e:
            self.ai_available = False
            logger.error(f"IntentValidator init failed: {e}")

    def validate(self, command: str, intent: str, params: dict) -> ValidationResult:
        if not self.ai_available or self.client is None:
            return ValidationResult(True, None, params or {}, 0.70, "validator_unavailable")

        if time.time() < self._groq_cooldown_until:
            return ValidationResult(True, None, params or {}, 0.70, "validator_cooldown")

        prompt = self._build_prompt(command, intent, params or {})

        try:
            resp = self.client.chat.completions.create(
                model=GROQ_MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=220,
            )
            text = (resp.choices[0].message.content or "").strip()
            return self._parse_response(text, fallback_intent=intent, fallback_params=params or {})
        except Exception as e:
            self._set_cooldown_from_error(e)
            logger.error(f"IntentValidator error: {e}")
            return ValidationResult(True, None, params or {}, 0.60, "validator_error")

    @staticmethod
    def _build_prompt(command: str, intent: str, params: dict) -> str:
        return (
            "Tu es un validateur strict d'intentions JARVIS.\n"
            "Tache: verifier si l'intention suggeree correspond a la commande utilisateur.\n\n"
            f"Commande: \"{command}\"\n"
            f"Intent suggere: {intent}\n"
            f"Params suggeres: {json.dumps(params, ensure_ascii=False)}\n\n"
            "Reponds UNIQUEMENT en JSON strict avec ce schema:\n"
            "{\n"
            "  \"is_valid\": true/false,\n"
            "  \"corrected_intent\": \"INTENT\" ou null,\n"
            "  \"corrected_params\": { ... },\n"
            "  \"confidence\": 0.0-1.0,\n"
            "  \"reason\": \"court\"\n"
            "}\n"
            "Regles:\n"
            "- Si c'est correct: is_valid=true, corrected_intent=null.\n"
            "- Si c'est faux: is_valid=false et corrected_intent doit etre explicite.\n"
            "- Garde les params si pas necessaire de les changer."
        )

    def _parse_response(self, text: str, fallback_intent: str, fallback_params: dict) -> ValidationResult:
        del fallback_intent  # kept for future guardrails
        text = re.sub(r"```(?:json)?", "", text).strip()

        data = None
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None

        if not isinstance(data, dict):
            return ValidationResult(True, None, fallback_params, 0.55, "validator_parse_failed")

        is_valid = bool(data.get("is_valid", True))
        corrected_intent = data.get("corrected_intent")
        corrected_params = data.get("corrected_params")
        confidence = float(data.get("confidence", 0.50))
        reason = str(data.get("reason", ""))

        if not isinstance(corrected_params, dict):
            corrected_params = fallback_params

        if is_valid:
            return ValidationResult(True, None, fallback_params, confidence, reason or "validated")

        if not corrected_intent or not isinstance(corrected_intent, str):
            return ValidationResult(True, None, fallback_params, 0.55, "invalid_correction")

        return ValidationResult(False, corrected_intent.strip().upper(), corrected_params, confidence, reason)

    def _set_cooldown_from_error(self, error: Exception):
        msg = str(error).lower()
        wait_s = 60 if "rate" in msg or "429" in msg else 20
        self._groq_cooldown_until = time.time() + wait_s


_validator_singleton = None


def get_intent_validator() -> IntentValidator:
    global _validator_singleton
    if _validator_singleton is None:
        _validator_singleton = IntentValidator()
    return _validator_singleton
