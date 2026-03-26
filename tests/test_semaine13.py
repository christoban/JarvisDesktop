#!/usr/bin/env python3
"""
test_semaine13.py — Tests Semaine 13 : Browser Control Niveaux 1-5

Groupes :
  G1. CommandParser — parsing des intents navigateur
  G2. IntentExecutor — routing browser
  G3. BrowserControl — lancement Chrome, onglets, recherche (mock CDP)
  G4. PageActions — extraction résultats, formulaires, résumé (mock)
  G5. Non-régression — semaines précédentes
    G6. File + Browser combinés (Semaine 8)

Usage :
    cd JarvisDesktop
    python tests/test_semaine13.py
    pytest tests/test_semaine13.py
"""

import sys
import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PASS  = 0
FAIL  = 0
SKIP  = 0
SYSTEM = platform.system()


def _run(name, fn, expect=True, skip_reason=None):
    global PASS, FAIL, SKIP
    if skip_reason:
        SKIP += 1
        print(f"  ⏭  {name} ({skip_reason})")
        print()
        return
    try:
        result = fn()
        ok     = result.get("success", result.get("ok", False))
        msg    = result.get("message", str(result))[:80]
        passed = (expect is None) or (bool(ok) == bool(expect))
        if passed: PASS += 1
        else:      FAIL += 1
        icon = "✅" if passed else "❌"
        note = f"  (attendu success={expect})" if not passed else ""
        print(f"  {icon} {name}{note}")
        print(f"       → {msg}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}")
        print(f"       Exception : {e}")
    print()


def _sep(title):
    print(f"{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}\n")


def _offline_parser():
    from core.command_parser import CommandParser
    p = CommandParser()
    p.ai_available = False
    p.client = None
    return p


# ════════════════════════════════════════════════════════════════
#  G1 — Parsing navigateur
# ════════════════════════════════════════════════════════════════

def _test_g1_parsing():
    _sep("GROUPE 1 — Parsing intents navigateur")
    parser = _offline_parser()

    cases = [
        ("cherche Python tutorial sur google",   "BROWSER_SEARCH"),
        ("recherche les dernières news",          "BROWSER_SEARCH"),
        ("ouvre youtube",                        "BROWSER_OPEN"),
        ("va sur github",                        "BROWSER_GO_TO_SITE"),
        ("cherche sur youtube lofi music",       "BROWSER_SEARCH_YOUTUBE"),
        ("ouvre le 2e résultat",                 "BROWSER_OPEN_RESULT"),
        ("résume cette page",                    "BROWSER_SUMMARIZE"),
        ("lis la page",                          "BROWSER_READ"),
        ("liste les onglets",                    "BROWSER_LIST_TABS"),
        ("nouvel onglet",                        "BROWSER_NEW_TAB"),
        ("ferme l'onglet",                       "BROWSER_CLOSE_TAB"),
        ("recharge la page",                     "BROWSER_RELOAD"),
        ("page précédente",                      "BROWSER_BACK"),
        ("page suivante",                        "BROWSER_FORWARD"),
        ("scrolle vers le bas",                  "BROWSER_SCROLL"),
        ("ferme le navigateur",                  "BROWSER_CLOSE"),
    ]

    global PASS, FAIL
    for cmd, expected in cases:
        parsed = parser.parse(cmd)
        intent = parsed.get("intent", "UNKNOWN")
        ok = intent == expected
        if ok: PASS += 1
        else:  FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} parse(\"{cmd}\") → {intent}{note}")
    print()


# ════════════════════════════════════════════════════════════════
#  G2 — Executor routing navigateur
# ════════════════════════════════════════════════════════════════

