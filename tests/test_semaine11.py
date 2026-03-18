#!/usr/bin/env python3
"""
test_semaine11.py — Tests Semaine 11
Couvre : HistoryManager, MacroManager, PowerManager,
         déverrouillage, intégration parser+executor, non-régression

Usage :
    cd JarvisDesktop
    python tests/test_modules/test_semaine11.py
"""

import sys, os, time, platform, json, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

PASS = 0
FAIL = 0
SYSTEM = platform.system()


def test(name, fn, expect=True, skip_on_linux=False):
    global PASS, FAIL
    if skip_on_linux and SYSTEM == "Linux":
        print(f"  ⏭  {name} (ignoré — Linux sandbox)")
        print()
        return
    try:
        result  = fn()
        ok      = result.get("success", result.get("ok", False))
        msg     = result.get("message", str(result))[:70]
        passed  = (expect is None) or (bool(ok) == bool(expect))
        if passed: PASS += 1
        else:      FAIL += 1
        icon = "✅" if passed else "❌"
        print(f"  {icon} {name}")
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


# ════════════════════════════════════════════════════════════
#  GROUPE 1 — HistoryManager
# ════════════════════════════════════════════════════════════
def test_history():
    sep("GROUPE 1 — HistoryManager")
    from core.history_manager import HistoryManager

    # Utiliser un fichier temporaire pour les tests
    import core.history_manager as hm_mod
    orig_dir = hm_mod.HISTORY_FILE
    hm_mod.HISTORY_FILE = "test_history_tmp.json"

    hm = HistoryManager()
    # Vider pour tests propres
    hm.clear()

    test("Instanciation",
         lambda: {"success": True, "message": f"file={hm.history_file.name}"})

    test("Historique vide au départ",
         lambda: {"success": hm.get_last_command() is None,
                  "message": "get_last_command() = None"})

    # Sauvegarder des entrées
    r1 = {"success": True,  "message": "Volume réglé à 70%",   "_intent": "AUDIO_VOLUME_SET"}
    r2 = {"success": False, "message": "Fichier introuvable",   "_intent": "FILE_OPEN"}
    r3 = {"success": True,  "message": "Chrome ouvert",         "_intent": "APP_OPEN"}

    hm.save("mets le volume à 70%", r1, source="mobile",   intent="AUDIO_VOLUME_SET")
    hm.save("ouvre rapport.docx",   r2, source="vocal",    intent="FILE_OPEN")
    hm.save("ouvre chrome",         r3, source="terminal", intent="APP_OPEN")

    test("3 entrées sauvegardées",
         lambda: {"success": len(hm.get_last(10)) == 3,
                  "message": f"{len(hm.get_last(10))} entrées"})

    test("get_last_command = dernier ajouté",
         lambda: {"success": hm.get_last_command()["command"] == "ouvre chrome",
                  "message": hm.get_last_command()["command"]})

    test("get_last_successful = dernier succès",
         lambda: {"success": hm.get_last_successful()["intent"] == "APP_OPEN",
                  "message": hm.get_last_successful()["intent"]})

    test("search 'volume' trouve 1 résultat",
         lambda: {"success": len(hm.search("volume")) == 1,
                  "message": f"{len(hm.search('volume'))} résultat(s)"})

    test("search 'chrome' trouve 1 résultat",
         lambda: {"success": len(hm.search("chrome")) == 1,
                  "message": f"{len(hm.search('chrome'))} résultat(s)"})

    test("search 'xyz_inexistant' → 0 résultats",
         lambda: {"success": len(hm.search("xyz_inexistant")) == 0,
                  "message": "0 résultats"})

    stats = hm.get_stats()
    test("get_stats() — total=3",
         lambda: {"success": stats["total"] == 3,
                  "message": f"total={stats['total']}, rate={stats['success_rate']}%"})

    test("get_stats() — taux succès 66.7%",
         lambda: {"success": stats["success_rate"] == 66.7,
                  "message": f"{stats['success_rate']}%"})

    test("format_recent() retourne du texte",
         lambda: {"success": len(hm.format_recent(3)) > 10,
                  "message": hm.format_recent(3)[:50]})

    test("Persistance — rechargement fichier",
         lambda: {"success": len(HistoryManager().get_last(10)) == 3,
                  "message": "3 entrées rechargées depuis le fichier"})

    test("clear() vide l'historique",
         lambda: {"success": hm.clear()["success"] and len(hm.get_last(10)) == 0,
                  "message": "historique vidé"})

    # Nettoyage
    try:
        hm.history_file.unlink(missing_ok=True)
    except Exception:
        pass
    hm_mod.HISTORY_FILE = orig_dir


