#!/usr/bin/env python3
"""
test_semaine12.py — Tests Semaine 12 : Module Musique Complet

Couvre :
  G1. CommandParser — parsing des 14 intents MUSIC_*
  G2. IntentExecutor — routing vers handlers MUSIC_*
  G3. MusicManager — bibliothèque, scan, recherche
  G4. PlaylistManager — CRUD playlists
  G5. VLCController — contrôle lecture
  G6. Non-régression — semaines précédentes

Usage :
    cd JarvisDesktop
    python tests/test_semaine12.py      ← mode script direct (recommandé)
    pytest tests/test_semaine12.py      ← mode pytest (aussi supporté)
"""

import sys
import os
import platform
import random
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PASS   = 0
FAIL   = 0
SKIP   = 0
SYSTEM = platform.system()


def _run(name, fn, expect=True, skip_reason=None):
    """Exécute un test et affiche le résultat. Renommé _run pour éviter le conflit pytest."""
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
        if passed:
            PASS += 1
        else:
            FAIL += 1
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


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 1 — CommandParser : 14 intents MUSIC_*
# ════════════════════════════════════════════════════════════════════════════

def _test_g1_parsing():
    _sep("GROUPE 1 — Parsing des intents MUSIC_*")
    parser = _offline_parser()

    cases = [
        ("joue la musique hallelujah",        "MUSIC_PLAY"),
        ("play shape of you",                 "MUSIC_PLAY"),
        ("ecoute Michael Jackson",            "MUSIC_PLAY"),
        ("musique suivante",                  "MUSIC_NEXT"),
        ("chanson suivante",                  "MUSIC_NEXT"),
        ("musique precedente",                "MUSIC_PREV"),
        ("mets en pause",                     "MUSIC_PAUSE"),
        ("pause la musique",                  "MUSIC_PAUSE"),
        ("reprends la musique",               "MUSIC_RESUME"),
        ("arrete la musique",                 "MUSIC_STOP"),
        ("coupe la musique",                  "MUSIC_STOP"),
        ("quelle musique joue",               "MUSIC_CURRENT"),
        ("c est quoi cette musique",          "MUSIC_CURRENT"),
        ("cree playlist chill",               "MUSIC_PLAYLIST_CREATE"),
        ("joue la playlist gospel",           "MUSIC_PLAYLIST_PLAY"),
        ("joue playlist chill",               "MUSIC_PLAYLIST_PLAY"),
        ("liste mes playlists",               "MUSIC_PLAYLIST_LIST"),
        ("mes playlists",                     "MUSIC_PLAYLIST_LIST"),
        ("lecture aleatoire",                 "MUSIC_SHUFFLE"),
        ("repete cette musique",              "MUSIC_REPEAT"),
        ("scanne la musique",                 "MUSIC_LIBRARY_SCAN"),
        ("analyse ma bibliotheque musicale",  "MUSIC_LIBRARY_SCAN"),
    ]

    global PASS, FAIL
    for cmd, expected in cases:
        parsed = parser.parse(cmd)
        intent = parsed.get("intent", "UNKNOWN")
        ok = intent == expected
        if ok:
            PASS += 1
        else:
            FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} parse(\"{cmd}\") → {intent}{note}")
    print()


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 2 — IntentExecutor : routing MUSIC_*
# ════════════════════════════════════════════════════════════════════════════