def _test_g2_executor():
    _sep("GROUPE 2 — IntentExecutor routing navigateur")
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()

    browser_intents = [
        "BROWSER_SEARCH", "BROWSER_URL", "BROWSER_NEW_TAB",
        "BROWSER_CLOSE_TAB", "BROWSER_RELOAD", "BROWSER_BACK",
        "BROWSER_FORWARD", "BROWSER_SCROLL", "BROWSER_READ",
        "BROWSER_SUMMARIZE", "BROWSER_LIST_TABS", "BROWSER_OPEN_RESULT",
        "BROWSER_GO_TO_SITE", "BROWSER_CLOSE",
    ]

    global PASS, FAIL
    for intent in browser_intents:
        has = intent in ex._handlers
        if has: PASS += 1
        else:   FAIL += 1
        print(f"  {'✅' if has else '❌'} Handler {intent} {'présent' if has else 'MANQUANT'}")
    print()


# ════════════════════════════════════════════════════════════════
#  G3 — BrowserControl (avec mock CDP)
# ════════════════════════════════════════════════════════════════

def _test_g3_browser_control():
    _sep("GROUPE 3 — BrowserControl (mock CDP)")

    from modules.browser.browser_control import BrowserControl

    # Mock CDPSession pour éviter d'avoir besoin de Chrome
    bc = BrowserControl()

    # Mock de la session pour les tests
    mock_tab = MagicMock()
    mock_tab.id = "tab_123"
    mock_tab.title = "Google"
    mock_tab.url = "https://www.google.com/search?q=test"

    bc._session.get_tabs = MagicMock(return_value=[mock_tab])
    bc._session.ensure_session = MagicMock(return_value={"success": True, "message": "CDP OK"})
    bc._session.navigate_tab = MagicMock(return_value={"success": True, "message": "Navigué"})
    bc._session.new_tab = MagicMock(return_value={"success": True, "message": "Nouvel onglet"})
    bc._session.close_tab_by_id = MagicMock(return_value={"success": True, "message": "Fermé"})
    bc._session.focus_tab = MagicMock(return_value={"success": True, "message": "Focus"})
    bc._session.reload_tab = MagicMock(return_value={"success": True, "message": "Rechargé"})
    bc._session.history_nav = MagicMock(return_value={"success": True, "message": "Retour"})
    bc._session.execute_js = MagicMock(return_value=[])

    _run("list_tabs() retourne les onglets",
         lambda: bc.list_tabs())

    _run("get_page_info() retourne titre + URL",
         lambda: bc.get_page_info())

    _run("new_tab() crée un onglet",
         lambda: bc.new_tab())

    _run("close_tab() sans argument ferme l'onglet actif",
         lambda: bc.close_tab())

    _run("reload_page() recharge",
         lambda: bc.reload_page())

    _run("navigate_back() retour",
         lambda: bc.navigate_back())

    _run("navigate_forward() suivant",
         lambda: bc.navigate_forward())

    _run("scroll() vers le bas",
         lambda: bc.scroll("down"))

    _run("switch_to_tab(index=1) fonctionne",
         lambda: bc.switch_to_tab(index=1))

    _run("health_check() retourne état",
         lambda: bc.health_check())

    # Test open_url
    _run("open_url() navigue vers URL",
         lambda: bc.open_url("https://www.google.com"))

    # Test dispatch
    _run("dispatch('recharge la page') OK",
         lambda: bc.dispatch("recharge la page"))


# ════════════════════════════════════════════════════════════════
#  G4 — PageActions (avec mock session)
# ════════════════════════════════════════════════════════════════