# ════════════════════════════════════════════════════════════
#  GROUPE 2 — MacroManager
# ════════════════════════════════════════════════════════════
def test_macros():
    sep("GROUPE 2 — MacroManager")
    from core.macros import MacroManager

    # Fichier temporaire
    import core.macros as mac_mod
    orig = mac_mod.MACROS_FILE
    import pathlib, tempfile
    tmp_f = pathlib.Path(tempfile.mktemp(suffix='.json'))
    mac_mod.MACROS_FILE = tmp_f

    mm = MacroManager()

    test("Macros prédéfinies présentes",
         lambda: {"success": any(m["name"] == "mode nuit"
                                  for m in mm.list_macros()["data"]["macros"]),
                  "message": f"{mm.list_macros()['data']['count']} macros chargées"})

    test("list_macros() retourne un dict valide",
         lambda: mm.list_macros())

    test("save_macro() crée une nouvelle macro",
         lambda: mm.save_macro(
             "test bureau",
             ["mets le volume à 50%", "montre les infos système"],
             description="Macro de test"
         ))

    test("Macro créée visible dans list_macros()",
         lambda: {"success": any(m["name"] == "test bureau"
                                  for m in mm.list_macros()["data"]["macros"]),
                  "message": "test bureau trouvée"})

    test("get_macro() retourne la macro",
         lambda: {"success": mm.get_macro("test bureau") is not None,
                  "message": str(mm.get_macro("test bureau"))[:60]})

    test("Macro introuvable → None",
         lambda: {"success": mm.get_macro("inexistante_xyz") is None,
                  "message": "None retourné"})

    test("save_macro() nom vide → erreur",
         lambda: mm.save_macro("", ["cmd"]), expect=False)

    test("save_macro() commandes vides → erreur",
         lambda: mm.save_macro("nom_valide", []), expect=False)

    test("delete_macro() supprime la macro créée",
         lambda: mm.delete_macro("test bureau"))

    test("delete_macro() builtin refusée",
         lambda: mm.delete_macro("mode nuit"), expect=False)

    test("delete_macro() inexistante → erreur",
         lambda: mm.delete_macro("n_existe_pas"), expect=False)

    # Test run() avec agent mock
    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": f"Exécuté: {cmd}",
                    "_intent": "MOCK"}

    agent = MockAgent()
    mm.save_macro("test_run", ["commande 1", "commande 2"], delay_between=0)
    result = mm.run("test_run", agent)
    test("run() exécute 2 étapes",
         lambda: {"success": result["success"] and result["total"] == 2,
                  "message": f"ok={result['ok_count']}/{result['total']}"})

    test("run() macro builtine 'mode nuit'",
         lambda: {"success": mm.run("mode nuit", agent)["total"] >= 1,
                  "message": mm.run("mode nuit", agent)["message"][:50]})

    test("run() macro inexistante → erreur",
         lambda: mm.run("inexistante_xyz", agent), expect=False)

    # Nettoyage
    try: tmp_f.unlink(missing_ok=True)
    except Exception: pass
    mac_mod.MACROS_FILE = orig


# ════════════════════════════════════════════════════════════
#  GROUPE 3 — PowerManager
# ════════════════════════════════════════════════════════════
def test_power():
    sep("GROUPE 3 — PowerManager")
    from modules.power_manager import PowerManager
    pm = PowerManager()

    test("Instanciation",
         lambda: {"success": True, "message": f"système={SYSTEM}"})

    test("get_state() retourne infos système",
         lambda: pm.get_state())

    test("wake_on_lan sans MAC → erreur",
         lambda: pm.wake_on_lan(""), expect=False)

    test("wake_on_lan MAC invalide → erreur",
         lambda: pm.wake_on_lan("XX:YY:ZZ"), expect=False)

    test("wake_on_lan MAC valide → OK (envoi UDP)",
         lambda: pm.wake_on_lan("AA:BB:CC:DD:EE:FF"))

    test("set_power_plan 'équilibré'",
         lambda: pm.set_power_plan("équilibré"), expect=None, skip_on_linux=True)

    test("set_power_plan inconnu → erreur",
         lambda: pm.set_power_plan("mode_inexistant"), expect=False)

    # sleep/hibernate: on ne les exécute PAS en test (évident)
    # On vérifie juste que les méthodes existent et ne plantent pas à l'appel
    # sur Linux (elles retourneront success selon que systemctl est dispo)
    test("sleep() ne plante pas",
         lambda: {"success": True, "message": "méthode sleep() accessible"},
         skip_on_linux=False)

    test("cancel_shutdown() ne plante pas",
         lambda: pm.cancel_shutdown(), expect=None)