def _test_g2_executor():
    _sep("GROUPE 2 — IntentExecutor routing MUSIC_*")
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()

    music_intents = [
        "MUSIC_PLAY", "MUSIC_PAUSE", "MUSIC_RESUME", "MUSIC_STOP",
        "MUSIC_NEXT", "MUSIC_PREV", "MUSIC_VOLUME", "MUSIC_SHUFFLE",
        "MUSIC_REPEAT", "MUSIC_CURRENT", "MUSIC_PLAYLIST_CREATE",
        "MUSIC_PLAYLIST_PLAY", "MUSIC_PLAYLIST_LIST", "MUSIC_LIBRARY_SCAN",
    ]

    global PASS, FAIL
    for intent in music_intents:
        has = intent in ex._handlers
        if has:
            PASS += 1
        else:
            FAIL += 1
        print(f"  {'✅' if has else '❌'} Handler {intent} {'présent' if has else 'MANQUANT'}")
    print()

    _sep("GROUPE 2b — Exécution sans crash")
    exec_cases = [
        ("MUSIC_PLAY",          {"query": "hallelujah"}),
        ("MUSIC_NEXT",          {}),
        ("MUSIC_PREV",          {}),
        ("MUSIC_PAUSE",         {}),
        ("MUSIC_STOP",          {}),
        ("MUSIC_CURRENT",       {}),
        ("MUSIC_PLAYLIST_LIST", {}),
        ("MUSIC_LIBRARY_SCAN",  {}),
    ]
    for intent, params in exec_cases:
        try:
            result = ex.execute(intent, params)
            ok = isinstance(result, dict) and "success" in result
            if ok:
                PASS += 1
            else:
                FAIL += 1
            msg = result.get("message", "")[:60] if isinstance(result, dict) else str(result)[:60]
            print(f"  {'✅' if ok else '❌'} {intent} → {msg}")
        except Exception as e:
            FAIL += 1
            print(f"  ❌ {intent} → Exception : {e}")
    print()


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 3 — MusicManager
# ════════════════════════════════════════════════════════════════════════════

def _test_g3_music_manager():
    _sep("GROUPE 3 — MusicManager")
    global SKIP
    try:
        from modules.music.music_manager import MusicManager
    except ImportError:
        print("  ⏭  MusicManager non disponible — vérifie modules/music/\n")
        SKIP += 5
        return

    mm = MusicManager()

    _run("Instanciation MusicManager",
         lambda: {"success": mm is not None, "message": "MusicManager initialisé"})

    _run("count_songs() retourne int",
         lambda: {"success": isinstance(mm.count_songs(), int),
                  "message": f"{mm.count_songs()} chanson(s)"})

    _run("search_song('test') retourne liste",
         lambda: {"success": isinstance(mm.search_song("test"), list),
                  "message": f"{len(mm.search_song('test'))} résultat(s)"})

    _run("current_song() ne plante pas",
         lambda: mm.current_song() or {"success": True, "message": "aucune musique"})

    _run("list_playlists() valide",
         lambda: mm.list_playlists())


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 4 — PlaylistManager
# ════════════════════════════════════════════════════════════════════════════

def _test_g4_playlist_manager():
    _sep("GROUPE 4 — PlaylistManager")
    global SKIP
    try:
        from modules.music.playlist_manager import PlaylistManager
    except ImportError:
        print("  ⏭  PlaylistManager non disponible\n")
        SKIP += 5
        return

    pm = PlaylistManager()

    _run("Instanciation",
         lambda: {"success": True, "message": "PlaylistManager ok"})

    _run("create_playlist('test_s12')",
         lambda: pm.create_playlist("test_s12"))

    _run("Playlist visible dans list_playlists()",
         lambda: {"success": any(p.get("name") == "test_s12"
                                  for p in pm.list_playlists().get("data", {}).get("playlists", [])),
                  "message": "test_s12 trouvée"})

    _run("delete_playlist('test_s12')",
         lambda: pm.delete_playlist("test_s12"))

    _run("delete playlist inexistante → erreur",
         lambda: pm.delete_playlist("n_existe_pas_xyz"), expect=False)


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 5 — VLCController
# ════════════════════════════════════════════════════════════════════════════

def _test_g5_vlc():
    _sep("GROUPE 5 — VLCController")
    global SKIP
    try:
        from modules.music.vlc_controller import VLCController
    except ImportError:
        print("  ⏭  VLCController non disponible\n")
        SKIP += 4
        return

    vc = VLCController()

    _run("is_available()",
         lambda: {"success": True, "message": f"VLC disponible : {vc.is_available()}"})

    _run("get_status() ne plante pas",
         lambda: vc.get_status())

    _run("set_volume(50) ne plante pas",
         lambda: vc.set_volume(50) if vc.is_available()
                 else {"success": True, "message": "VLC absent — skip volume"})

    _run("stop() ne plante pas",
         lambda: vc.stop() if vc.is_available()
                 else {"success": True, "message": "VLC absent — skip stop"})


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 6 — Non-régression
# ════════════════════════════════════════════════════════════════════════════

