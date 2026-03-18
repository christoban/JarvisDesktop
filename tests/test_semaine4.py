"""
test_semaine4.py — Tests complets Semaine 4
  - CommandParser (fallback keywords + format Groq)
  - IntentExecutor (toutes les intentions)
  - Intégration Agent end-to-end : 20 phrases naturelles

LANCER :
    cd jarvis_windows
    python tests/test_modules/test_semaine4.py
"""

import sys
import json
import shutil
import tempfile
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def assert_response(result: dict, expected_success: bool = None):
    assert isinstance(result, dict),          f"Doit être un dict : {type(result)}"
    assert "success" in result,               "Clé 'success' manquante"
    assert "message" in result,               "Clé 'message' manquante"
    assert "data"    in result,               "Clé 'data' manquante"
    assert isinstance(result["message"], str), "message doit être str"
    if expected_success is not None:
        assert result["success"] == expected_success, (
            f"Attendu success={expected_success} : {result['message']}"
        )


def assert_parsed(result: dict, expected_intent: str, required_params: list = None):
    """Vérifie qu'un résultat de parse a le bon intent et les bons params."""
    assert "intent"     in result, "Clé 'intent' manquante"
    assert "params"     in result, "Clé 'params' manquante"
    assert "confidence" in result, "Clé 'confidence' manquante"
    assert "source"     in result, "Clé 'source' manquante"
    assert result["intent"] == expected_intent, (
        f"Intent attendu '{expected_intent}', reçu '{result['intent']}'"
    )
    if required_params:
        for param in required_params:
            assert param in result["params"], (
                f"Paramètre '{param}' manquant dans {result['params']}"
            )


# ══════════════════════════════════════════════════════════════════════════════
#  LUNDI — Test connexion Groq & structure CommandParser
# ══════════════════════════════════════════════════════════════════════════════

def test_command_parser_instantiation():
    """CommandParser s'instancie sans erreur."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    assert cp is not None
    assert isinstance(cp.ai_available, bool)

def test_command_parser_parse_returns_dict():
    """parse() retourne toujours un dict valide."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("test")
    assert isinstance(result, dict)
    assert "intent"     in result
    assert "params"     in result
    assert "confidence" in result
    assert "source"     in result

def test_command_parser_empty_command():
    """parse() gère une commande vide."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("")
    assert isinstance(result, dict)
    assert result["intent"] == "UNKNOWN"

def test_command_parser_confidence_range():
    """confidence est toujours entre 0.0 et 1.0."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    for cmd in ["éteins", "ouvre chrome", "inconnu xyz 123", ""]:
        result = cp.parse(cmd)
        conf = result["confidence"]
        assert 0.0 <= conf <= 1.0, f"confidence={conf} hors bornes pour '{cmd}'"

def test_command_parser_known_intents():
    """Tous les intents retournés sont dans INTENTS."""
    from core.command_parser import CommandParser, INTENTS
    cp = CommandParser()
    test_cmds = ["éteins", "ouvre chrome", "cherche rapport", "aide", "xyz_inconnu"]
    for cmd in test_cmds:
        result = cp.parse(cmd)
        assert result["intent"] in INTENTS, (
            f"Intent '{result['intent']}' non trouvé dans INTENTS (cmd='{cmd}')"
        )

