"""
scripts/curate_dataset.py — Curation dataset raw -> train
=========================================================
Construit un dataset d'entrainement propre a partir de dataset_raw.jsonl.

Usage:
    python scripts/curate_dataset.py
    python scripts/curate_dataset.py --min-confidence 0.9
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import BASE_DIR, DATASET_RAW_FILE
from core.dataset_builder import get_quality_report


EXCLUDED_INTENTS = {
    "",
    "UNKNOWN",
    "INCOMPLETE",
    "FOLLOWUP",
    "HELP",
    "GREETING",
    "__CLARIFY_INTENT__",
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(str(path), encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line.strip()))
            except Exception:
                continue
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def curate(raw_rows: list[dict], min_confidence: float) -> tuple[list[dict], dict]:
    kept = []
    seen = set()

    stats = {
        "raw_rows": len(raw_rows),
        "kept": 0,
        "dropped": 0,
        "drop_reasons": {},
    }

    def drop(reason: str):
        stats["drop_reasons"][reason] = stats["drop_reasons"].get(reason, 0) + 1

    for row in raw_rows:
        gate = str(row.get("quality_gate", "accepted"))
        if gate != "accepted":
            drop(f"quality_gate_{gate}")
            continue

        intent = str(row.get("intent", "")).upper()
        if intent in EXCLUDED_INTENTS:
            drop("excluded_intent")
            continue

        conf = float(row.get("confidence", 0.0))
        if conf < min_confidence:
            drop("below_min_confidence")
            continue

        sample_id = str(row.get("sample_id", "")).strip()
        if not sample_id:
            drop("missing_sample_id")
            continue
        if sample_id in seen:
            drop("duplicate_sample_id")
            continue

        train_row = {
            "input": row.get("input", "").strip(),
            "intent": intent,
            "params": row.get("params") or {},
            "confidence": round(conf, 3),
            "source": str(row.get("source", "unknown")).lower(),
            "sample_id": sample_id,
        }
        if not train_row["input"]:
            drop("empty_input")
            continue

        seen.add(sample_id)
        kept.append(train_row)

    stats["kept"] = len(kept)
    stats["dropped"] = max(0, stats["raw_rows"] - stats["kept"])
    stats["keep_rate"] = round((stats["kept"] / stats["raw_rows"]) * 100, 2) if stats["raw_rows"] else 0.0
    return kept, stats


def main():
    parser = argparse.ArgumentParser(description="Curate Jarvis training dataset from raw quarantine file")
    parser.add_argument("--min-confidence", type=float, default=0.9, help="Minimum confidence to keep")
    parser.add_argument(
        "--out",
        type=str,
        default=str(BASE_DIR / "data" / "dataset_train.jsonl"),
        help="Output train JSONL path",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(BASE_DIR / "data" / "dataset_quality_report.json"),
        help="Output report JSON path",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    report_path = Path(args.report)

    raw_rows = _read_jsonl(DATASET_RAW_FILE)
    train_rows, curation_stats = curate(raw_rows, min_confidence=args.min_confidence)
    _write_jsonl(out_path, train_rows)

    quality_report = get_quality_report()
    report_payload = {
        "curation": curation_stats,
        "live_quality": quality_report,
        "min_confidence": args.min_confidence,
        "train_output": str(out_path),
        "raw_input": str(DATASET_RAW_FILE),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== JARVIS DATASET CURATION ===")
    print(f"Raw input rows      : {curation_stats['raw_rows']}")
    print(f"Train rows kept     : {curation_stats['kept']}")
    print(f"Rows dropped        : {curation_stats['dropped']}")
    print(f"Keep rate           : {curation_stats['keep_rate']}%")
    print(f"Output train file   : {out_path}")
    print(f"Output report file  : {report_path}")


if __name__ == "__main__":
    main()