def _test_g6_non_regression():
    _sep("GROUPE 6 — Non-régression")
    parser = _offline_parser()

    cases = [
        # Audio classique intact
        ("monte le volume",           "AUDIO_VOLUME_UP"),
        ("baisse le son",             "AUDIO_VOLUME_DOWN"),
        ("mets le volume a 70",       "AUDIO_VOLUME_SET"),
        ("coupe le son",              "AUDIO_MUTE"),
        # Corrections S9
        ("luminosite 70",             "SCREEN_BRIGHTNESS"),
        ("mets la luminosite a 50",   "SCREEN_BRIGHTNESS"),
        ("envoie la capture au telephone", "SCREENSHOT_TO_PHONE"),
        # Patterns S11
        ("mode nuit",                 "MACRO_RUN"),
        ("repete",                    "REPEAT_LAST"),
        ("historique",                "HISTORY_SHOW"),
        ("annule extinction",         "POWER_CANCEL"),
        ("liste les macros",          "MACRO_LIST"),
        # Base
        ("ouvre chrome",              "APP_OPEN"),
        ("eteins l ordinateur",       "SYSTEM_SHUTDOWN"),
        ("infos systeme",             "SYSTEM_INFO"),
        ("liste les reseaux wifi",    "WIFI_LIST"),
        ("active le bluetooth",       "BLUETOOTH_ENABLE"),
    ]

    global PASS, FAIL
    for cmd, expected in cases:
        intent = parser.parse(cmd).get("intent", "UNKNOWN")
        ok = intent == expected
        if ok:
            PASS += 1
        else:
            FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} \"{cmd}\" → {intent}{note}")
    print()

    _sep("GROUPE 6b — _postprocess_result")
    pp_cases = [
        ("mets la luminosite a 70%",
         {"intent": "AUDIO_PLAY", "params": {}, "confidence": 0.4, "raw": ""},
         "SCREEN_BRIGHTNESS"),
        ("ferme cette fenetre",
         {"intent": "SCREEN_OFF", "params": {}, "confidence": 0.5, "raw": ""},
         "WINDOW_CLOSE"),
    ]
    for cmd, raw_result, expected_intent in pp_cases:
        corrected = parser._postprocess_result(cmd, raw_result)
        intent    = corrected.get("intent", "UNKNOWN")
        ok = intent == expected_intent
        if ok:
            PASS += 1
        else:
            FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected_intent}" if not ok else ""
        print(f"  {icon} postprocess(\"{cmd}\") → {intent}{note}")
    print()


# ════════════════════════════════════════════════════════════════════════════
#  PYTEST WRAPPERS — pour `pytest tests/test_semaine12.py`
# ════════════════════════════════════════════════════════════════════════════

def test_g1_parsing():
    """pytest wrapper — G1 parsing MUSIC_*"""
    from core.command_parser import CommandParser
    p = CommandParser(); p.ai_available = False; p.client = None

    cases = [
        ("joue la musique hallelujah", "MUSIC_PLAY"),
        ("musique suivante",           "MUSIC_NEXT"),
        ("mets en pause",              "MUSIC_PAUSE"),
        ("arrete la musique",          "MUSIC_STOP"),
        ("quelle musique joue",        "MUSIC_CURRENT"),
        ("cree playlist chill",        "MUSIC_PLAYLIST_CREATE"),
        ("joue playlist chill",        "MUSIC_PLAYLIST_PLAY"),
        ("liste mes playlists",        "MUSIC_PLAYLIST_LIST"),
        ("lecture aleatoire",          "MUSIC_SHUFFLE"),
        ("scanne la musique",          "MUSIC_LIBRARY_SCAN"),
    ]
    failed = []
    for cmd, expected in cases:
        intent = p.parse(cmd).get("intent", "UNKNOWN")
        if intent != expected:
            failed.append(f"parse('{cmd}') → {intent} (attendu {expected})")
    assert not failed, "\n" + "\n".join(failed)


