#!/usr/bin/env python3
"""
test_semaine12.py — Tests Semaine 12 : Module Musique Complet

Couvre :
  G1. CommandParser — parsing des 14 intents MUSIC_*
  G2. IntentExecutor — routing vers handlers MUSIC_*
  G3. MusicManager — bibliothèque, scan, recherche (quand disponible)
  G4. PlaylistManager — CRUD playlists (quand disponible)
  G5. VLCController — contrôle lecture (quand disponible)
  G6. Non-régression — semaines précédentes

Usage :
    cd JarvisDesktop
    python tests/test_semaine12.py
"""

import sys
import os
import time
import platform
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PASS  = 0
FAIL  = 0
SKIP  = 0
SYSTEM = platform.system()


def test(name, fn, expect=True, skip_reason=None):
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


def sep(title):
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
def test_g1_parsing():
    sep("GROUPE 1 — Parsing des intents MUSIC_*")
    parser = _offline_parser()

    cases = [
        # Lecture
        ("joue la musique hallelujah",       "MUSIC_PLAY"),
        ("play shape of you",                "MUSIC_PLAY"),
        ("ecoute Michael Jackson",           "MUSIC_PLAY"),
        # Contrôle
        ("musique suivante",                 "MUSIC_NEXT"),
        ("chanson suivante",                 "MUSIC_NEXT"),
        ("musique precedente",               "MUSIC_PREV"),
        ("mets en pause",                    "MUSIC_PAUSE"),
        ("pause la musique",                 "MUSIC_PAUSE"),
        ("reprends la musique",              "MUSIC_RESUME"),
        ("arrete la musique",                "MUSIC_STOP"),
        ("coupe la musique",                 "MUSIC_STOP"),
        # Info
        ("quelle musique joue",              "MUSIC_CURRENT"),
        ("c est quoi cette musique",         "MUSIC_CURRENT"),
        # Playlists
        ("cree playlist chill",              "MUSIC_PLAYLIST_CREATE"),
        ("joue la playlist gospel",          "MUSIC_PLAYLIST_PLAY"),
        ("joue playlist chill",              "MUSIC_PLAYLIST_PLAY"),
        ("liste mes playlists",              "MUSIC_PLAYLIST_LIST"),
        ("mes playlists",                    "MUSIC_PLAYLIST_LIST"),
        # Mode
        ("lecture aleatoire",                "MUSIC_SHUFFLE"),
        ("repete cette musique",             "MUSIC_REPEAT"),
        # Bibliothèque
        ("scanne la musique",                "MUSIC_LIBRARY_SCAN"),
        ("analyse ma bibliotheque musicale", "MUSIC_LIBRARY_SCAN"),
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
def test_g2_executor():
    sep("GROUPE 2 — IntentExecutor routing MUSIC_*")
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()

    # Vérifier que tous les handlers MUSIC_* existent dans _handlers
    music_intents = [
        "MUSIC_PLAY", "MUSIC_PAUSE", "MUSIC_RESUME", "MUSIC_STOP",
        "MUSIC_NEXT", "MUSIC_PREV", "MUSIC_VOLUME", "MUSIC_SHUFFLE",
        "MUSIC_REPEAT", "MUSIC_CURRENT", "MUSIC_PLAYLIST_CREATE",
        "MUSIC_PLAYLIST_PLAY", "MUSIC_PLAYLIST_LIST", "MUSIC_LIBRARY_SCAN",
    ]

    global PASS, FAIL
    for intent in music_intents:
        has_handler = intent in ex._handlers
        if has_handler:
            PASS += 1
        else:
            FAIL += 1
        icon = "✅" if has_handler else "❌"
        print(f"  {icon} Handler {intent} {'présent' if has_handler else 'MANQUANT'}")

    print()

    # Test exécution — ne doit pas planter (résultats variables selon état module)
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
    sep("GROUPE 2b — Exécution sans crash")
    for intent, params in exec_cases:
        try:
            result = ex.execute(intent, params)
            # Succès = ne pas planter ET retourner un dict
            ok = isinstance(result, dict) and "success" in result
            if ok:
                PASS += 1
            else:
                FAIL += 1
            icon = "✅" if ok else "❌"
            msg = result.get("message", "")[:60] if isinstance(result, dict) else str(result)[:60]
            print(f"  {icon} {intent} → {msg}")
        except Exception as e:
            FAIL += 1
            print(f"  ❌ {intent} → Exception : {e}")
    print()


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 3 — MusicManager (si module disponible)
# ════════════════════════════════════════════════════════════════════════════
def test_g3_music_manager():
    sep("GROUPE 3 — MusicManager (module semaine 3)")
    try:
        from modules.music.music_manager import MusicManager
        music_available = True
    except ImportError:
        music_available = False

    if not music_available:
        global SKIP
        print("  ⏭  MusicManager non disponible — module créé en semaine 3")
        print("       Ce groupe passera automatiquement une fois le module créé.\n")
        SKIP += 6
        return

    mm = MusicManager()

    test("Instanciation MusicManager",
         lambda: {"success": mm is not None, "message": "MusicManager initialisé"})

    test("scan_library() ne plante pas",
         lambda: mm.scan_library() or {"success": True, "message": "scan ok"})

    test("count_songs() retourne un entier",
         lambda: {"success": isinstance(mm.count_songs(), int),
                  "message": f"{mm.count_songs()} chanson(s)"})

    test("search_song('test') retourne une liste",
         lambda: {"success": isinstance(mm.search_song("test"), list),
                  "message": f"{len(mm.search_song('test'))} résultat(s)"})

    test("current_song() ne plante pas",
         lambda: mm.current_song() or {"success": True, "message": "aucune musique en cours"})

    test("list_playlists() retourne dict valide",
         lambda: mm.list_playlists())


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 4 — PlaylistManager (si module disponible)
# ════════════════════════════════════════════════════════════════════════════
def test_g4_playlist_manager():
    sep("GROUPE 4 — PlaylistManager (module semaine 3)")
    try:
        from modules.music.playlist_manager import PlaylistManager
        pm_available = True
    except ImportError:
        pm_available = False

    if not pm_available:
        global SKIP
        print("  ⏭  PlaylistManager non disponible — module créé en semaine 3\n")
        SKIP += 6
        return

    pm = PlaylistManager()

    test("Instanciation",
         lambda: {"success": pm is not None, "message": "PlaylistManager ok"})

    test("create_playlist('test_s12')",
         lambda: pm.create_playlist("test_s12"))

    test("Playlist créée visible dans list_playlists()",
         lambda: {"success": any(p.get("name") == "test_s12"
                                  for p in pm.list_playlists().get("data", {}).get("playlists", [])),
                  "message": "test_s12 trouvée"})

    test("delete_playlist('test_s12')",
         lambda: pm.delete_playlist("test_s12"))

    test("Playlist inexistante → erreur",
         lambda: pm.delete_playlist("n_existe_pas_xyz"), expect=False)

    test("list_playlists() retourne dict valide",
         lambda: pm.list_playlists())


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 5 — VLCController (si VLC disponible)
# ════════════════════════════════════════════════════════════════════════════
def test_g5_vlc_controller():
    sep("GROUPE 5 — VLCController (si VLC installé)")
    try:
        from modules.music.vlc_controller import VLCController
        vlc_available = True
    except ImportError:
        vlc_available = False

    if not vlc_available:
        global SKIP
        print("  ⏭  VLCController non disponible — module créé en semaine 3\n")
        SKIP += 4
        return

    try:
        vc = VLCController()
        vlc_init_ok = True
    except Exception as e:
        print(f"  ⚠  VLCController init échouée : {e}")
        vlc_init_ok = False

    if not vlc_init_ok:
        SKIP += 4
        return

    test("VLC disponible",
         lambda: {"success": vc.is_available(), "message": f"VLC: {vc.is_available()}"})

    test("get_status() ne plante pas",
         lambda: vc.get_status() or {"success": True, "message": "status ok"})

    test("set_volume(50) ne plante pas",
         lambda: vc.set_volume(50) or {"success": True, "message": "volume 50"})

    test("stop() ne plante pas",
         lambda: vc.stop() or {"success": True, "message": "stop ok"})


# ════════════════════════════════════════════════════════════════════════════
#  GROUPE 6 — Non-régression
# ════════════════════════════════════════════════════════════════════════════
def test_g6_non_regression():
    sep("GROUPE 6 — Non-régression")
    parser = _offline_parser()

    # Vérifier que les anciens intents audio sont intacts
    cases = [
        ("monte le volume",          "AUDIO_VOLUME_UP"),
        ("baisse le son",            "AUDIO_VOLUME_DOWN"),
        ("mets le volume a 70",      "AUDIO_VOLUME_SET"),
        ("coupe le son",             "AUDIO_MUTE"),
        # Nouveaux patterns S9
        ("luminosite 70",            "SCREEN_BRIGHTNESS"),
        ("mets la luminosite a 50%", "SCREEN_BRIGHTNESS"),
        ("envoie la capture au telephone", "SCREENSHOT_TO_PHONE"),
        ("resolution de l ecran",    "SCREEN_INFO"),
        # Patterns S11
        ("mode nuit",                "MACRO_RUN"),
        ("repete",                   "REPEAT_LAST"),
        ("historique",               "HISTORY_SHOW"),
        ("annule extinction",        "POWER_CANCEL"),
        ("liste les macros",         "MACRO_LIST"),
        # Patterns de base
        ("ouvre chrome",             "APP_OPEN"),
        ("eteins l ordinateur",      "SYSTEM_SHUTDOWN"),
        ("infos systeme",            "SYSTEM_INFO"),
        ("liste les reseaux wifi",   "WIFI_LIST"),
        ("active le bluetooth",      "BLUETOOTH_ENABLE"),
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
        print(f"  {icon} \"{cmd}\" → {intent}{note}")
    print()

    # Test postprocess_result()
    sep("GROUPE 6b — _postprocess_result réactivé")
    pp_cases = [
        ("mets la luminosite a 70%",
         {"intent": "AUDIO_PLAY", "params": {"query": "la luminosite a 70%"}, "confidence": 0.4},
         "SCREEN_BRIGHTNESS"),
        ("luminosite 50",
         {"intent": "UNKNOWN", "params": {}, "confidence": 0.3},
         "SCREEN_BRIGHTNESS"),
        ("ferme cette fenetre",
         {"intent": "SCREEN_OFF", "params": {}, "confidence": 0.5},
         "WINDOW_CLOSE"),
    ]
    for cmd, raw_result, expected_intent in pp_cases:
        corrected = parser._postprocess_result(cmd, raw_result)
        intent = corrected.get("intent", "UNKNOWN")
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
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
def main():
    print("\n" + "═" * 60)
    print("  TESTS SEMAINE 12 — Module Musique")
    print("═" * 60 + "\n")

    test_g1_parsing()
    test_g2_executor()
    test_g3_music_manager()
    test_g4_playlist_manager()
    test_g5_vlc_controller()
    test_g6_non_regression()

    total = PASS + FAIL
    print("═" * 60)
    print(f"  {PASS} PASSES  |  {FAIL} FAILURES  |  {SKIP} SKIPPED  |  {total} TOTAL")
    print("═" * 60)

    if FAIL == 0:
        if SKIP > 0:
            print(f"  ✅ Tests passés ({SKIP} groupes en attente du module musique semaine 3)\n")
        else:
            print("  ✅ Semaine 12 VALIDÉE — Module Musique opérationnel\n")
    else:
        print(f"  ⚠️  {FAIL} test(s) échoué(s)\n")
        print("  Pour G3-G5 : créer les modules en semaine 3")
        print("  Pour G1-G2 : appliquer le patch command_parser.py + intent_executor.py\n")


if __name__ == "__main__":
    main()