def _test_g4_page_actions():
    _sep("GROUPE 4 — PageActions (mock)")

    from modules.browser.page_actions import PageActions
    from modules.browser.cdp_session import CDPSession

    # Mock CDPSession
    mock_session = MagicMock(spec=CDPSession)
    mock_session._shared_search_results = []

    pa = PageActions(mock_session)

    mock_tab = MagicMock()
    mock_tab.id = "tab_456"
    mock_tab.title = "Google"
    mock_tab.url = "https://www.google.com/search?q=python"

    # Simuler résultats de recherche Google
    fake_results = [
        {"rank": 1, "title": "Python.org", "url": "https://www.python.org", "description": "Official Python"},
        {"rank": 2, "title": "Python Tutorial", "url": "https://docs.python.org", "description": "Docs"},
        {"rank": 3, "title": "Python GitHub", "url": "https://github.com/python", "description": "Source"},
    ]

    global PASS, FAIL

    # Test extract_search_results avec mock
    mock_session.execute_js = MagicMock(return_value=fake_results)
    _run("extract_search_results() Google → 3 résultats",
         lambda: pa._extract_google_results(mock_tab, 5))

    # Test open_search_result avec résultats stockés
    pa._last_search_results = fake_results
    _run("open_search_result(2) → Python Docs",
         lambda: pa.open_search_result(2))

    _run("open_search_result(99) → erreur",
         lambda: pa.open_search_result(99), expect=False)

    # Test scroll
    mock_session.execute_js = MagicMock(return_value=None)
    _run("scroll(down) ne plante pas",
         lambda: pa.scroll(mock_tab, "down"))

    _run("scroll(top) ne plante pas",
         lambda: pa.scroll(mock_tab, "top"))

    # Test fill_field_by_selector
    mock_session.execute_js = MagicMock(return_value={"ok": True, "tag": "INPUT"})
    _run("fill_field_by_selector('#search', 'Python')",
         lambda: pa.fill_field_by_selector(mock_tab, "#search", "Python"))

    # Test fill_field_by_label
    _run("fill_field_by_label('Nom', 'Christian')",
         lambda: pa.fill_field_by_label(mock_tab, "Nom", "Christian"))

    # Test click_text
    mock_session.execute_js = MagicMock(return_value={"ok": True, "tag": "BUTTON"})
    _run("click_text('Recherche')",
         lambda: pa.click_text(mock_tab, "Recherche"))

    # Test read_page
    mock_session.execute_js = MagicMock(return_value={
        "text": "Ceci est le contenu de la page. Python est un langage de programmation.",
        "title": "Test Page",
        "url": "https://test.com",
        "length": 72,
    })
    _run("read_page() extrait le texte",
         lambda: pa.read_page(mock_tab))

    # Test detect_blocker
    mock_session.execute_js = MagicMock(return_value={
        "captcha": False, "paywall": False, "rate_limit": False,
        "login_required": False, "blocked": False
    })
    _run("detect_blocker() → pas bloqué",
         lambda: pa.detect_blocker(mock_tab))

    # Test B14 : _last_search_results partagé via CDPSession
    pa2 = PageActions(mock_session)  # nouvelle instance
    global PASS, FAIL
    shared_ok = pa2._last_search_results == fake_results
    if shared_ok: PASS += 1
    else:         FAIL += 1
    print(f"  {'✅' if shared_ok else '❌'} [B14] _last_search_results partagé entre instances")
    print(f"       → {len(pa2._last_search_results)} résultat(s) accessibles depuis nouvelle instance")
    print()


# ════════════════════════════════════════════════════════════════
#  G5 — Non-régression
# ════════════════════════════════════════════════════════════════

def _test_g5_non_regression():
    _sep("GROUPE 5 — Non-régression")
    parser = _offline_parser()

    cases = [
        ("monte le volume",          "AUDIO_VOLUME_UP"),
        ("luminosite 70",            "SCREEN_BRIGHTNESS"),
        ("mode nuit",                "MACRO_RUN"),
        ("ouvre chrome",             "APP_OPEN"),
        ("joue la musique chill",    "MUSIC_PLAY"),
        ("musique suivante",         "MUSIC_NEXT"),
        ("liste les reseaux wifi",   "WIFI_LIST"),
        ("eteins l ordinateur",      "SYSTEM_SHUTDOWN"),
        ("infos systeme",            "SYSTEM_INFO"),
        ("repete",                   "REPEAT_LAST"),
    ]

    global PASS, FAIL
    for cmd, expected in cases:
        intent = parser.parse(cmd).get("intent", "UNKNOWN")
        ok = intent == expected
        if ok: PASS += 1
        else:  FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} \"{cmd}\" → {intent}{note}")
    print()


