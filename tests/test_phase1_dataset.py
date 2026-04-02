#!/usr/bin/env python3
"""
Tests anti-pollution dataset (Sprint 2).
Valide la quarantaine raw/clean, les rejets critiques et les doublons.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.dataset_builder as ds


def _reset_runtime_state() -> None:
    ds._seen_hashes.clear()
    ds._seen_loaded = False


def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open(encoding="utf-8"))


def test_reject_followup_and_log_lines(tmp_path):
    ds.DATASET_FILE = tmp_path / "dataset.jsonl"
    ds.DATASET_RAW_FILE = tmp_path / "dataset_raw.jsonl"
    _reset_runtime_state()

    ok_followup = ds.save_entry(
        "non ouvre un nouvel onglet plutot",
        {"intent": "FOLLOWUP", "params": {}, "confidence": 0.95},
        source="context",
    )
    ok_logline = ds.save_entry(
        "2026-03-28 11:42:58 | ERROR | core.telegram_bot | timeout",
        {"intent": "SYSTEM_TIME", "params": {}, "confidence": 0.98},
        source="groq",
    )

    assert ok_followup is False
    assert ok_logline is False
    assert _jsonl_count(ds.DATASET_FILE) == 0
    assert _jsonl_count(ds.DATASET_RAW_FILE) == 2


def test_duplicate_goes_to_raw_not_clean(tmp_path):
    ds.DATASET_FILE = tmp_path / "dataset.jsonl"
    ds.DATASET_RAW_FILE = tmp_path / "dataset_raw.jsonl"
    _reset_runtime_state()

    payload = {
        "intent": "APP_OPEN",
        "params": {"app_name": "chrome"},
        "confidence": 0.99,
    }

    first = ds.save_entry("ouvre chrome", payload, source="groq")
    second = ds.save_entry("ouvre chrome", payload, source="groq")

    assert first is True
    assert second is False
    assert _jsonl_count(ds.DATASET_FILE) == 1
    assert _jsonl_count(ds.DATASET_RAW_FILE) == 2


def test_quality_report_has_rejection_reasons(tmp_path):
    ds.DATASET_FILE = tmp_path / "dataset.jsonl"
    ds.DATASET_RAW_FILE = tmp_path / "dataset_raw.jsonl"
    _reset_runtime_state()

    ds.save_entry(
        "ouvre chrome",
        {"intent": "APP_OPEN", "params": {"app_name": "chrome"}, "confidence": 0.99},
        source="groq",
    )
    ds.save_entry(
        "a",
        {"intent": "APP_OPEN", "params": {"app_name": "chrome"}, "confidence": 0.99},
        source="groq",
    )

    report = ds.get_quality_report()

    assert report["raw_total"] == 2
    assert report["accepted"] == 1
    assert report["rejected"] == 1
    assert "input_too_short" in report["rejection_reasons"]
    assert report["clean_total"] == 1
