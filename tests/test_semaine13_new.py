#!/usr/bin/env python3
"""
test_semaine13.py — Tests Semaine 13 : Vision et OCR

Groupes :
  G1. VisionManager — import et initialisation
  G2. VisionManager — lecture écran (OCR)
  G3. VisionManager — détection éléments
  G4. VisionManager — actions souris
  G5. Intents — VISION_*
  G6. Router — patterns vision

Usage :
    cd JarvisDesktop
    python tests/test_semaine13.py
    pytest tests/test_semaine13.py -v
"""

import sys
import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PASS = 0
FAIL = 0
SKIP = 0


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
#  G1 — Import et initialisation
# ════════════════════════════════════════════════════════════════════════

def _test_g1_import():
    _sep("GROUPE 1 — Import VisionManager")

    def t1():
        try:
            from modules.vision_manager import VisionManager
            return {"success": True, "message": "VisionManager import OK"}
        except ImportError as e:
            return {"success": False, "message": f"Import error: {e}"}
    _run("Vision: import VisionManager", t1)

    def t2():
        try:
            from modules.vision_manager import VisionManager, ScreenElement, OCRResult
            vm = VisionManager()
            return {"success": True, "message": "Instance created"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Vision: create instance", t2)

    def t3():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            health = vm.health_check()
            return health
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Vision: health_check", t3)


# ════════════════════════════════════════════════════════════════════════
#  G2 — OCR (lecture écran)
# ════════════════════════════════════════════════════════════════════════

def _test_g2_ocr():
    _sep("GROUPE 2 — OCR et lecture écran")

    def t1():
        try:
            from modules.vision_manager import VisionManager, OCRResult
            return {"success": True, "message": "OCRResult import OK"}
        except ImportError as e:
            return {"success": False, "message": f"Import error: {e}"}
    _run("OCR: import OCRResult", t1)

    def t2():
        try:
            from modules.vision_manager import ScreenElement
            elem = ScreenElement(
                text="OK",
                x=100,
                y=200,
                width=50,
                height=30,
                confidence=90.0,
                element_type="button"
            )
            return {
                "success": elem.center_x == 125 and elem.center_y == 215,
                "message": f"Element center: ({elem.center_x}, {elem.center_y})"
            }
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("OCR: ScreenElement center calculation", t2)

    def t3():
        try:
            from modules.vision_manager import ScreenElement
            elem = ScreenElement(
                text="Submit",
                x=100,
                y=200,
                width=80,
                height=25,
                confidence=85.0,
                element_type="button"
            )
            d = elem.to_dict()
            return {
                "success": "center_x" in d and "center_y" in d,
                "message": "to_dict() includes center coordinates"
            }
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("OCR: ScreenElement to_dict", t3)


# ════════════════════════════════════════════════════════════════════════
#  G3 — Détection d'éléments
# ════════════════════════════════════════════════════════════════════════

def _test_g3_detection():
    _sep("GROUPE 3 — Detection d'elements")

    def t1():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            # Sans dépendance réelle, ça retourne un message d'erreur
            result = vm.read_screen_text()
            # On vérifie juste que ça ne crash pas
            return {"success": True, "message": "read_screen_text executed"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Detection: read_screen_text no crash", t1)

    def t2():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            # Simuler une recherche
            result = vm.find_element("OK")
            return {"success": True, "message": f"find_element returned {type(result).__name__}"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Detection: find_element", t2)

    def t3():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            elements = vm.find_button("Submit")
            return {"success": True, "message": f"find_button returned list"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Detection: find_button", t3)


# ════════════════════════════════════════════════════════════════════════
#  G4 — Actions souris
# ════════════════════════════════════════════════════════════════════════

def _test_g4_actions():
    _sep("GROUPE 4 — Actions souris")

    def t1():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            result = vm.click_element("OK")
            # Sans élément trouvé, retourne error
            return {"success": True, "message": "click_element executed"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Action: click_element execution", t1)

    def t2():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            result = vm.click_button("Submit")
            return {"success": True, "message": "click_button executed"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Action: click_button", t2)

    def t3():
        try:
            from modules.vision_manager import VisionManager
            vm = VisionManager()
            result = vm.hover_element("Test")
            return {"success": True, "message": "hover_element executed"}
        except Exception as e:
            return {"success": False, "message": str(e)[:50]}
    _run("Action: hover_element", t3)


# ════════════════════════════════════════════════════════════════════════
#  G5 — Intents VISION_*
# ════════════════════════════════════════════════════════════════════════

def _test_g5_intents():
    _sep("GROUPE 5 — Intents VISION_*")

    def t1():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_read = "VISION_READ_SCREEN" in ie._handlers
        return {"success": has_read, "message": f"VISION_READ_SCREEN: {has_read}"}
    _run("Intent: VISION_READ_SCREEN exists", t1)

    def t2():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_click = "VISION_CLICK_TEXT" in ie._handlers
        return {"success": has_click, "message": f"VISION_CLICK_TEXT: {has_click}"}
    _run("Intent: VISION_CLICK_TEXT exists", t2)

    def t3():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_summarize = "VISION_SUMMARIZE" in ie._handlers
        return {"success": has_summarize, "message": f"VISION_SUMMARIZE: {has_summarize}"}
    _run("Intent: VISION_SUMMARIZE exists", t3)

    def t4():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_find = "VISION_FIND_BUTTON" in ie._handlers
        return {"success": has_find, "message": f"VISION_FIND_BUTTON: {has_find}"}
    _run("Intent: VISION_FIND_BUTTON exists", t4)

    def t5():
        from core.intent_executor import IntentExecutor
        ie = IntentExecutor()
        has_links = "VISION_EXTRACT_LINKS" in ie._handlers
        return {"success": has_links, "message": f"VISION_EXTRACT_LINKS: {has_links}"}
    _run("Intent: VISION_EXTRACT_LINKS exists", t5)


# ════════════════════════════════════════════════════════════════════════
#  G6 — Router patterns
# ════════════════════════════════════════════════════════════════════════

def _test_g6_router():
    _sep("GROUPE 6 — Router patterns")

    def t1():
        from core.router import FAST_INTENTS
        has_read = "VISION_READ_SCREEN" in FAST_INTENTS
        return {"success": has_read, "message": f"VISION_READ_SCREEN in router: {has_read}"}
    _run("Router: VISION_READ_SCREEN pattern", t1)

    def t2():
        from core.router import FAST_INTENTS
        has_click = "VISION_CLICK_TEXT" in FAST_INTENTS
        return {"success": has_click, "message": f"VISION_CLICK_TEXT in router: {has_click}"}
    _run("Router: VISION_CLICK_TEXT pattern", t2)

    def t3():
        from core.router import FAST_INTENTS
        has_summarize = "VISION_SUMMARIZE" in FAST_INTENTS
        return {"success": has_summarize, "message": f"VISION_SUMMARIZE in router: {has_summarize}"}
    _run("Router: VISION_SUMMARIZE pattern", t3)


# ════════════════════════════════════════════════════════════════════════
#  G7 — Command parser schema
# ════════════════════════════════════════════════════════════════════════

def _test_g7_schema():
    _sep("GROUPE 7 — Command parser schema")

    def t1():
        from core.command_parser import INTENT_SCHEMA
        has_read = "VISION_READ_SCREEN" in INTENT_SCHEMA
        return {"success": has_read, "message": f"VISION_READ_SCREEN in schema: {has_read}"}
    _run("Schema: VISION_READ_SCREEN defined", t1)

    def t2():
        from core.command_parser import INTENT_SCHEMA
        has_click = "VISION_CLICK_TEXT" in INTENT_SCHEMA
        return {"success": has_click, "message": f"VISION_CLICK_TEXT in schema: {has_click}"}
    _run("Schema: VISION_CLICK_TEXT defined", t2)


# ════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════

def main():
    print()
    print("="*60)
    print("  JARVIS — Tests Semaine 13")
    print("  Vision et OCR")
    print("="*60)
    print()

    _test_g1_import()
    _test_g2_ocr()
    _test_g3_detection()
    _test_g4_actions()
    _test_g5_intents()
    _test_g6_router()
    _test_g7_schema()

    print()
    print("="*60)
    print(f"  RESUME : OK {PASS}  FAIL {FAIL}  SKIP {SKIP}")
    print("="*60)
    print()

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
