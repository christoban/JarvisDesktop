#!/usr/bin/env python3
"""
run_full_command_audit.py

Executes a full Jarvis command battery and writes detailed audit reports
(JSON + Markdown) so a team can review what works, what fails, and why.

Usage:
    cd JarvisDesktop
    python tests/run_full_command_audit.py
    python tests/run_full_command_audit.py --allow-dangerous
    python tests/run_full_command_audit.py --groups SYSTEM,AUDIO,APPS

Notes:
- By default, dangerous commands are skipped (shutdown / lock screen).
- The script captures the exact assistant message returned by Agent.handle_command.
- Reports are written to data/test_reports/.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.agent import Agent  # noqa: E402


@dataclass
class TestCase:
    command: str
    expected_intents: list[str]
    expected_data_keys: list[str]
    notes: str = ""
    dangerous: bool = False
    manual_check: bool = False


SUITES: dict[str, list[TestCase]] = {
    "SYSTEM": [
        TestCase("infos systeme", ["SYSTEM_INFO"], ["cpu", "ram", "memory"], notes="CPU/RAM info"),
        TestCase("montre les processus en cours", ["SYSTEM_PROCESSES"], ["process"], notes="Process list"),
        TestCase("espace disque", ["SYSTEM_DISK"], ["disk", "free", "total"], notes="Disk state"),
        TestCase("quelle heure est-il", ["SYSTEM_TIME"], ["time", "date", "timestamp"], notes="Local time"),
        TestCase(
            "eteins l'ordinateur dans 2 minutes",
            ["SYSTEM_SHUTDOWN"],
            ["delay", "seconds", "shutdown"],
            notes="Countdown shutdown",
            dangerous=True,
        ),
        TestCase("annule l'extinction", ["SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL"], ["cancel", "annul"], notes="Cancel shutdown"),
        TestCase("verrouille l'ecran", ["SYSTEM_LOCK"], ["lock", "verrou"], notes="Lock screen", dangerous=True),
    ],
    "AUDIO": [
        TestCase("mets le volume a 50%", ["AUDIO_VOLUME_SET", "MUSIC_VOLUME"], ["volume", "level"], notes="Set volume 50", manual_check=True),
        TestCase("monte le volume", ["AUDIO_VOLUME_UP"], ["volume", "step"], notes="Volume up", manual_check=True),
        TestCase("baisse le son", ["AUDIO_VOLUME_DOWN"], ["volume", "step"], notes="Volume down", manual_check=True),
        TestCase("coupe le son", ["AUDIO_MUTE"], ["mute", "son"], notes="Mute", manual_check=True),
        TestCase("retablis le son", ["AUDIO_MUTE"], ["mute", "son"], notes="Unmute toggle", manual_check=True),
        TestCase("quel est le volume actuel", ["AUDIO_VOLUME_SET", "MUSIC_CURRENT", "UNKNOWN"], ["volume"], notes="Current volume may depend on module support"),
    ],
    "APPS": [
        TestCase("ouvre chrome", ["APP_OPEN", "BROWSER_OPEN"], ["chrome"], notes="Open browser"),
        TestCase("ouvre le bloc-notes", ["APP_OPEN"], ["notepad", "bloc"], notes="Open notepad"),
        TestCase("ouvre le gestionnaire des taches", ["SYSTEM_TASK_MANAGER", "APP_OPEN"], ["task"], notes="Task manager"),
        TestCase("quelles applications sont ouvertes", ["APP_LIST_RUNNING"], ["apps", "process"], notes="Running apps"),
        TestCase("ferme le bloc-notes", ["APP_CLOSE", "WINDOW_CLOSE"], ["close", "ferme"], notes="Close notepad"),
    ],
    "FILES": [
        TestCase("cherche un fichier PDF sur mon PC", ["FILE_SEARCH_TYPE", "FILE_SEARCH"], ["results", "files", "count"], notes="Find PDFs"),
        TestCase("liste le contenu du bureau", ["FOLDER_LIST"], ["items", "files", "results"], notes="Desktop listing"),
        TestCase("liste le contenu du dossier telechargements", ["FOLDER_LIST"], ["items", "files", "results"], notes="Downloads listing"),
        TestCase("cherche le fichier jarvis_memory.json", ["FILE_SEARCH"], ["results", "files", "count"], notes="Find memory file"),
    ],
    "BROWSER": [
        TestCase("ouvre chrome", ["APP_OPEN", "BROWSER_OPEN"], ["chrome"], notes="Ensure browser launched"),
        TestCase("cherche Python tutorial sur google", ["BROWSER_SEARCH", "BROWSER_FIND_AND_OPEN"], ["query", "results", "count"], notes="Google search"),
        TestCase("ouvre le premier resultat", ["BROWSER_OPEN_RESULT"], ["rank", "url", "opened"], notes="Open first search result"),
        TestCase("resume cette page", ["BROWSER_SUMMARIZE", "BROWSER_READ"], ["summary", "title", "url"], notes="Summarize page"),
        TestCase("liste les onglets", ["BROWSER_LIST_TABS"], ["tabs", "count"], notes="List browser tabs"),
        TestCase("ouvre un nouvel onglet", ["BROWSER_NEW_TAB"], ["tab", "id", "url"], notes="Create tab"),
        TestCase("va sur youtube", ["BROWSER_GO_TO_SITE", "BROWSER_URL", "BROWSER_NAVIGATE"], ["youtube", "url"], notes="Navigate youtube"),
        TestCase("cherche sur youtube lofi music", ["BROWSER_SEARCH_YOUTUBE", "BROWSER_SEARCH"], ["query", "results"], notes="YouTube search"),
    ],
    "MUSIC": [
        TestCase("analyse mon dossier musique", ["MUSIC_LIBRARY_SCAN"], ["tracks", "count", "path"], notes="Scan music folder"),
        TestCase("joue une musique", ["MUSIC_PLAY", "AUDIO_PLAY"], ["track", "playing", "query"], notes="Play music"),
        TestCase("pause la musique", ["MUSIC_PAUSE"], ["pause", "state"], notes="Pause music"),
        TestCase("musique suivante", ["MUSIC_NEXT"], ["next", "track"], notes="Next track"),
        TestCase("mets le volume a 70%", ["AUDIO_VOLUME_SET", "MUSIC_VOLUME"], ["volume", "level"], notes="Set volume 70", manual_check=True),
        TestCase("liste mes playlists", ["MUSIC_PLAYLIST_LIST"], ["playlists", "count"], notes="List playlists"),
        TestCase("renomme la playlist coding hit en coding pro", ["MUSIC_PLAYLIST_RENAME"], ["old_name", "new_name"], notes="Rename playlist"),
        TestCase("duplique la playlist coding hit en coding backup", ["MUSIC_PLAYLIST_DUPLICATE"], ["source", "target"], notes="Duplicate playlist"),
        TestCase("fusionne la playlist coding hit avec coding backup dans coding merged", ["MUSIC_PLAYLIST_MERGE"], ["source", "target", "output"], notes="Merge playlists"),
        TestCase("deplace moonlight dans ma playlist coding merged en position 1", ["MUSIC_PLAYLIST_MOVE_SONG"], ["name", "query", "to_index"], notes="Move song in playlist"),
        TestCase("exporte la playlist coding hit en json", ["MUSIC_PLAYLIST_EXPORT"], ["path", "format", "count"], notes="Export playlist"),
        TestCase("importe la playlist coding hit depuis C:/tmp/demo.m3u", ["MUSIC_PLAYLIST_IMPORT"], ["source", "mode", "added"], notes="Import playlist"),
        TestCase("ajoute blinding lights a la file d attente", ["MUSIC_QUEUE_ADD"], ["query", "queue", "size"], notes="Queue add song"),
        TestCase("ajoute la playlist coding merged a la file d attente", ["MUSIC_QUEUE_ADD_PLAYLIST"], ["name", "added", "size"], notes="Queue add playlist"),
        TestCase("liste la file d attente", ["MUSIC_QUEUE_LIST"], ["queue", "size"], notes="Queue list"),
        TestCase("lance la file d attente", ["MUSIC_QUEUE_PLAY"], ["track", "playing", "queue"], notes="Queue play"),
        TestCase("vide la file d attente", ["MUSIC_QUEUE_CLEAR"], ["cleared", "size", "queue"], notes="Queue clear"),
    ],
    "HISTORY_MACROS": [
        TestCase("historique", ["HISTORY_SHOW"], ["history", "commands", "entries"], notes="Show history"),
        TestCase("mes 5 dernieres commandes", ["HISTORY_SHOW"], ["history", "count"], notes="Last 5 commands"),
        TestCase("liste les macros", ["MACRO_LIST"], ["macros", "count"], notes="List macros"),
        TestCase("lance la macro mode nuit", ["MACRO_RUN"], ["mode nuit", "macro"], notes="Run mode nuit"),
        TestCase("repete", ["REPEAT_LAST"], ["repeat", "last"], notes="Repeat last command"),
    ],
    "NETWORK": [
        TestCase("liste les reseaux wifi", ["WIFI_LIST"], ["wifi", "networks", "count"], notes="Visible wifi list"),
        TestCase("infos reseau", ["NETWORK_INFO", "SYSTEM_NETWORK"], ["ip", "network"], notes="Network info"),
        TestCase("active le bluetooth", ["BLUETOOTH_ENABLE"], ["bluetooth", "enabled", "state"], notes="Enable BT", manual_check=True),
        TestCase("desactive le bluetooth", ["BLUETOOTH_DISABLE"], ["bluetooth", "disabled", "state"], notes="Disable BT", manual_check=True),
    ],
    "SCREEN": [
        TestCase("capture d'ecran", ["SCREEN_CAPTURE"], ["path", "screenshot", "file"], notes="Take screenshot"),
        TestCase("mets la luminosite a 70%", ["SCREEN_BRIGHTNESS"], ["brightness", "level"], notes="Set brightness", manual_check=True),
        TestCase("envoie la capture au telephone", ["SCREENSHOT_TO_PHONE"], ["phone", "sent", "notification"], notes="Send to phone"),
    ],
    "MEMORY_CONTEXT": [
        TestCase("ouvre chrome", ["APP_OPEN", "BROWSER_OPEN"], ["chrome"], notes="Context step 1"),
        TestCase("ferme-le", ["APP_CLOSE", "WINDOW_CLOSE", "BROWSER_CLOSE"], ["close", "ferme"], notes="Context step 2 pronoun resolution"),
        TestCase("cherche python sur google", ["BROWSER_SEARCH", "BROWSER_FIND_AND_OPEN"], ["python", "results"], notes="Context step 3 search"),
        TestCase("ouvre le deuxieme resultat", ["BROWSER_OPEN_RESULT"], ["rank", "2", "result"], notes="Context step 4 uses previous search"),
        TestCase("mon prenom est Christophe", ["KNOWLEDGE_QA", "UNKNOWN", "HELP"], ["Christophe", "prenom"], notes="Context step 5 personal memory"),
        TestCase("tu te souviens de mon prenom ?", ["KNOWLEDGE_QA", "MEMORY_SHOW", "UNKNOWN"], ["Christophe", "prenom"], notes="Context step 6 recall"),
    ],
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full Jarvis command audit battery")
    parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Execute dangerous commands (shutdown/lock). Default is skip.",
    )
    parser.add_argument(
        "--groups",
        type=str,
        default="",
        help="Comma-separated groups to run. Example: SYSTEM,AUDIO,FILES",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="",
        help="Optional report directory. Default: JarvisDesktop/data/test_reports",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop at first unhandled exception.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute commands; only print planned execution order.",
    )
    return parser.parse_args()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_lower(value: Any) -> str:
    try:
        return str(value).lower()
    except Exception:
        return ""


def _contains_any(blob: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    blob_l = blob.lower()
    return any(tok.lower() in blob_l for tok in tokens)


def _evaluate_case(case: TestCase, result: dict[str, Any] | None, skipped: bool, skip_reason: str = "") -> dict[str, Any]:
    if skipped:
        return {
            "status": "SKIPPED",
            "reason": skip_reason,
            "checks": {
                "intent_match": False,
                "success_true": False,
                "expected_data_present": False,
            },
        }

    if result is None:
        return {
            "status": "FAIL",
            "reason": "No result returned.",
            "checks": {
                "intent_match": False,
                "success_true": False,
                "expected_data_present": False,
            },
        }

    intent = _safe_lower(result.get("_intent", ""))
    success = bool(result.get("success", False))
    data = result.get("data", {})
    message = result.get("message", "")

    expected_intents_l = [_safe_lower(v) for v in case.expected_intents]
    intent_match = (not expected_intents_l) or intent in expected_intents_l

    joined_blob = "\n".join([
        _safe_lower(message),
        _safe_lower(json.dumps(data, ensure_ascii=True, default=str)),
    ])
    expected_data_present = _contains_any(joined_blob, case.expected_data_keys)

    checks = {
        "intent_match": intent_match,
        "success_true": success,
        "expected_data_present": expected_data_present,
    }

    if all(checks.values()):
        status = "PASS"
        reason = "All checks passed."
    else:
        status = "FAIL"
        failed = [k for k, v in checks.items() if not v]
        reason = "Failed checks: " + ", ".join(failed)

    return {
        "status": status,
        "reason": reason,
        "checks": checks,
    }


def _compute_group_analytics(groups: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    module_scores: list[dict[str, Any]] = []

    for group in groups:
        name = group.get("name", "UNKNOWN")
        results = group.get("results", [])
        executed = [r for r in results if r.get("evaluation", {}).get("status") != "SKIPPED"]
        passed = [r for r in executed if r.get("evaluation", {}).get("status") == "PASS"]
        failed = [r for r in executed if r.get("evaluation", {}).get("status") == "FAIL"]
        skipped = [r for r in results if r.get("evaluation", {}).get("status") == "SKIPPED"]

        executed_count = len(executed)
        pass_count = len(passed)
        fail_count = len(failed)
        skip_count = len(skipped)

        reliability_pct = round((pass_count / executed_count) * 100, 1) if executed_count else 0.0
        failure_rate_pct = round((fail_count / executed_count) * 100, 1) if executed_count else 0.0

        latencies = [int(r.get("duration_ms", 0)) for r in executed]
        avg_latency_ms = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
        max_latency_ms = max(latencies) if latencies else 0

        # Composite health score prioritizes correctness, then speed.
        speed_score = max(0.0, 100.0 - min(avg_latency_ms / 50.0, 100.0))
        health_score = round((0.75 * reliability_pct) + (0.25 * speed_score), 1)

        failed_checks = {
            "intent_match": 0,
            "success_true": 0,
            "expected_data_present": 0,
        }
        for item in failed:
            checks = item.get("evaluation", {}).get("checks", {})
            for key in failed_checks:
                if checks.get(key) is False:
                    failed_checks[key] += 1

        dominant_failure = "none"
        if fail_count:
            dominant_failure = max(failed_checks, key=failed_checks.get)

        module_scores.append(
            {
                "group": name,
                "executed": executed_count,
                "pass": pass_count,
                "fail": fail_count,
                "skipped": skip_count,
                "reliability_pct": reliability_pct,
                "failure_rate_pct": failure_rate_pct,
                "avg_latency_ms": avg_latency_ms,
                "max_latency_ms": max_latency_ms,
                "health_score": health_score,
                "dominant_failure": dominant_failure,
                "failed_checks": failed_checks,
            }
        )

    remediation_priority = sorted(
        module_scores,
        key=lambda m: (
            -int(m.get("fail", 0)),
            -float(m.get("failure_rate_pct", 0.0)),
            -float(m.get("avg_latency_ms", 0.0)),
        ),
    )

    priorities: list[dict[str, Any]] = []
    for rank, module in enumerate(remediation_priority, start=1):
        if module.get("fail", 0) == 0:
            continue
        dominant = module.get("dominant_failure", "none")
        if dominant == "intent_match":
            action = "Improve parser mapping / intent disambiguation for this module."
        elif dominant == "success_true":
            action = "Focus on runtime execution failures and platform permissions."
        elif dominant == "expected_data_present":
            action = "Stabilize response payload schema and required data fields."
        else:
            action = "Review failing commands and module-level logs."

        priorities.append(
            {
                "rank": rank,
                "group": module.get("group"),
                "fail": module.get("fail"),
                "failure_rate_pct": module.get("failure_rate_pct"),
                "avg_latency_ms": module.get("avg_latency_ms"),
                "dominant_failure": dominant,
                "recommended_action": action,
            }
        )

    return module_scores, priorities


def _build_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Jarvis Command Audit Report")
    lines.append("")
    lines.append(f"- Started: {report.get('started_at')}")
    lines.append(f"- Finished: {report.get('finished_at')}")
    lines.append(f"- Host: {report.get('environment', {}).get('host')}")
    lines.append(f"- OS: {report.get('environment', {}).get('os')}")
    lines.append(f"- Python: {report.get('environment', {}).get('python')}")
    lines.append(f"- Groq parser available: {report.get('environment', {}).get('groq_available')}")
    lines.append("")

    summary = report.get("summary", {})
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total: {summary.get('total', 0)}")
    lines.append(f"- PASS: {summary.get('pass', 0)}")
    lines.append(f"- FAIL: {summary.get('fail', 0)}")
    lines.append(f"- SKIPPED: {summary.get('skipped', 0)}")
    lines.append("")

    lines.append("## Module Scores")
    lines.append("")
    lines.append("| Module | Health | Reliability | Failure Rate | Avg Latency (ms) | Executed | Fail |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for module in summary.get("module_scores", []):
        lines.append(
            "| {group} | {health:.1f} | {rel:.1f}% | {fr:.1f}% | {lat:.1f} | {exe} | {fail} |".format(
                group=module.get("group", ""),
                health=float(module.get("health_score", 0.0)),
                rel=float(module.get("reliability_pct", 0.0)),
                fr=float(module.get("failure_rate_pct", 0.0)),
                lat=float(module.get("avg_latency_ms", 0.0)),
                exe=int(module.get("executed", 0)),
                fail=int(module.get("fail", 0)),
            )
        )
    lines.append("")

    lines.append("## Priority Queue")
    lines.append("")
    priorities = summary.get("priority_queue", [])
    if priorities:
        for item in priorities:
            lines.append(
                "{rank}. {group} - fail={fail}, failure_rate={rate}%, avg_latency={lat}ms, dominant_failure={dom}. Action: {action}".format(
                    rank=item.get("rank"),
                    group=item.get("group"),
                    fail=item.get("fail"),
                    rate=item.get("failure_rate_pct"),
                    lat=item.get("avg_latency_ms"),
                    dom=item.get("dominant_failure"),
                    action=item.get("recommended_action"),
                )
            )
    else:
        lines.append("- No failing module detected.")
    lines.append("")

    for group in report.get("groups", []):
        lines.append(f"## Group: {group.get('name')}")
        lines.append("")
        lines.append("| # | Command | Status | Intent | Success | Why |")
        lines.append("|---|---|---|---|---|---|")

        for idx, item in enumerate(group.get("results", []), start=1):
            status = item.get("evaluation", {}).get("status", "")
            intent = item.get("response", {}).get("_intent", "")
            success = item.get("response", {}).get("success", False)
            reason = item.get("evaluation", {}).get("reason", "")
            command = item.get("command", "").replace("|", "\\|")
            lines.append(
                f"| {idx} | {command} | {status} | {intent} | {success} | {reason} |"
            )

        lines.append("")
        lines.append("### Detailed responses")
        lines.append("")
        for idx, item in enumerate(group.get("results", []), start=1):
            response = item.get("response", {})
            lines.append(f"{idx}. Command: {item.get('command')}")
            lines.append(f"   - assistant_message: {response.get('message', '')}")
            lines.append(f"   - intent: {response.get('_intent', '')}")
            lines.append(f"   - confidence: {response.get('_confidence', '')}")
            lines.append(f"   - source: {response.get('_source', '')}")
            lines.append(f"   - success: {response.get('success', '')}")
            lines.append(f"   - duration_ms: {item.get('duration_ms', '')}")
            if item.get("manual_check"):
                lines.append("   - manual_check: true")
            if item.get("dangerous"):
                lines.append("   - dangerous: true")
            if item.get("evaluation", {}).get("status") == "SKIPPED":
                lines.append(f"   - skipped_reason: {item.get('evaluation', {}).get('reason', '')}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    selected_groups = []
    if args.groups.strip():
        selected_groups = [g.strip().upper() for g in args.groups.split(",") if g.strip()]
        unknown = [g for g in selected_groups if g not in SUITES]
        if unknown:
            raise ValueError(f"Unknown groups: {', '.join(unknown)}")
    else:
        selected_groups = list(SUITES.keys())

    agent = Agent()

    started = _now_iso()
    report: dict[str, Any] = {
        "started_at": started,
        "finished_at": "",
        "environment": {
            "host": platform.node(),
            "os": platform.platform(),
            "python": platform.python_version(),
            "cwd": os.getcwd(),
            "groq_available": bool(getattr(agent.parser, "ai_available", False)),
        },
        "options": {
            "allow_dangerous": bool(args.allow_dangerous),
            "groups": selected_groups,
        },
        "groups": [],
        "summary": {},
    }

    count_pass = 0
    count_fail = 0
    count_skip = 0

    for group_name in selected_groups:
        group_results = []
        for case in SUITES[group_name]:
            if case.dangerous and not args.allow_dangerous:
                evaluation = _evaluate_case(case, result=None, skipped=True, skip_reason="Dangerous command skipped by policy. Use --allow-dangerous to execute.")
                count_skip += 1
                group_results.append(
                    {
                        "timestamp": _now_iso(),
                        "command": case.command,
                        "notes": case.notes,
                        "dangerous": case.dangerous,
                        "manual_check": case.manual_check,
                        "expected": asdict(case),
                        "response": {},
                        "duration_ms": 0,
                        "evaluation": evaluation,
                    }
                )
                continue

            start = time.perf_counter()
            result: dict[str, Any] | None = None
            unhandled_error = ""

            try:
                result = agent.handle_command(case.command)
            except Exception as exc:
                unhandled_error = f"Unhandled exception: {exc}"
                if args.stop_on_error:
                    raise

            duration_ms = int((time.perf_counter() - start) * 1000)

            if unhandled_error:
                response = {
                    "success": False,
                    "message": unhandled_error,
                    "data": {},
                    "_intent": "EXCEPTION",
                    "_confidence": 0.0,
                    "_source": "runner",
                }
            else:
                response = result or {
                    "success": False,
                    "message": "No result returned by agent.",
                    "data": {},
                    "_intent": "NO_RESULT",
                    "_confidence": 0.0,
                    "_source": "runner",
                }

            evaluation = _evaluate_case(case, response, skipped=False)
            status = evaluation.get("status")
            if status == "PASS":
                count_pass += 1
            elif status == "FAIL":
                count_fail += 1
            else:
                count_skip += 1

            group_results.append(
                {
                    "timestamp": _now_iso(),
                    "command": case.command,
                    "notes": case.notes,
                    "dangerous": case.dangerous,
                    "manual_check": case.manual_check,
                    "expected": asdict(case),
                    "response": response,
                    "duration_ms": duration_ms,
                    "evaluation": evaluation,
                }
            )

        report["groups"].append({"name": group_name, "results": group_results})

    report["finished_at"] = _now_iso()
    module_scores, priorities = _compute_group_analytics(report.get("groups", []))
    report["summary"] = {
        "total": count_pass + count_fail + count_skip,
        "pass": count_pass,
        "fail": count_fail,
        "skipped": count_skip,
        "module_scores": module_scores,
        "priority_queue": priorities,
    }

    return report


def _write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"jarvis_audit_{ts}.json"
    md_path = output_dir / f"jarvis_audit_{ts}.md"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    md_path.write_text(_build_markdown_report(report), encoding="utf-8")

    return json_path, md_path


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else (ROOT_DIR / "data" / "test_reports")

    if args.dry_run:
        selected_groups = [g.strip().upper() for g in args.groups.split(",") if g.strip()] if args.groups.strip() else list(SUITES.keys())
        print("=" * 72)
        print("JARVIS FULL COMMAND AUDIT (DRY RUN)")
        print("=" * 72)
        for group in selected_groups:
            print(f"\n[{group}]")
            for idx, case in enumerate(SUITES[group], start=1):
                flags = []
                if case.dangerous:
                    flags.append("dangerous")
                if case.manual_check:
                    flags.append("manual")
                suffix = f" ({', '.join(flags)})" if flags else ""
                print(f"{idx:02d}. {case.command}{suffix}")
        print("\nNo command executed. Remove --dry-run to run the full audit.")
        print("=" * 72)
        return 0

    report = run_audit(args)
    json_path, md_path = _write_reports(report, output_dir)

    summary = report.get("summary", {})
    print("=" * 72)
    print("JARVIS FULL COMMAND AUDIT")
    print("=" * 72)
    print(f"Total   : {summary.get('total', 0)}")
    print(f"PASS    : {summary.get('pass', 0)}")
    print(f"FAIL    : {summary.get('fail', 0)}")
    print(f"SKIPPED : {summary.get('skipped', 0)}")
    top_priorities = summary.get("priority_queue", [])[:3]
    if top_priorities:
        print("Top priorities:")
        for p in top_priorities:
            print(
                f"  - {p.get('group')}: fail={p.get('fail')} "
                f"rate={p.get('failure_rate_pct')}% latency={p.get('avg_latency_ms')}ms"
            )
    print(f"JSON    : {json_path}")
    print(f"MD      : {md_path}")
    print("=" * 72)

    return 0 if summary.get("fail", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