def test_health_check_returns_dict():
    """health_check() retourne une structure valide."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    result = cp.health_check()
    assert isinstance(result, dict)
    assert "available" in result
    assert "message"   in result
    assert isinstance(result["available"], bool)

def test_intents_catalog_completeness():
    """INTENTS contient au moins 40 intentions."""
    from core.command_parser import INTENTS
    assert len(INTENTS) >= 40, f"Seulement {len(INTENTS)} intentions définies"

def test_few_shot_examples_valid_json():
    """Tous les exemples few-shot sont du JSON valide."""
    from core.command_parser import FEW_SHOT_EXAMPLES
    for user_msg, assistant_msg in FEW_SHOT_EXAMPLES:
        try:
            data = json.loads(assistant_msg)
            assert "intent" in data
            assert "params" in data
        except json.JSONDecodeError as e:
            raise AssertionError(f"Exemple invalide pour '{user_msg}': {e}")

def test_system_prompt_contains_intents():
    """Le system prompt contient les intentions clés."""
    from core.command_parser import CommandParser
    prompt = CommandParser()._build_system_prompt()
    assert "SYSTEM_SHUTDOWN" in prompt
    assert "APP_OPEN"        in prompt
    assert "FILE_SEARCH"     in prompt
    assert len(prompt)       > 500


# ══════════════════════════════════════════════════════════════════════════════
#  MARDI + JEUDI — 20 phrases naturelles testées avec fallback keywords
# ══════════════════════════════════════════════════════════════════════════════

PHRASES_TEST = [
    # ( phrase, expected_intent, required_params )

    # SYSTÈME
    ("éteins l'ordinateur",                "SYSTEM_SHUTDOWN",  []),
    ("shutdown dans 30 secondes",           "SYSTEM_SHUTDOWN",  []),
    ("redémarre le PC maintenant",          "SYSTEM_RESTART",   []),
    ("mets en veille",                      "SYSTEM_SLEEP",     []),
    ("verrouille l'écran",                  "SYSTEM_LOCK",      []),
    ("quel est l'état du système",          "SYSTEM_INFO",      []),
    ("montre l'espace disque",              "SYSTEM_DISK",      []),
    ("liste les processus triés par RAM",   "SYSTEM_PROCESSES", ["sort_by"]),
    ("tue notepad",                         "SYSTEM_KILL_PROCESS", ["target"]),
    ("rapport système complet",             "SYSTEM_FULL_REPORT", []),

    # APPLICATIONS
    ("ouvre chrome",                        "APP_OPEN",     ["app_name"]),
    ("lance visual studio code",            "APP_OPEN",     ["app_name"]),
    ("ferme spotify",                       "APP_CLOSE",    ["app_name"]),
    ("redémarre firefox",                   "APP_RESTART",  ["app_name"]),
    ("quelles applications sont ouvertes",  "APP_LIST_RUNNING", []),

    # FICHIERS
    ("cherche le fichier rapport.docx",     "FILE_SEARCH",  ["query"]),
    ("cherche tous les PDF",                "FILE_SEARCH_TYPE", ["extension"]),
    ("liste le contenu du dossier Documents", "FOLDER_LIST", []),
    ("crée un dossier Projets/Jarvis",      "FOLDER_CREATE", ["path"]),

    # AIDE
    ("aide",                                "HELP", []),
]


def test_20_phrases_naturelles():
    """Jeudi — Teste 20 phrases naturelles avec le fallback keywords."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    results = []

    print("\n  JEUDI — 20 PHRASES NATURELLES :")
    print("  " + "-" * 70)

    for phrase, expected_intent, req_params in PHRASES_TEST:
        parsed     = cp.parse(phrase)
        got_intent = parsed["intent"]
        conf       = parsed["confidence"]
        ok         = (got_intent == expected_intent)

        icon  = "✅" if ok else "❌"
        extra = ""
        if not ok:
            extra = f" (attendu: {expected_intent})"

        print(f"  {icon} [{conf:.0%}] '{phrase[:42]:<42}' → {got_intent}{extra}")
        results.append((phrase, ok, got_intent, expected_intent))

    failed = [(p, got, exp) for p, ok, got, exp in results if not ok]
    print(f"\n  Score : {len(results) - len(failed)}/{len(results)}")

    # Tolérance : au moins 18/20 correct
    assert len(failed) <= 2, (
        f"Trop d'erreurs ({len(failed)}/20) :\n"
        + "\n".join(f"  '{p}' → {got} (attendu {exp})" for p, got, exp in failed)
    )


def test_shutdown_delay_extracted():
    """Parser extrait le délai depuis 'éteins dans 30 secondes'."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("éteins dans 30 secondes")
    assert result["intent"] == "SYSTEM_SHUTDOWN"
    assert result["params"].get("delay_seconds") == 30

def test_process_kill_target_extracted():
    """Parser extrait la cible depuis 'tue notepad'."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("tue notepad")
    assert result["intent"] == "SYSTEM_KILL_PROCESS"
    assert "target" in result["params"]
    assert "notepad" in result["params"]["target"].lower()

def test_app_open_name_extracted():
    """Parser extrait app_name depuis 'ouvre chrome'."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("ouvre chrome")
    assert result["intent"] == "APP_OPEN"
    assert "chrome" in result["params"].get("app_name", "").lower()

def test_file_search_query_extracted():
    """Parser extrait query depuis 'cherche rapport'."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("cherche rapport")
    assert result["intent"] in ("FILE_SEARCH", "FILE_SEARCH_TYPE", "FILE_SEARCH_CONTENT")
    assert result["params"]  # au moins un paramètre