# ════════════════════════════════════════════════════════════════
#  G6 — File + Browser combinés (Semaine 8)
# ════════════════════════════════════════════════════════════════

def _test_g6_file_browser_combined():
    _sep("GROUPE 6 — File + Browser combinés (Semaine 8)")

    from core.command_parser import CommandParser
    from core.intent_executor import IntentExecutor

    parser = CommandParser()
    parser.ai_available = False
    parser.client = None

    parse_cases = [
        ("prépare mon dossier de candidature", "FILE_PREPARE_APPLICATION"),
        ("classifie mes documents", "FILE_CLASSIFY"),
        ("synchronise mes documents avec google drive", "FILE_SYNC_DRIVE"),
        ("trouve les pdf de cette semaine", "FILE_SEARCH_DATE"),
        ("cherche les fichiers de plus de 100 mo", "FILE_SEARCH_SIZE"),
        ("organise mon dossier téléchargements", "FILE_ORGANIZE"),
        ("trouve les doublons", "FILE_FIND_DUPLICATES"),
        ("ouvre youtube", "BROWSER_OPEN"),
    ]

    global PASS, FAIL
    for cmd, expected in parse_cases:
        intent = parser.parse(cmd).get("intent", "UNKNOWN")
        ok = intent == expected
        if ok:
            PASS += 1
        else:
            FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} parse(\"{cmd}\") → {intent}{note}")

    ex = IntentExecutor()
    required_handlers = [
        "FILE_SEARCH_DATE",
        "FILE_SEARCH_SIZE",
        "FILE_SEARCH_ADVANCED",
        "FILE_ORGANIZE",
        "FILE_BULK_RENAME",
        "FILE_FIND_DUPLICATES",
        "FILE_DELETE_DUPLICATES",
        "FILE_CLEAN",
        "FILE_CLASSIFY",
        "FILE_PREPARE_APPLICATION",
        "FILE_SYNC_DRIVE",
        "BROWSER_OPEN",
        "BROWSER_SEARCH",
    ]
    for intent in required_handlers:
        has = intent in ex._handlers
        if has:
            PASS += 1
        else:
            FAIL += 1
        print(f"  {'✅' if has else '❌'} Handler {intent} {'présent' if has else 'MANQUANT'}")
    print()


# ════════════════════════════════════════════════════════════════
#  PYTEST WRAPPERS
# ════════════════════════════════════════════════════════════════

def test_g1_browser_parsing():
    """pytest — G1 parsing navigateur"""
    from core.command_parser import CommandParser
    p = CommandParser(); p.ai_available = False; p.client = None

    cases = [
        ("cherche Python tutorial", "BROWSER_SEARCH"),
        ("résume cette page",       "BROWSER_SUMMARIZE"),
        ("liste les onglets",       "BROWSER_LIST_TABS"),
        ("recharge la page",        "BROWSER_RELOAD"),
        ("page précédente",         "BROWSER_BACK"),
    ]
    failed = []
    for cmd, expected in cases:
        intent = p.parse(cmd).get("intent", "UNKNOWN")
        if intent != expected:
            failed.append(f"parse('{cmd}') → {intent} (attendu {expected})")
    assert not failed, "\n" + "\n".join(failed)


def test_g2_browser_handlers():
    """pytest — G2 handlers navigateur"""
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()
    must_have = ["BROWSER_SEARCH", "BROWSER_URL", "BROWSER_RELOAD",
                 "BROWSER_BACK", "BROWSER_SUMMARIZE", "BROWSER_LIST_TABS"]
    missing = [i for i in must_have if i not in ex._handlers]
    assert not missing, f"Handlers manquants : {missing}"


def test_g3_browser_control_init():
    """pytest — G3 BrowserControl s'instancie sans erreur"""
    from modules.browser.browser_control import BrowserControl
    bc = BrowserControl()
    assert bc is not None
    assert bc._session is not None
    assert bc._page is not None
    assert bc._auto is not None


