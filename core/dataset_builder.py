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
import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path
from config.logger import get_logger
from config.settings import DATASET_FILE, DATASET_MODE, DATASET_RAW_FILE

logger = get_logger(__name__)
_lock  = threading.Lock()

_EXCLUDED_INTENTS = {
    "",
    "UNKNOWN",
    "INCOMPLETE",
    "FOLLOWUP",
    "HELP",
    "GREETING",
    "__CLARIFY_INTENT__",
}

_EXCLUDED_SOURCES = {
    "fallback",
    "fast_rules",
    "context",
    "context_correction",
}

_LOG_LINE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*\|\s*(INFO|WARNING|WARN|ERROR|DEBUG|CRITICAL)"
)

_seen_hashes: set[str] = set()
_seen_loaded = False


def _normalize_input(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _canonical_params(params: dict) -> str:
    try:
        return json.dumps(params or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "{}"


def _sample_hash(input_text: str, intent: str, params: dict) -> str:
    payload = f"{_normalize_input(input_text)}|{str(intent or '').upper()}|{_canonical_params(params)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _looks_like_log_entry(text: str) -> bool:
    return bool(_LOG_LINE_RE.search(str(text or "")))


def _load_existing_hashes_once() -> None:
    global _seen_loaded
    if _seen_loaded:
        return

    if not DATASET_FILE.exists():
        _seen_loaded = True
        return

    try:
        with open(str(DATASET_FILE), encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    h = _sample_hash(entry.get("input", ""), entry.get("intent", ""), entry.get("params") or {})
                    _seen_hashes.add(h)
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"Dataset hash preload error: {e}")
    finally:
        _seen_loaded = True


def _append_jsonl(file_path: Path, payload: dict) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(file_path), "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def save_entry(input_text: str, result: dict, source: str = "groq") -> bool:
    """
    Sauvegarde une entrée dans le dataset si DATASET_MODE est activé.

    Args:
        input_text : commande brute de l'utilisateur
        result     : dict retourné par command_parser (intent, params, confidence, quality_flag)
        source     : "groq" | "fallback" | "local_llm"

    Returns:
        True si sauvegardé, False sinon

    RÈGLES DE QUALITÉ (entrées non sauvegardées) :
      - intent UNKNOWN ou INCOMPLETE
      - confidence < 0.80
      - input vide ou trop court (< 3 chars)
      - source == "fallback" (règles locales, pas pertinent pour dataset LLM)
      
    TRAÇABILITÉ DE QUALITÉ:
      - quality_flag == "premium" (conf ≥0.95) : données d'entraînement premium
      - quality_flag == "uncertain_needs_review" : données brutes, nécessitent révision
    """
    if not DATASET_MODE:
        return False

    intent     = str(result.get("intent", "UNKNOWN") or "").strip().upper()
    confidence = float(result.get("confidence", 0.0))
    params     = result.get("params") or {}
    source_clean = str(source or "").strip().lower()
    input_clean = _normalize_input(input_text)
    quality_flag = str(result.get("quality_flag", "standard") or "").strip()

    reject_reason = ""
    if intent in _EXCLUDED_INTENTS:
        reject_reason = "excluded_intent"
    elif source_clean in _EXCLUDED_SOURCES:
        reject_reason = "excluded_source"
    elif confidence < 0.80:
        reject_reason = "low_confidence"
    elif len(input_clean) < 3:
        reject_reason = "input_too_short"
    elif _looks_like_log_entry(input_text):
        reject_reason = "log_like_input"

    sample_id = _sample_hash(input_clean, intent, params)

    raw_entry = {
        "timestamp":  datetime.now().isoformat(),
        "input":      input_text.strip(),
        "intent":     intent,
        "params":     params,
        "confidence": round(confidence, 3),
        "source":     source_clean,
        "sample_id":  sample_id,
        "quality_gate": "accepted",
        "quality_gate_reason": "",
        "quality_flag": quality_flag,  # New: premium / uncertain / standard
    }

    clean_entry = {
        "timestamp": raw_entry["timestamp"],
        "input": raw_entry["input"],
        "intent": raw_entry["intent"],
        "params": raw_entry["params"],
        "confidence": raw_entry["confidence"],
        "source": raw_entry["source"],
        "sample_id": raw_entry["sample_id"],
        "quality_gate": "accepted",
        "quality_flag": quality_flag,  # New: premium / uncertain / standard
    }

    try:
        with _lock:
            _load_existing_hashes_once()
            if not reject_reason and sample_id in _seen_hashes:
                reject_reason = "duplicate"

            if reject_reason:
                raw_entry["quality_gate"] = "rejected"
                raw_entry["quality_gate_reason"] = reject_reason
                _append_jsonl(DATASET_RAW_FILE, raw_entry)
                return False

            _append_jsonl(DATASET_RAW_FILE, raw_entry)
            _append_jsonl(DATASET_FILE, clean_entry)
            _seen_hashes.add(sample_id)
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
                    if entry.get("quality_gate", "accepted") != "accepted":
                        continue
                    if str(entry.get("intent", "")).upper() in _EXCLUDED_INTENTS:
                        continue
                    if _looks_like_log_entry(entry.get("input", "")):
                        continue
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


def get_quality_report() -> dict:
    """
    Retourne un rapport qualité simple basé sur dataset_raw (quarantaine)
    et dataset clean (entraînable).
    """
    report = {
        "raw_total": 0,
        "accepted": 0,
        "rejected": 0,
        "acceptance_rate": 0.0,
        "rejection_reasons": {},
        "accepted_by_source": {},
        "accepted_by_intent": {},
        "clean_total": 0,
    }

    if DATASET_RAW_FILE.exists():
        try:
            with open(str(DATASET_RAW_FILE), encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                    except Exception:
                        continue

                    report["raw_total"] += 1
                    gate = str(entry.get("quality_gate", "accepted"))

                    if gate == "accepted":
                        report["accepted"] += 1

                        src = str(entry.get("source", "unknown")).lower()
                        report["accepted_by_source"][src] = report["accepted_by_source"].get(src, 0) + 1

                        intent = str(entry.get("intent", "UNKNOWN")).upper()
                        report["accepted_by_intent"][intent] = report["accepted_by_intent"].get(intent, 0) + 1
                    else:
                        report["rejected"] += 1
                        reason = str(entry.get("quality_gate_reason", "unknown"))
                        report["rejection_reasons"][reason] = report["rejection_reasons"].get(reason, 0) + 1
        except Exception as e:
            logger.error(f"Quality report read error (raw): {e}")

    if DATASET_FILE.exists():
        try:
            with open(str(DATASET_FILE), encoding="utf-8") as f:
                for line in f:
                    try:
                        json.loads(line.strip())
                        report["clean_total"] += 1
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Quality report read error (clean): {e}")

    if report["raw_total"] > 0:
        report["acceptance_rate"] = round((report["accepted"] / report["raw_total"]) * 100, 2)

    return report