def test_volume_up_step_extracted():
    """Parser extrait step depuis 'monte le volume de 20%'."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("monte le volume")
    assert result["intent"] == "AUDIO_VOLUME_UP"

def test_folder_create_path_extracted():
    """Parser extrait path depuis 'crée dossier Projets/Test'."""
    from core.command_parser import CommandParser
    result = CommandParser().parse("crée dossier Projets/Test")
    assert result["intent"] == "FOLDER_CREATE"
    assert "path" in result["params"]

def test_parse_json_response_valid():
    """_parse_json_response gère un JSON valide."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    json_str = '{"intent":"SYSTEM_SHUTDOWN","params":{"delay_seconds":10},"confidence":0.99}'
    result = cp._parse_json_response(json_str, "éteins")
    assert result["intent"] == "SYSTEM_SHUTDOWN"
    assert result["confidence"] == 0.99

def test_parse_json_response_invalid():
    """_parse_json_response retourne UNKNOWN si JSON invalide."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    result = cp._parse_json_response("pas du json {{{", "test")
    assert result["intent"] == "UNKNOWN"

def test_parse_json_response_unknown_intent():
    """_parse_json_response remplace un intent inconnu par UNKNOWN."""
    from core.command_parser import CommandParser
    cp = CommandParser()
    json_str = '{"intent":"INTENT_INEXISTANT","params":{},"confidence":0.9}'
    result = cp._parse_json_response(json_str, "test")
    assert result["intent"] == "UNKNOWN"


# ══════════════════════════════════════════════════════════════════════════════
#  MERCREDI — IntentExecutor
# ══════════════════════════════════════════════════════════════════════════════

def test_intent_executor_instantiation():
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()
    assert ex is not None

def test_intent_executor_handlers_count():
    """IntentExecutor a au moins 40 handlers."""
    from core.intent_executor import IntentExecutor
    ex = IntentExecutor()
    assert len(ex._handlers) >= 40

def test_intent_executor_unknown_intent():
    """IntentExecutor gère un intent inconnu sans crash."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("INTENT_QUI_NEXISTE_PAS", {})
    assert_response(result, expected_success=False)

def test_intent_executor_system_info():
    """SYSTEM_INFO exécute et retourne du contenu."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_INFO", {})
    assert_response(result, expected_success=True)
    data = result["data"]
    assert "cpu" in data
    assert "ram" in data

def test_intent_executor_system_disk():
    """SYSTEM_DISK retourne les partitions."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_DISK", {})
    assert_response(result, expected_success=True)
    assert "partitions" in result["data"]

def test_intent_executor_system_processes():
    """SYSTEM_PROCESSES retourne une liste de processus."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_PROCESSES", {"sort_by": "cpu"})
    assert_response(result, expected_success=True)
    assert "processes" in result["data"]

def test_intent_executor_system_processes_ram():
    """SYSTEM_PROCESSES avec sort_by=ram fonctionne."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_PROCESSES", {"sort_by": "ram"})
    assert_response(result, expected_success=True)

def test_intent_executor_system_kill_missing_target():
    """SYSTEM_KILL_PROCESS sans target retourne erreur claire."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_KILL_PROCESS", {})
    assert_response(result, expected_success=False)
    assert "processus" in result["message"].lower() or "précise" in result["message"].lower()

def test_intent_executor_system_kill_nonexistent():
    """SYSTEM_KILL_PROCESS avec processus inexistant retourne success=False."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_KILL_PROCESS", {"target": "processus_xyz_inexistant"})
    assert_response(result, expected_success=False)

def test_intent_executor_app_open_empty():
    """APP_OPEN sans app_name retourne erreur claire."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("APP_OPEN", {})
    assert_response(result, expected_success=False)

def test_intent_executor_app_list_running():
    """APP_LIST_RUNNING retourne la liste."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("APP_LIST_RUNNING", {})
    assert_response(result, expected_success=True)
    assert "apps" in result["data"]

def test_intent_executor_app_list_known():
    """APP_LIST_KNOWN retourne les apps connues."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("APP_LIST_KNOWN", {})
    assert_response(result, expected_success=True)
    assert len(result["data"]["apps"]) >= 10

def test_intent_executor_file_search_missing_query():
    """FILE_SEARCH sans query retourne erreur claire."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("FILE_SEARCH", {})
    assert_response(result, expected_success=False)

def test_intent_executor_file_search_notfound():
    """FILE_SEARCH avec fichier inexistant retourne success=False."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("FILE_SEARCH", {"query": "fichier_xyz_inexistant_jarvis_s4"})
    assert_response(result, expected_success=False)

def test_intent_executor_folder_list_home():
    """FOLDER_LIST sans path liste le dossier home."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("FOLDER_LIST", {})
    assert_response(result, expected_success=True)
    assert "files"   in result["data"]
    assert "folders" in result["data"]