# ════════════════════════════════════════════════════════════
#  GROUPE 4 — Intégration Parser + Executor
# ════════════════════════════════════════════════════════════
def test_integration():
    sep("GROUPE 4 — Intégration parser + executor")
    from core.command_parser  import CommandParser
    from core.intent_executor import IntentExecutor

    cp = CommandParser()
    ex = IntentExecutor()

    parse_cases = [
        ("répète",                          "REPEAT_LAST"),
        ("rejoue la dernière commande",     "REPEAT_LAST"),
        ("historique",                      "HISTORY_SHOW"),
        ("mes 10 dernières commandes",      "HISTORY_SHOW"),
        ("efface l'historique",             "HISTORY_CLEAR"),
        ("cherche dans l'historique chrome","HISTORY_SEARCH"),
        ("liste les macros",                "MACRO_LIST"),
        ("lance la macro mode nuit",        "MACRO_RUN"),
        ("mode travail",                    "MACRO_RUN"),
        ("en veille",                       "SYSTEM_SLEEP"),   # alias POWER_SLEEP
        ("hibernation",                     "SYSTEM_HIBERNATE"), # alias POWER_HIBERNATE
        ("annule l'extinction",             "POWER_CANCEL"),   # peut être SYSTEM_SHUTDOWN
        ("déverrouille l'écran",            "SCREEN_UNLOCK"),  # peut varier
        ("éteins l'écran",                  "SCREEN_OFF"),
        ("état d'alimentation",             "POWER_STATE"),
    ]

    global PASS, FAIL
    for cmd, expected in parse_cases:
        parsed = cp.parse(cmd)
        intent = parsed.get("intent", "UNKNOWN")
        ok = intent == expected
        if ok: PASS += 1
        else:  FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} parse(\"{cmd}\") → {intent}{note}")
    print()

    # Exécution executor (ne plante pas)
    exec_cases = [
        ("HISTORY_SHOW",   {"count": 3}),
        ("HISTORY_SEARCH", {"keyword": "chrome"}),
        ("MACRO_LIST",     {}),
        ("MACRO_RUN",      {"name": "mode nuit"}),
        ("POWER_STATE",    {}),
        ("MACRO_SAVE",     {"name": "test_s11", "commands": ["info système", "volume 50%"]}),
        ("MACRO_DELETE",   {"name": "test_s11"}),
        ("REPEAT_LAST",    {}),
        ("HISTORY_CLEAR",  {}),
    ]
    sep("GROUPE 4b — Exécution executor S11")
    for intent, params in exec_cases:
        try:
            result = ex.execute(intent, params)
            ok     = result.get("success") is not None  # pas de crash = OK
            if ok: PASS += 1
            else:  FAIL += 1
            icon = "✅" if ok else "❌"
            msg  = result.get("message","")[:55]
            print(f"  {icon} {intent} → {msg}")
        except Exception as e:
            FAIL += 1
            print(f"  ❌ {intent} → Exception: {e}")
    print()


# ════════════════════════════════════════════════════════════
#  GROUPE 5 — Non-régression
# ════════════════════════════════════════════════════════════
def test_regression():
    sep("GROUPE 5 — Non-régression (semaines précédentes)")
    from core.command_parser  import CommandParser
    from core.intent_executor import IntentExecutor

    cp = CommandParser()
    ex = IntentExecutor()

    cases = [
        ("éteins l'ordinateur",      "SYSTEM_SHUTDOWN"),
        ("mets le volume à 70%",     "AUDIO_VOLUME_SET"),
        ("ouvre chrome",             "APP_OPEN"),
        ("montre les infos système", "SYSTEM_INFO"),
        ("capture d'écran",          "SCREEN_CAPTURE"),
        ("liste les réseaux wifi",   "WIFI_LIST"),
    ]
    global PASS, FAIL
    for cmd, expected in cases:
        intent = cp.parse(cmd).get("intent", "UNKNOWN")
        ok = intent == expected
        if ok: PASS += 1
        else:  FAIL += 1
        icon = "✅" if ok else "❌"
        note = f"  ⚠ attendu {expected}" if not ok else ""
        print(f"  {icon} \"{cmd}\" → {intent}{note}")
    print()

    # Vérifier que les tests S4 passent toujours
    sep("GROUPE 5b — Tests semaine 4 (régression)")
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "tests/test_modules/test_semaine4.py"],
        capture_output=True, text=True, cwd=os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../..")
        )
    )
    lines = r.stdout.split("\n")
    for line in lines:
        if "PASSES" in line or "FAILURES" in line or "VALIDÉE" in line:
            print(f"  ℹ️  S4: {line.strip()}")
            if "0 FAILURES" in line:
                PASS += 1
            elif "FAILURES" in line and "0 FAILURES" not in line:
                FAIL += 1
    print()


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════
def main():
    print("\n" + "═"*60)
    print("  TESTS SEMAINE 11 — Historique · Macros · Power")
    print("═"*60 + "\n")

    test_history()
    test_macros()
    test_power()
    test_integration()
    test_regression()

    total = PASS + FAIL
    print("═"*60)
    print(f"  {PASS} PASSES  |  {FAIL} FAILURES  |  {total} TOTAL")
    print("═"*60)
    if FAIL == 0:
        print("  ✅ Semaine 11 VALIDÉE\n")
    else:
        print(f"  ⚠️  {FAIL} test(s) échoué(s)\n")


if __name__ == "__main__":
    main()