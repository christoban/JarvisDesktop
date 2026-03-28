#!/usr/bin/env python3
"""
test_semaine14.py — Tests Semaine 14 : App Control + Screen Share

Groupes :
  G1. Word Manager — création CV, rapports, documents
  G2. Excel Manager — création feuilles, tableaux
  G3. Email Outlook — lecture, envoi, recherche
  G4. Screen Share — capture, streaming, status
  G5. Workflows — execution, liste, creation
  G6. Macro Recorder — record, stop, replay

Usage :
    cd JarvisDesktop
    python tests/test_semaine14.py
    pytest tests/test_semaine14.py -v
"""

import sys
import os
import platform
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PASS = 0
FAIL = 0
SKIP = 0
SYSTEM = platform.system()


def _run(name, fn, expect=True, skip_reason=None):
    global PASS, FAIL, SKIP
    if skip_reason:
        SKIP += 1
        print(f"  SKIP {name} ({skip_reason})")
        print()
        return
    try:
        result = fn()
        ok = result.get("success", result.get("ok", False))
        msg = result.get("message", str(result))[:80]
        passed = (expect is None) or (bool(ok) == bool(expect))
        if passed:
            PASS += 1
        else:
            FAIL += 1
        icon = "OK" if passed else "FAIL"
        note = f"  (attendu success={expect})" if not passed else ""
        print(f"  {icon} {name}{note}")
        print(f"       -> {msg}")
    except Exception as e:
        FAIL += 1
        print(f"  FAIL {name}")
        print(f"       Exception : {e}")
    print()


def _sep(title):
    print(f"{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}\n")


# ════════════════════════════════════════════════════════════════════════
#  G1 — Word Manager
# ════════════════════════════════════════════════════════════════════════

def _test_g1_word():
    _sep("GROUPE 1 — Word Manager")

    # Test création document simple
    def t1():
        with patch('modules.word_manager.DOCX_AVAILABLE', True):
            with patch('builtins.open', MagicMock()):
                with patch('docx.Document') as MockDoc:
                    MockDoc.return_value = MagicMock()
                    from modules.word_manager import WordManager
                    wm = WordManager(output_dir=tempfile.gettempdir())
                    return wm.create_document(
                        title="Test Document",
                        sections=[{"heading": "Intro", "content": "Hello"}],
                        open_after=False
                    )
    _run("Word: create_document", t1)

    # Test création CV sans python-docx
    def t2():
        with patch('modules.word_manager.DOCX_AVAILABLE', False):
            from modules.word_manager import WordManager
            wm = WordManager()
            return wm.create_cv({"name": "Test"}, open_after=False)
    _run("Word: create_cv sans docx", t2, expect=False)

    # Test export PDF sans win32
    def t3():
        with patch('modules.word_manager.WIN32_AVAILABLE', False):
            with patch('modules.word_manager.WIN32_AVAILABLE', False):
                from modules.word_manager import WordManager
                wm = WordManager()
                return wm.export_to_pdf("test.docx")
    _run("Word: export_pdf fallback", t3, expect=False)


# ════════════════════════════════════════════════════════════════════════
#  G2 — Excel Manager
# ════════════════════════════════════════════════════════════════════════

def _test_g2_excel():
    _sep("GROUPE 2 — Excel Manager")

    def t1():
        try:
            from modules.excel_manager import ExcelManager
            return {"success": True, "message": "ExcelManager import OK"}
        except ImportError as e:
            return {"success": False, "message": f"Import error: {e}"}
    _run("Excel: import", t1)

    def t2():
        try:
            from modules.excel_manager import ExcelManager
            em = ExcelManager(output_dir=tempfile.gettempdir())
            return em.create_sheet("TestSheet", [{"col1": "val1"}], open_after=False)
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Excel: create_sheet", t2)


# ════════════════════════════════════════════════════════════════════════
#  G3 — Email Outlook
# ════════════════════════════════════════════════════════════════════════

def _test_g3_email():
    _sep("GROUPE 3 — Email Outlook")

    def t1():
        with patch('win32com.client.Dispatch', MagicMock()):
            try:
                from core.email_outlook import OutlookEmail
                return {"success": True, "message": "OutlookEmail import OK"}
            except ImportError as e:
                return {"success": False, "message": f"Import error: {e}"}
    _run("Email: import OutlookEmail", t1)

    def t2():
        with patch('win32com.client.Dispatch') as mock_dispatch:
            mock_dispatch.return_value = MagicMock()
            try:
                from core.email_outlook import OutlookEmail
                oe = OutlookEmail()
                return {"success": oe.is_connected, "message": f"Connected: {oe.is_connected}"}
            except Exception as e:
                return {"success": False, "message": str(e)[:50]}
    _run("Email: Outlook connection", t2)

    def t3():
        with patch('win32com.client.Dispatch') as mock_dispatch:
            mock_namespace = MagicMock()
            mock_namespace.GetDefaultFolder.return_value = MagicMock()
            mock_dispatch.return_value.GetNamespace.return_value = mock_namespace
            from core.email_outlook import OutlookEmail
            oe = OutlookEmail()
            inbox = oe.get_inbox(limit=5)
            return {"success": True, "message": f"Got {len(inbox)} emails"}
    _run("Email: get_inbox", t3)


# ════════════════════════════════════════════════════════════════════════
#  G4 — Screen Share
# ════════════════════════════════════════════════════════════════════════

