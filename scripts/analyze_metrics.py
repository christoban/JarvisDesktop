"""
scripts/analyze_metrics.py — Analyse KPI historique
====================================================
Charge les datasets existants + memory et génère un rapport
KPI pour valider la qualité du pipeline et détecter drift.

Usage:
    python scripts/analyze_metrics.py
    python scripts/analyze_metrics.py --intents APP_OPEN BROWSER_SEARCH
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import BASE_DIR


DATASET_FILE = BASE_DIR / "data" / "dataset.jsonl"
DATASET_RAW_FILE = BASE_DIR / "data" / "dataset_raw.jsonl"
MEMORY_FILE = BASE_DIR / "data" / "jarvis_memory.json"


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


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(str(path), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def analyze_datasets():
    """Charge et analyse les datasets."""
    clean_rows = _read_jsonl(DATASET_FILE)
    raw_rows = _read_jsonl(DATASET_RAW_FILE)
    
    print(f"\n{'='*60}")
    print(f"JARVIS DATASET METRICS")
    print(f"{'='*60}\n")
    
    print(f"├─ Clean dataset    : {len(clean_rows):3d} entries")
    print(f"├─ Raw dataset      : {len(raw_rows):3d} entries")
    if raw_rows:
        accepted = sum(1 for r in raw_rows if r.get("quality_gate") == "accepted")
        rejected = sum(1 for r in raw_rows if r.get("quality_gate") == "rejected")
        print(f"│  ├─ Accepted      : {accepted:3d} ({100*accepted/len(raw_rows):.1f}%)")
        print(f"│  └─ Rejected      : {rejected:3d} ({100*rejected/len(raw_rows):.1f}%)")
    
    # Analyse par intent
    intent_stats = defaultdict(lambda: {"count": 0, "avg_confidence": 0.0, "sources": defaultdict(int)})
    for row in clean_rows:
        intent = row.get("intent", "UNKNOWN")
        conf = float(row.get("confidence", 0.0))
        source = row.get("source", "unknown")
        
        stats = intent_stats[intent]
        stats["count"] += 1
        stats["avg_confidence"] = (stats["avg_confidence"] * (stats["count"]-1) + conf) / stats["count"]
        stats["sources"][source] += 1
    
    print(f"\n├─ Top 10 intents (by frequency):")
    for intent, stats in sorted(intent_stats.items(), key=lambda x: -x[1]["count"])[:10]:
        print(f"│  ├─ {intent:25s} : {stats['count']:3d} ({stats['avg_confidence']:.2f} avg conf)")
        for src, cnt in sorted(stats["sources"].items(), key=lambda x: -x[1]):
            print(f"│  │  └─ {src:15s} : {cnt:3d}")
    
    # Analyse sources
    source_stats = defaultdict(lambda: {"count": 0, "avg_confidence": 0.0})
    for row in clean_rows:
        source = row.get("source", "unknown")
        conf = float(row.get("confidence", 0.0))
        
        stats = source_stats[source]
        stats["count"] += 1
        stats["avg_confidence"] = (stats["avg_confidence"] * (stats["count"]-1) + conf) / stats["count"]
    
    print(f"\n├─ Sources (parser engines):")
    for source, stats in sorted(source_stats.items(), key=lambda x: -x[1]["count"]):
        print(f"│  ├─ {source:15s} : {stats['count']:3d} ({stats['avg_confidence']:.2f} avg conf)")
    
    # Confidence distribution
    confidence_buckets = defaultdict(int)
    for row in clean_rows:
        conf = float(row.get("confidence", 0.0))
        bucket = f"{int(conf*10)*10}%-{int(conf*10)*10+10}%"
        confidence_buckets[bucket] += 1
    
    print(f"\n├─ Confidence distribution:")
    for bucket in sorted(confidence_buckets.keys()):
        count = confidence_buckets[bucket]
        pct = 100 * count / len(clean_rows) if clean_rows else 0
        bar = "█" * int(pct / 2)
        print(f"│  ├─ {bucket:10s} : {count:3d} {bar}")
    
    return clean_rows, raw_rows, intent_stats, source_stats


def analyze_memory():
    """Charge et analyse jarvis_memory.json."""
    memory = _read_json(MEMORY_FILE)
    
    if not memory:
        print(f"\n└─ Memory file not found or invalid")
        return None
    
    stats = memory.get("stats", {})
    session_count = stats.get("session_count", 0)
    total_commands = stats.get("total_commands", 0)
    
    facts = memory.get("facts", {})
    favorites = {k: v.get("value") for k, v in facts.items() if v and "value" in v}
    
    print(f"\n{'='*60}")
    print(f"JARVIS MEMORY & SESSION STATS")
    print(f"{'='*60}\n")
    
    print(f"├─ Sessions        : {session_count}")
    print(f"├─ Total commands  : {total_commands}")
    if session_count:
        print(f"├─ Cmd per session : {total_commands/session_count:.1f}")
    
    print(f"\n├─ User facts (learned):")
    for label, value in sorted(favorites.items())[:5]:
        print(f"│  ├─ {label:20s} : {value}")
    
    prefs = memory.get("preferences", {})
    if prefs:
        print(f"\n├─ Preferences:")
        if "app_counts" in prefs:
            for app, cnt in sorted(prefs["app_counts"].items(), key=lambda x: -x[1])[:5]:
                print(f"│  ├─ {app:20s} : {cnt:3d} opens")
    
    return memory


def main():
    parser = argparse.ArgumentParser(description="Analyze Jarvis metrics and KPIs")
    parser.add_argument("--intents", nargs="*", help="Filter specific intents")
    parser.add_argument("--source", type=str, help="Filter by source (groq, telegram, etc)")
    args = parser.parse_args()
    
    print("\n🔍 Loading datasets...")
    clean_rows, raw_rows, intent_stats, source_stats = analyze_datasets()
    
    print("\n🧠 Loading memory...")
    memory = analyze_memory()
    
    # Recommendations
    print(f"\n{'='*60}")
    print(f"RECOMMENDATIONS FOR SPRINT 3")
    print(f"{'='*60}\n")
    
    low_conf_intents = [i for i, s in intent_stats.items() if s["avg_confidence"] < 0.85]
    if low_conf_intents:
        print(f"⚠️  Low confidence intents (< 0.85):")
        for intent in low_conf_intents[:5]:
            print(f"   - {intent}")
    
    fallback_heavy = source_stats.get("fallback", {})
    if fallback_heavy.get("count", 0) / len(clean_rows) > 0.1 if clean_rows else False:
        print(f"\n⚠️  Heavy fallback usage ({fallback_heavy['count']} entries)")
        print(f"    → Need to improve parser training")
    
    print(f"\n✅ E2E Test coverage needed for:")
    top_10 = sorted(intent_stats.items(), key=lambda x: -x[1]["count"])[:10]
    for intent, stats in top_10:
        print(f"   - {intent} ({stats['count']} sessions)")
    
    print()


if __name__ == "__main__":
    main()