def test_g4_page_actions_b14():
    """pytest — G4 correction B14 : résultats partagés entre instances PageActions"""
    from modules.browser.page_actions import PageActions
    from unittest.mock import MagicMock
    mock_session = MagicMock()
    mock_session._shared_search_results = []

    pa1 = PageActions(mock_session)
    pa1._last_search_results = [{"rank": 1, "title": "Test", "url": "https://test.com", "description": ""}]

    pa2 = PageActions(mock_session)  # nouvelle instance, même session
    assert len(pa2._last_search_results) == 1, "Résultats non partagés entre instances (bug B14)"


def test_g5_non_regression():
    """pytest — G5 non-régression"""
    from core.command_parser import CommandParser
    p = CommandParser(); p.ai_available = False; p.client = None
    cases = [
        ("luminosite 70", "SCREEN_BRIGHTNESS"),
        ("mode nuit",     "MACRO_RUN"),
        ("joue la musique chill", "MUSIC_PLAY"),
    ]
    failed = []
    for cmd, expected in cases:
        intent = p.parse(cmd).get("intent", "UNKNOWN")
        if intent != expected:
            failed.append(f"'{cmd}' → {intent} (attendu {expected})")
    assert not failed, "\n" + "\n".join(failed)


def test_g6_file_browser_combined():
    """pytest — G6 file + browser combinés (Semaine 8)"""
    from core.command_parser import CommandParser
    from core.intent_executor import IntentExecutor

    p = CommandParser()
    p.ai_available = False
    p.client = None

    parse_cases = [
        ("prépare mon dossier de candidature", "FILE_PREPARE_APPLICATION"),
        ("classifie mes documents", "FILE_CLASSIFY"),
        ("synchronise mes documents avec google drive", "FILE_SYNC_DRIVE"),
        ("trouve les pdf de cette semaine", "FILE_SEARCH_DATE"),
        ("cherche les fichiers de plus de 100 mo", "FILE_SEARCH_SIZE"),
        ("organise mon dossier téléchargements", "FILE_ORGANIZE"),
        ("trouve les doublons", "FILE_FIND_DUPLICATES"),
        ("ouvre youtube", "BROWSER_OPEN"),
    ]
    failed = []
    for cmd, expected in parse_cases:
        intent = p.parse(cmd).get("intent", "UNKNOWN")
        if intent != expected:
            failed.append(f"parse('{cmd}') → {intent} (attendu {expected})")

    ex = IntentExecutor()
    for intent in [
        "FILE_SEARCH_DATE",
        "FILE_SEARCH_SIZE",
        "FILE_SEARCH_ADVANCED",
        "FILE_ORGANIZE",
        "FILE_BULK_RENAME",
        "FILE_FIND_DUPLICATES",
        "FILE_DELETE_DUPLICATES",
        "FILE_CLEAN",
        "FILE_CLASSIFY",
        "FILE_PREPARE_APPLICATION",
        "FILE_SYNC_DRIVE",
        "BROWSER_OPEN",
        "BROWSER_SEARCH",
    ]:
        if intent not in ex._handlers:
            failed.append(f"Handler manquant: {intent}")

    assert not failed, "\n" + "\n".join(failed)


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  TESTS SEMAINE 13 — Browser Control Niveaux 1-5")
    print("═" * 60 + "\n")

    _test_g1_parsing()
    _test_g2_executor()
    _test_g3_browser_control()
    _test_g4_page_actions()
    _test_g5_non_regression()
    _test_g6_file_browser_combined()

    total = PASS + FAIL
    print("═" * 60)
    print(f"  {PASS} PASSES  |  {FAIL} FAILURES  |  {SKIP} SKIPPED  |  {total} TOTAL")
    print("═" * 60)

    if FAIL == 0:
        print("  ✅ Semaine 13 VALIDÉE — Browser Control niveaux 1-5\n")
    else:
        print(f"  ⚠️  {FAIL} test(s) échoué(s)\n")


if __name__ == "__main__":
    main()