def _test_g4_screen():
    _sep("GROUPE 4 — Screen Share")

    def t1():
        try:
            from modules.screen_share.capture import ScreenCapture
            return {"success": True, "message": "ScreenCapture import OK"}
        except ImportError as e:
            return {"success": False, "message": f"Import error: {e}"}
    _run("Screen: import ScreenCapture", t1)

    def t2():
        try:
            from modules.screen_share.capture import ScreenCapture
            cap = ScreenCapture(fps=5)
            return {"success": True, "message": f"Created with fps={cap.fps}"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Screen: create instance", t2)

    def t3():
        try:
            from modules.screen_share.capture import ScreenCapture
            cap = ScreenCapture(fps=1)
            status = cap.get_stats()
            return {"success": True, "message": f"Stats: running={status.get('running')}"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Screen: get_stats", t3)


# ════════════════════════════════════════════════════════════════════════
#  G5 — Workflows
# ════════════════════════════════════════════════════════════════════════

def _test_g5_workflows():
    _sep("GROUPE 5 — Workflows")

    def t1():
        try:
            from core.workflow_engine import WorkflowEngine
            return {"success": True, "message": "WorkflowEngine import OK"}
        except ImportError as e:
            return {"success": False, "message": f"Import error: {e}"}
    _run("Workflow: import", t1)

    def t2():
        from core.workflow_engine import WorkflowEngine
        we = WorkflowEngine()
        return we.list_workflows()
    _run("Workflow: list", t2)

    def t3():
        from core.workflow_engine import WorkflowEngine
        we = WorkflowEngine()
        wf = we.get_workflow("postule emploi")
        return wf
    _run("Workflow: get postule emploi", t3)

    def t4():
        from core.workflow_engine import WorkflowEngine
        we = WorkflowEngine()
        result = we.register_workflow(
            name="test workflow",
            steps=[{"type": "delay", "params": {"seconds": 0}, "description": "test"}],
            description="Test workflow"
        )
        return result
    _run("Workflow: register", t4)

    def t5():
        from core.workflow_engine import WorkflowEngine
        we = WorkflowEngine()
        return we.delete_workflow("test workflow")
    _run("Workflow: delete", t5)


# ════════════════════════════════════════════════════════════════════════
#  G6 — Macro Recorder
# ════════════════════════════════════════════════════════════════════════

def _test_g6_recorder():
    _sep("GROUPE 6 — Macro Recorder")

    def t1():
        try:
            from core.workflow_engine import MacroRecorder
            return {"success": True, "message": "MacroRecorder import OK"}
        except ImportError as e:
            return {"success": False, "message": f"Import error: {e}"}
    _run("Recorder: import", t1)

    def t2():
        from core.workflow_engine import MacroRecorder
        mr = MacroRecorder()
        mr.start("test_recording")
        return {"success": mr.is_recording, "message": f"Recording: {mr.is_recording}"}
    _run("Recorder: start", t2)

    def t3():
        from core.workflow_engine import MacroRecorder
        mr = MacroRecorder()
        mr.start("test_recording")
        mr.record_action("APP_OPEN", {"app_name": "chrome"}, "ouvre chrome")
        mr.record_action("MUSIC_PLAY", {"query": "lofi"}, "joue de la musique")
        return {"success": mr.action_count == 2, "message": f"Actions: {mr.action_count}"}
    _run("Recorder: record_action", t3)

    def t4():
        from core.workflow_engine import MacroRecorder
        mr = MacroRecorder()
        mr.start("test_recording")
        mr.record_action("APP_OPEN", {"app_name": "chrome"}, "ouvre chrome")
        result = mr.stop("macro_test", "Test macro")
        return result
    _run("Recorder: stop & save", t4)


# ════════════════════════════════════════════════════════════════════════
#  G7 — Intents Integration
# ════════════════════════════════════════════════════════════════════════

def _test_g7_intents():
    _sep("GROUPE 7 — Intents Integration")

    def t1():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_workflow = "WORKFLOW_RUN" in ie._handlers
        return {"success": has_workflow, "message": f"WORKFLOW_RUN: {has_workflow}"}
    _run("Intent: WORKFLOW_RUN exists", t1)

    def t2():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_list = "WORKFLOW_LIST" in ie._handlers
        return {"success": has_list, "message": f"WORKFLOW_LIST: {has_list}"}
    _run("Intent: WORKFLOW_LIST exists", t2)

    def t3():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_record = "RECORD_START" in ie._handlers
        return {"success": has_record, "message": f"RECORD_START: {has_record}"}
    _run("Intent: RECORD_START exists", t3)

    def t4():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_record_stop = "RECORD_STOP" in ie._handlers
        return {"success": has_record_stop, "message": f"RECORD_STOP: {has_record_stop}"}
    _run("Intent: RECORD_STOP exists", t4)

    def t5():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_email = "EMAIL_SEND" in ie._handlers
        return {"success": has_email, "message": f"EMAIL_SEND: {has_email}"}
    _run("Intent: EMAIL_SEND exists", t5)


# ════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════

def main():
    print()
    print("="*60)
    print("  JARVIS — Tests Semaine 14")
    print("  App Control + Screen Share + Workflows")
    print("="*60)
    print()

    _test_g1_word()
    _test_g2_excel()
    _test_g3_email()
    _test_g4_screen()
    _test_g5_workflows()
    _test_g6_recorder()
    _test_g7_intents()

    print()
    print("="*60)
    print(f"  RESUME : OK {PASS}  FAIL {FAIL}  SKIP {SKIP}")
    print("="*60)
    print()

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