def test_g2_handlers_present():
    """pytest wrapper — G2 handlers MUSIC_* dans IntentExecutor"""
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()
    missing = [i for i in [
        "MUSIC_PLAY", "MUSIC_PAUSE", "MUSIC_RESUME", "MUSIC_STOP",
        "MUSIC_NEXT", "MUSIC_PREV", "MUSIC_VOLUME", "MUSIC_SHUFFLE",
        "MUSIC_REPEAT", "MUSIC_CURRENT", "MUSIC_PLAYLIST_CREATE",
        "MUSIC_PLAYLIST_PLAY", "MUSIC_PLAYLIST_LIST", "MUSIC_LIBRARY_SCAN",
    ] if i not in ex._handlers]
    assert not missing, f"Handlers manquants : {missing}"


def test_g3_music_manager():
    """pytest wrapper — G3 MusicManager"""
    from modules.music.music_manager import MusicManager
    mm = MusicManager()
    assert isinstance(mm.count_songs(), int)
    assert isinstance(mm.search_song("test"), list)
    status = mm.current_song()
    assert isinstance(status, dict) and "success" in status


def test_g4_playlist_manager():
    """pytest wrapper — G4 PlaylistManager"""
    from modules.music.playlist_manager import PlaylistManager
    pm = PlaylistManager()
    r = pm.create_playlist("test_pytest_s12")
    assert r["success"]
    names = [p["name"] for p in pm.list_playlists()["data"]["playlists"]]
    assert "test_pytest_s12" in names
    r2 = pm.delete_playlist("test_pytest_s12")
    assert r2["success"]
    r3 = pm.delete_playlist("inexistant_xyz")
    assert not r3["success"]


def test_g5_vlc_controller():
    """pytest wrapper — G5 VLCController"""
    from modules.music.vlc_controller import VLCController
    vc = VLCController()
    status = vc.get_status()
    assert isinstance(status, dict) and "success" in status


def test_g6_non_regression():
    """pytest wrapper — G6 non-régression"""
    from core.command_parser import CommandParser
    p = CommandParser(); p.ai_available = False; p.client = None

    cases = [
        ("luminosite 70",          "SCREEN_BRIGHTNESS"),
        ("mode nuit",              "MACRO_RUN"),
        ("repete",                 "REPEAT_LAST"),
        ("ouvre chrome",           "APP_OPEN"),
        ("eteins l ordinateur",    "SYSTEM_SHUTDOWN"),
        ("liste les reseaux wifi", "WIFI_LIST"),
    ]
    failed = []
    for cmd, expected in cases:
        intent = p.parse(cmd).get("intent", "UNKNOWN")
        if intent != expected:
            failed.append(f"'{cmd}' → {intent} (attendu {expected})")
    assert not failed, "\n" + "\n".join(failed)


# ════════════════════════════════════════════════════════════════════════════
#  MAIN — mode script direct
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  TESTS SEMAINE 12 — Module Musique")
    print("═" * 60 + "\n")

    _test_g1_parsing()
    _test_g2_executor()
    _test_g3_music_manager()
    _test_g4_playlist_manager()
    _test_g5_vlc()
    _test_g6_non_regression()

    total = PASS + FAIL
    print("═" * 60)
    print(f"  {PASS} PASSES  |  {FAIL} FAILURES  |  {SKIP} SKIPPED  |  {total} TOTAL")
    print("═" * 60)

    if FAIL == 0:
        if SKIP > 0:
            print(f"  ✅ Tests passés ({SKIP} skipped — vérifier modules/music/)\n")
        else:
            print("  ✅ Semaine 12 VALIDÉE — Module Musique opérationnel\n")
    else:
        print(f"  ⚠️  {FAIL} test(s) échoué(s)\n")


if __name__ == "__main__":
    main()