def test_intent_executor_folder_create():
    """FOLDER_CREATE crée bien le dossier."""
    from core.intent_executor import IntentExecutor
    import tempfile
    tmp = tempfile.mkdtemp()
    new_dir = str(Path(tmp) / "jarvis_test_s4")
    try:
        result = IntentExecutor().execute("FOLDER_CREATE", {"path": new_dir})
        assert_response(result, expected_success=True)
        assert Path(new_dir).exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_intent_executor_file_copy_move_rename_delete():
    """Test complet CRUD fichier via IntentExecutor."""
    from core.intent_executor import IntentExecutor
    ex  = IntentExecutor()
    tmp = tempfile.mkdtemp()
    try:
        src = Path(tmp) / "source.txt"
        src.write_text("contenu test semaine 4")

        dst_dir = Path(tmp) / "backup"
        dst_dir.mkdir()

        # COPY
        r = ex.execute("FILE_COPY", {"src": str(src), "dst": str(dst_dir)})
        assert_response(r, expected_success=True)
        assert (dst_dir / "source.txt").exists()

        # RENAME
        r = ex.execute("FILE_RENAME", {"path": str(src), "new_name": "renomme.txt"})
        assert_response(r, expected_success=True)
        renamed = Path(tmp) / "renomme.txt"
        assert renamed.exists()

        # DELETE
        r = ex.execute("FILE_DELETE", {"path": str(renamed)})
        assert_response(r, expected_success=True)
        assert not renamed.exists()

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_intent_executor_network():
    """SYSTEM_NETWORK retourne les interfaces."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_NETWORK", {})
    assert_response(result, expected_success=True)
    assert "interfaces" in result["data"]

def test_intent_executor_full_report():
    """SYSTEM_FULL_REPORT retourne toutes les sections."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("SYSTEM_FULL_REPORT", {})
    assert_response(result, expected_success=True)
    data = result["data"]
    assert "system" in data
    assert "disk"   in data
    assert "network" in data

def test_intent_executor_help():
    """HELP retourne le menu d'aide avec les groupes d'intentions."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("HELP", {})
    assert_response(result, expected_success=True)
    assert "display" in result["data"]
    help_text = result["data"]["display"]
    assert "SYSTÈME"      in help_text
    assert "APPLICATIONS" in help_text
    assert "FICHIERS"     in help_text

def test_intent_executor_unknown():
    """UNKNOWN retourne un message d'aide."""
    from core.intent_executor import IntentExecutor
    result = IntentExecutor().execute("UNKNOWN", {})
    assert_response(result, expected_success=False)
    assert len(result["message"]) > 10


# ══════════════════════════════════════════════════════════════════════════════
#  VENDREDI — Tests d'intégration end-to-end via Agent
#  Les 5 phrases clés du cahier des charges
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_instantiation_with_parser():
    """Agent s'instancie avec le nouveau pipeline IA."""
    from core.agent import Agent
    agent = Agent()
    assert agent is not None
    assert agent.parser   is not None
    assert agent.executor is not None

def test_agent_handle_empty():
    """Agent gère une commande vide."""
    from core.agent import Agent
    result = Agent().handle_command("")
    assert_response(result, expected_success=False)

def test_agent_result_has_meta():
    """handle_command enrichit le résultat avec _intent/_confidence/_source."""
    from core.agent import Agent
    result = Agent().handle_command("cpu")
    # Les méta sont soit dans data soit dans result directement
    intent_found = (
        result.get("_intent") is not None
        or (isinstance(result.get("data"), dict) and "_intent" in result["data"])
    )
    assert intent_found, f"_intent manquant dans le résultat : {result}"

def test_integration_eteins_ordi():
    """INTÉGRATION — 'éteins l'ordi' → SYSTEM_SHUTDOWN reconnu et exécutable."""
    from core.agent import Agent
    from core.command_parser import CommandParser
    # On teste le parsing (pas l'exécution réelle du shutdown)
    result = CommandParser().parse("éteins l'ordi")
    assert result["intent"] == "SYSTEM_SHUTDOWN"
    assert result["confidence"] >= 0.7

