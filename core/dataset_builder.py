"""
core/dataset_builder.py — Collecteur automatique de dataset
============================================================
Enregistre chaque réponse Groq dans data/dataset.jsonl.
Format : {"input": str, "intent": str, "params": dict, "confidence": float}

Ce dataset sera utilisé pour :
  1. Les few-shots injectés dans le prompt local LLM
  2. Les embeddings du router sémantique
  3. Le fine-tuning futur si nécessaire
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from config.logger import get_logger
from config.settings import DATASET_FILE, DATASET_MODE

logger = get_logger(__name__)
_lock  = threading.Lock()


def save_entry(input_text: str, result: dict, source: str = "groq") -> bool:
    """
    Sauvegarde une entrée dans le dataset si DATASET_MODE est activé.

    Args:
        input_text : commande brute de l'utilisateur
        result     : dict retourné par command_parser (intent, params, confidence)
        source     : "groq" | "fallback" | "local_llm"

    Returns:
        True si sauvegardé, False sinon

    RÈGLES DE QUALITÉ (entrées non sauvegardées) :
      - intent UNKNOWN ou INCOMPLETE
      - confidence < 0.80
      - input vide ou trop court (< 3 chars)
      - source == "fallback" (règles locales, pas pertinent pour dataset LLM)
    """
    if not DATASET_MODE:
        return False

    intent     = result.get("intent", "UNKNOWN")
    confidence = float(result.get("confidence", 0.0))
    params     = result.get("params") or {}
    input_clean = input_text.strip().lower()

    # Filtres qualité
    if intent in ("UNKNOWN", "INCOMPLETE", ""):
        return False
    if confidence < 0.80:
        return False
    if len(input_clean) < 3:
        return False
    if source == "fallback":
        return False

    entry = {
        "timestamp":  datetime.now().isoformat(),
        "input":      input_text.strip(),
        "intent":     intent,
        "params":     params,
        "confidence": round(confidence, 3),
        "source":     source,
    }

    try:
        DATASET_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(str(DATASET_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.error(f"Dataset save error: {e}")
        return False


def load_examples(n: int = 40, min_confidence: float = 0.85) -> list[dict]:
    """
    Charge les N meilleurs exemples du dataset pour les few-shots.

    Tri : confidence DESC, puis aléatoire pour diversité.
    Déduplique par intent pour avoir une couverture maximale.

    Returns:
        Liste de dicts {"input": str, "intent": str, "params": dict}
    """
    if not DATASET_FILE.exists():
        return []

    entries = []
    try:
        with open(str(DATASET_FILE), encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("confidence", 0) >= min_confidence:
                        entries.append(entry)
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"Dataset load error: {e}")
        return []

    # Dédupliquer : garder le meilleur exemple par intent
    seen_intents: dict[str, dict] = {}
    for entry in sorted(entries, key=lambda x: x.get("confidence", 0), reverse=True):
        intent = entry["intent"]
        if intent not in seen_intents:
            seen_intents[intent] = entry
        # Garder aussi des variantes (max 3 par intent)
        elif sum(1 for e in seen_intents.values() if e["intent"] == intent) < 3:
            seen_intents[f"{intent}_{len(seen_intents)}"] = entry

    result = list(seen_intents.values())[:n]
    return result


def format_few_shots(examples: list[dict]) -> str:
    """
    Formate les exemples en texte few-shot pour le prompt LLM local.

    Format :
        Input: "ouvre chrome"
        Output: {"intent":"APP_OPEN","params":{"app_name":"chrome"}}
    """
    lines = []
    for ex in examples:
        params_str = json.dumps(ex.get("params", {}), ensure_ascii=False)
        lines.append(
            f'Input: "{ex["input"]}"\n'
            f'Output: {{"intent":"{ex["intent"]}","params":{params_str}}}'
        )
    return "\n\n".join(lines)


def get_stats() -> dict:
    """Retourne les stats du dataset."""
    if not DATASET_FILE.exists():
        return {"total": 0, "intents": {}}

    total = 0
    intents: dict[str, int] = {}
    try:
        with open(str(DATASET_FILE), encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    total += 1
                    intent = entry.get("intent", "UNKNOWN")
                    intents[intent] = intents.get(intent, 0) + 1
                except Exception:
                    continue
    except Exception:
        pass
    return {"total": total, "intents": intents}