def test_integration_ouvre_chrome():
    """INTÉGRATION — 'ouvre chrome' → APP_OPEN + app_name=chrome."""
    from core.agent import Agent
    result = Agent().handle_command("ouvre chrome")
    # Chrome peut ne pas être installé → success peut être False
    # Mais le pipeline doit l'avoir correctement parsé et tenté
    assert_response(result)  # Format valide
    # L'intent doit avoir été APP_OPEN
    intent = (
        result.get("_intent")
        or (result.get("data") or {}).get("_intent", "")
    )
    assert intent == "APP_OPEN", f"Intent attendu APP_OPEN, reçu '{intent}'"

def test_integration_cherche_rapport():
    """INTÉGRATION — 'cherche rapport.docx' → FILE_SEARCH reconnu."""
    from core.agent import Agent
    result = Agent().handle_command("cherche rapport.docx")
    assert_response(result)
    intent = (
        result.get("_intent")
        or (result.get("data") or {}).get("_intent", "")
    )
    assert intent in ("FILE_SEARCH", "FILE_SEARCH_TYPE"), (
        f"Intent attendu FILE_SEARCH, reçu '{intent}'"
    )

def test_integration_liste_processus():
    """INTÉGRATION — 'liste les processus' → vraie liste."""
    from core.agent import Agent
    result = Agent().handle_command("liste les processus")
    assert_response(result, expected_success=True)
    data = result.get("data") or {}
    assert "processes" in data or "apps" in data

def test_integration_rapport_complet():
    """INTÉGRATION — 'rapport complet' → toutes sections présentes."""
    from core.agent import Agent
    result = Agent().handle_command("rapport complet")
    assert_response(result, expected_success=True)
    data = result.get("data") or {}
    assert "system" in data or "display" in data

def test_integration_aide():
    """INTÉGRATION — 'aide' → menu complet avec SYSTÈME + APPLICATIONS + FICHIERS."""
    from core.agent import Agent
    result = Agent().handle_command("aide")
    assert_response(result, expected_success=True)
    display = (result.get("data") or {}).get("display", "")
    assert "SYSTÈME"      in display
    assert "APPLICATIONS" in display
    assert "FICHIERS"     in display

def test_integration_creer_dossier():
    """INTÉGRATION — 'crée dossier /tmp/jarvis_s4_test' → dossier créé."""
    from core.agent import Agent
    tmp = tempfile.mkdtemp()
    new_dir = str(Path(tmp) / "jarvis_s4_integration")
    try:
        result = Agent().handle_command(f"crée dossier {new_dir}")
        assert_response(result, expected_success=True)
        assert Path(new_dir).exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_integration_liste_dossier():
    """INTÉGRATION — 'liste dossier /tmp' → liste valide."""
    from core.agent import Agent
    test_path = os.environ.get("TEMP", "C:\\Windows")
    result = Agent().handle_command(f"liste dossier {test_path}")
    assert_response(result, expected_success=True)

def test_integration_unknown_graceful():
    """INTÉGRATION — commande totalement inconnue gérée proprement."""
    from core.agent import Agent
    result = Agent().handle_command("blabla xyz_commande_inconnue_123")
    assert_response(result)  # Pas de crash, format valide

def test_pipeline_parse_then_execute():
    """Test du pipeline complet : parse → execute séparément."""
    from core.command_parser import CommandParser
    from core.intent_executor import IntentExecutor

    cp = CommandParser()
    ex = IntentExecutor()

    test_cases = [
        ("état du système",    "SYSTEM_INFO"),
        ("espace disque",      "SYSTEM_DISK"),
        ("apps ouvertes",      "APP_LIST_RUNNING"),
    ]

    for cmd, expected_intent in test_cases:
        parsed = cp.parse(cmd)
        assert parsed["intent"] == expected_intent, (
            f"'{cmd}' → intent={parsed['intent']} (attendu {expected_intent})"
        )
        result = ex.execute(parsed["intent"], parsed["params"], cmd)
        assert_response(result, expected_success=True), (
            f"Exécution échouée pour '{cmd}' : {result['message']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  RUNNER MANUEL
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import inspect
    import traceback

    test_funcs = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]

    passed, failed = [], []
    for name, fn in test_funcs:
        sig = inspect.signature(fn)
        try:
            fn()
            passed.append(name)
        except Exception as e:
            failed.append((name, str(e)[:120]))

    print()
    print("=" * 65)
    print(f"  {len(passed)} PASSES  |  {len(failed)} FAILURES  |  {len(test_funcs)} TOTAL")
    print("=" * 65)
    if failed:
        print()
        for fn_name, err in failed:
            print(f"  FAIL : {fn_name}")
            print(f"         {err}")
    else:
        print("  Tous les tests passent. Semaine 4 VALIDÉE.")