"""
test_semaine6.py — Tests complets Semaine 6
  - Mock Azure Server         (Mercredi)
  - WebSocketClient           (Mardi)
  - Auth HMAC-SHA256          (Vendredi)
  - Flux complet POST→Poll→Result (Jeudi)

LANCER :
    cd jarvis_windows
    python tests/test_modules/test_semaine6.py
"""

import hashlib
import hmac
import json
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def assert_response(result: dict, expected_success: bool = None):
    assert isinstance(result, dict),           f"Doit être dict : {type(result)}"
    assert "success" in result,                "'success' manquant"
    assert "message" in result,                "'message' manquant"
    assert "data"    in result,                "'data' manquant"
    if expected_success is not None:
        assert result["success"] == expected_success, (
            f"Attendu success={expected_success} : {result['message']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  LUNDI — Azure Function (modèle v1 : un dossier par fonction)
#
#  Structure attendue dans azure_function/ :
#    host.json, requirements.txt, local.settings.json
#    health/    → function.json + __init__.py
#    command/   → function.json + __init__.py
#    poll/      → function.json + __init__.py
#    result/    → function.json + __init__.py
#    result_get/→ function.json + __init__.py
#    shared/    → storage.py
# ══════════════════════════════════════════════════════════════════════════════

def test_azure_function_files_exist():
    """Vérifie les fichiers de base du projet Azure Function v1."""
    base = Path(__file__).resolve().parent.parent / "azure_function"
    assert base.exists(),                            f"Dossier azure_function/ manquant ({base})"
    assert (base / "host.json").exists(),            "host.json manquant"
    assert (base / "requirements.txt").exists(),     "requirements.txt manquant"
    assert (base / "local.settings.json").exists(),  "local.settings.json manquant"

def test_azure_function_host_json():
    """host.json est valide et contient les champs requis."""
    base = Path(__file__).resolve().parent.parent / "azure_function"
    data = json.loads((base / "host.json").read_text())
    assert data.get("version") == "2.0", "version doit être '2.0'"
    assert "functionTimeout" in data,    "functionTimeout manquant"

def test_azure_function_routes_defined():
    """Vérifie que les 5 dossiers de fonctions existent (modèle v1)."""
    base = Path(__file__).resolve().parent.parent / "azure_function"
    # Dans le modèle v1, chaque route = un dossier avec function.json + __init__.py
    expected_folders = ["health", "command", "poll", "result", "result_get"]
    for folder in expected_folders:
        assert (base / folder).exists(), \
            f"Dossier de fonction '{folder}/' manquant dans azure_function/"
        assert (base / folder / "function.json").exists(), \
            f"function.json manquant dans azure_function/{folder}/"
        assert (base / folder / "__init__.py").exists(), \
            f"__init__.py manquant dans azure_function/{folder}/"

def test_azure_function_auth_functions_defined():
    """verify_token est défini dans shared/storage.py."""
    base   = Path(__file__).resolve().parent.parent / "azure_function"
    source = (base / "shared" / "storage.py").read_text()
    assert "verify_token" in source, "verify_token manquant dans shared/storage.py"
    assert "hmac"         in source, "hmac non importé dans shared/storage.py"
    assert "hashlib"      in source, "hashlib non importé dans shared/storage.py"

def test_azure_function_storage_functions():
    """Les fonctions de stockage sont définies dans shared/storage.py."""
    base   = Path(__file__).resolve().parent.parent / "azure_function"
    source = (base / "shared" / "storage.py").read_text()
    for fn in ["store_command", "get_pending_commands", "store_result", "get_result"]:
        assert fn in source, f"Fonction '{fn}' manquante dans shared/storage.py"

def test_azure_function_anti_replay():
    """L'anti-replay (300s) est codé dans command/__init__.py."""
    base   = Path(__file__).resolve().parent.parent / "azure_function"
    source = (base / "command" / "__init__.py").read_text()
    assert "300" in source, "Fenêtre anti-replay 300s manquante dans command/__init__.py"
    assert "age"  in source, "Variable 'age' manquante dans command/__init__.py"


# ══════════════════════════════════════════════════════════════════════════════
#  MARDI — WebSocketClient
# ══════════════════════════════════════════════════════════════════════════════

def test_ws_client_instantiation():
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": f"OK: {cmd}", "data": None}

    client = WebSocketClient(MockAgent())
    assert client is not None
    assert client.connected        == False
    assert client.running          == False
    assert client.commands_received == 0

def test_ws_client_start_no_url():
    """start() retourne False si AZURE_FUNCTION_URL non configuré."""
    from communication.websocket_client import WebSocketClient
    import config.settings as s

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    old_url = s.AZURE_FUNCTION_URL
    s.AZURE_FUNCTION_URL = ""
    try:
        client = WebSocketClient(MockAgent())
        result = client.start()
        assert result == False, "Doit retourner False si URL vide"
    finally:
        s.AZURE_FUNCTION_URL = old_url

def test_ws_client_get_status():
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client = WebSocketClient(MockAgent())
    status = client.get_status()

    assert isinstance(status, dict)
    for key in ["connected", "running", "mode", "reconnect_count",
                "commands_received", "commands_executed"]:
        assert key in status, f"Clé '{key}' manquante dans get_status()"

def test_ws_client_stop_when_not_running():
    """stop() ne crashe pas si le client n'est pas démarré."""
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client = WebSocketClient(MockAgent())
    client.stop()  # Ne doit pas crasher
    assert client.running == False

def test_ws_client_result_queue():
    """La result_queue est initialisée et accessible."""
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client = WebSocketClient(MockAgent())
    assert hasattr(client, "_result_queue")
    assert isinstance(client._result_queue, queue.Queue)

def test_ws_client_execute_and_report():
    """_execute_and_report exécute la commande et met le résultat en queue."""
    from communication.websocket_client import WebSocketClient

    executed = []

    class MockAgent:
        def handle_command(self, cmd):
            executed.append(cmd)
            return {"success": True, "message": f"Fait : {cmd}", "data": {"cmd": cmd}}

    client = WebSocketClient(MockAgent())
    cmd    = {
        "command_id": str(uuid.uuid4()),
        "command":    "liste les processus",
    }
    client._execute_and_report(cmd)

    assert cmd["command"] in executed, "L'agent n'a pas été appelé"
    assert not client._result_queue.empty(), "Résultat non mis en queue"

    result_item = client._result_queue.get_nowait()
    assert result_item["command_id"] == cmd["command_id"]
    assert result_item["result"]["success"] == True
    assert "Fait" in result_item["result"]["message"]

def test_ws_client_execute_handles_agent_error():
    """_execute_and_report gère les erreurs de l'agent sans crasher."""
    from communication.websocket_client import WebSocketClient

    class BrokenAgent:
        def handle_command(self, cmd):
            raise RuntimeError("Agent simulé en erreur")

    client = WebSocketClient(BrokenAgent())
    cmd    = {
        "command_id": str(uuid.uuid4()),
        "command":    "commande qui plante",
    }
    client._execute_and_report(cmd)  # Ne doit pas lever d'exception

    result_item = client._result_queue.get_nowait()
    assert result_item["result"]["success"] == False
    assert "erreur" in result_item["result"]["message"].lower()

def test_ws_client_on_status_change_callback():
    """on_status_change est appelé lors des changements d'état."""
    from communication.websocket_client import WebSocketClient

    status_changes = []

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    def on_status(status, detail):
        status_changes.append(status)

    client = WebSocketClient(MockAgent(), on_status_change=on_status)
    client._notify_status("connected", "Test")
    client._notify_status("stopped", "Test")

    assert "connected" in status_changes
    assert "stopped"   in status_changes


# ══════════════════════════════════════════════════════════════════════════════
#  MERCREDI — Mock Azure Server
# ══════════════════════════════════════════════════════════════════════════════

def _start_mock_server() -> tuple:
    """Démarre le mock server et retourne (server, base_url)."""
    from communication.mock_azure_server import start_mock_server, _COMMAND_QUEUE, _RESULTS
    # Trouver un port libre
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server = start_mock_server("127.0.0.1", port, background=True)
    return server, f"http://127.0.0.1:{port}"

def test_mock_server_starts():
    """Le mock server démarre sans erreur."""
    server, url = _start_mock_server()
    assert server is not None
    server.shutdown()

def test_mock_server_health():
    """GET /api/health retourne {'status': 'healthy'}."""
    import urllib.request
    server, url = _start_mock_server()
    try:
        resp = urllib.request.urlopen(f"{url}/api/health", timeout=5)
        data = json.loads(resp.read())
        assert data["status"] == "healthy"
        assert "version"   in data
        assert "timestamp" in data
    finally:
        server.shutdown()

def test_mock_server_command_requires_auth():
    """POST /api/command sans token → 401."""
    import urllib.request
    server, url = _start_mock_server()
    try:
        body = json.dumps({
            "command": "test", "device_id": "t", "timestamp": int(time.time())
        }).encode()
        req  = urllib.request.Request(
            f"{url}/api/command", data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Doit retourner 401"
        except urllib.request.HTTPError as e:
            assert e.code == 401, f"Attendu 401, reçu {e.code}"
    finally:
        server.shutdown()

def test_mock_server_send_command_with_token():
    """POST /api/command avec token valide → 202 + command_id."""
    import urllib.request
    from config.settings import SECRET_TOKEN
    server, url = _start_mock_server()
    try:
        body = json.dumps({
            "command": "état système", "device_id": "test", "timestamp": int(time.time())
        }).encode()
        req  = urllib.request.Request(
            f"{url}/api/command", data=body,
            headers={
                "Content-Type": "application/json",
                "X-Jarvis-Token": SECRET_TOKEN,
            },
            method="POST"
        )
        resp = urllib.request.urlopen(req, timeout=5)
        assert resp.status == 202
        data = json.loads(resp.read())
        assert data.get("status") == "queued"
        assert "command_id" in data
    finally:
        server.shutdown()

def test_mock_server_poll_after_command():
    """GET /api/poll après envoi de commande → retourne la commande."""
    import urllib.request
    from config.settings import SECRET_TOKEN
    server, url = _start_mock_server()
    try:
        # Envoyer une commande
        body = json.dumps({
            "command": "ouvre chrome", "device_id": "t",
            "timestamp": int(time.time())
        }).encode()
        req = urllib.request.Request(
            f"{url}/api/command", data=body,
            headers={"Content-Type": "application/json", "X-Jarvis-Token": SECRET_TOKEN},
            method="POST"
        )
        resp  = urllib.request.urlopen(req, timeout=5)
        sent  = json.loads(resp.read())
        cmd_id = sent["command_id"]

        # Poller
        poll_req = urllib.request.Request(
            f"{url}/api/poll",
            headers={"X-Jarvis-Token": SECRET_TOKEN}
        )
        poll_resp = urllib.request.urlopen(poll_req, timeout=5)
        poll_data = json.loads(poll_resp.read())

        assert poll_data.get("count") >= 1
        commands = poll_data.get("commands", [])
        assert len(commands) >= 1
        assert any(c["command_id"] == cmd_id for c in commands)
        assert any(c["command"] == "ouvre chrome" for c in commands)
    finally:
        server.shutdown()

def test_mock_server_result_round_trip():
    """POST /api/result puis GET /api/result/{id} → round-trip complet."""
    import urllib.request
    from config.settings import SECRET_TOKEN
    server, url = _start_mock_server()
    try:
        cmd_id = str(uuid.uuid4())

        # 1. Envoyer le résultat
        result_body = json.dumps({
            "command_id":  cmd_id,
            "success":     True,
            "message":     "Chrome ouvert.",
            "executed_at": int(time.time()),
        }).encode()
        post_req = urllib.request.Request(
            f"{url}/api/result", data=result_body,
            headers={"Content-Type": "application/json", "X-Jarvis-Token": SECRET_TOKEN},
            method="POST"
        )
        post_resp = urllib.request.urlopen(post_req, timeout=5)
        post_data = json.loads(post_resp.read())
        assert post_data.get("status") == "stored"

        # 2. Récupérer le résultat
        get_req  = urllib.request.Request(
            f"{url}/api/result/{cmd_id}",
            headers={"X-Jarvis-Token": SECRET_TOKEN}
        )
        get_resp = urllib.request.urlopen(get_req, timeout=5)
        get_data = json.loads(get_resp.read())
        assert get_data.get("status")  == "done"
        assert get_data.get("success") == True
        assert "Chrome ouvert" in get_data.get("message", "")

    finally:
        server.shutdown()

def test_mock_server_unknown_route():
    """Route inconnue → 404."""
    import urllib.request
    server, url = _start_mock_server()
    try:
        req = urllib.request.Request(f"{url}/api/inexistant")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Doit retourner 404"
        except urllib.request.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
#  VENDREDI — Auth HMAC-SHA256
# ══════════════════════════════════════════════════════════════════════════════

def test_auth_hmac_build_headers():
    """_build_auth_headers génère les headers requis."""
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client  = WebSocketClient(MockAgent())
    headers = client._build_auth_headers("POST", "command", {"test": 1})

    assert "X-Jarvis-Token"     in headers
    assert "X-Jarvis-Signature" in headers
    assert "X-Jarvis-Timestamp" in headers
    assert "X-Jarvis-Device"    in headers

def test_auth_hmac_signature_correct():
    """La signature HMAC-SHA256 est calculée correctement."""
    from communication.websocket_client import WebSocketClient
    from config.settings import SECRET_TOKEN

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client    = WebSocketClient(MockAgent())
    body      = {"command": "test"}
    body_bytes = json.dumps(body).encode("utf-8")
    headers   = client._build_auth_headers("POST", "command", body)

    timestamp  = headers["X-Jarvis-Timestamp"]
    signature  = headers["X-Jarvis-Signature"]
    body_hash  = hashlib.sha256(body_bytes).hexdigest()
    message    = f"POST:command:{timestamp}:{body_hash}"
    expected   = hmac.new(
        SECRET_TOKEN.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    assert signature == expected, "Signature HMAC incorrecte"

def test_auth_hmac_timestamp_fresh():
    """Le timestamp dans les headers est récent (< 5 sec)."""
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client    = WebSocketClient(MockAgent())
    headers   = client._build_auth_headers("GET", "poll")
    timestamp = int(headers["X-Jarvis-Timestamp"])
    age       = abs(int(time.time()) - timestamp)
    assert age < 5, f"Timestamp trop vieux : {age}s"

def test_auth_hmac_different_per_request():
    """Deux requêtes consécutives ont des signatures différentes (timestamp unique)."""
    from communication.websocket_client import WebSocketClient

    class MockAgent:
        def handle_command(self, cmd):
            return {"success": True, "message": "OK", "data": None}

    client = WebSocketClient(MockAgent())
    h1     = client._build_auth_headers("GET", "poll")
    time.sleep(1.1)  # Timestamp change chaque seconde
    h2     = client._build_auth_headers("GET", "poll")
    # Timestamp doit être différent
    assert h1["X-Jarvis-Timestamp"] != h2["X-Jarvis-Timestamp"]

def test_auth_mock_server_rejects_bad_token():
    """Le mock server rejette un token incorrect."""
    import urllib.request
    server, url = _start_mock_server()
    try:
        poll_req = urllib.request.Request(
            f"{url}/api/poll",
            headers={"X-Jarvis-Token": "mauvais_token_xyz"}
        )
        try:
            urllib.request.urlopen(poll_req, timeout=5)
            assert False, "Doit retourner 401"
        except urllib.request.HTTPError as e:
            assert e.code == 401
            error_data = json.loads(e.read())
            assert "Unauthorized" in error_data.get("error", "")
    finally:
        server.shutdown()

def test_auth_mock_server_accepts_hmac_signature():
    """Le mock server accepte une vraie signature HMAC."""
    import urllib.request
    from config.settings import SECRET_TOKEN
    server, url = _start_mock_server()
    try:
        timestamp  = str(int(time.time()))
        body_bytes = b""
        body_hash  = hashlib.sha256(body_bytes).hexdigest()
        message    = f"GET:poll:{timestamp}:{body_hash}"
        signature  = hmac.new(
            SECRET_TOKEN.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        req = urllib.request.Request(
            f"{url}/api/poll",
            headers={
                "X-Jarvis-Signature": signature,
                "X-Jarvis-Timestamp": timestamp,
            }
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        assert "commands" in data
    finally:
        server.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
#  FLUX COMPLET — Postman Tests (intégration)
# ══════════════════════════════════════════════════════════════════════════════

def test_full_flow_command_to_result():
    """
    Test du flux complet :
    1. App mobile envoie commande → Azure Function
    2. PC poll → récupère commande
    3. PC exécute → envoie résultat
    4. App mobile récupère résultat
    """
    import urllib.request
    from config.settings import SECRET_TOKEN

    server, url = _start_mock_server()
    try:
        def http_post(path, body_dict, token=SECRET_TOKEN):
            body = json.dumps(body_dict).encode()
            req  = urllib.request.Request(
                f"{url}/api{path}", data=body,
                headers={"Content-Type": "application/json", "X-Jarvis-Token": token},
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read())

        def http_get(path, token=SECRET_TOKEN):
            req  = urllib.request.Request(
                f"{url}/api{path}",
                headers={"X-Jarvis-Token": token}
            )
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read())

        # ÉTAPE 1 : L'app envoie une commande
        send_result = http_post("/command", {
            "command":   "état du système",
            "device_id": "telephone-test",
            "timestamp": int(time.time()),
        })
        assert send_result.get("status") == "queued"
        command_id = send_result["command_id"]

        # ÉTAPE 2 : Le PC poll et récupère la commande
        poll_result = http_get("/poll")
        assert poll_result.get("count") >= 1
        commands = poll_result["commands"]
        found = next((c for c in commands if c["command_id"] == command_id), None)
        assert found is not None,            "Commande non trouvée dans le poll"
        assert found["command"] == "état du système"

        # ÉTAPE 3 : Le PC exécute et envoie le résultat
        exec_result = http_post("/result", {
            "command_id":  command_id,
            "success":     True,
            "message":     "CPU: 8% | RAM: 3.5 GB / 16 GB",
            "data":        {"cpu": 8, "ram_gb": 3.5},
            "executed_at": int(time.time()),
        })
        assert exec_result.get("status") == "stored"

        # ÉTAPE 4 : L'app récupère le résultat
        final_result = http_get(f"/result/{command_id}")
        assert final_result.get("status")  == "done"
        assert final_result.get("success") == True
        assert "CPU" in final_result.get("message", "")

    finally:
        server.shutdown()

def test_full_flow_with_websocket_client():
    """
    Test flux complet avec WebSocketClient en mode polling.
    Le client exécute vraiment les commandes via l'Agent.
    """
    import urllib.request
    from communication.websocket_client import WebSocketClient
    from config.settings import SECRET_TOKEN
    import config.settings as s

    executed_commands = []
    results_sent      = []

    class MockAgent:
        def handle_command(self, cmd):
            executed_commands.append(cmd)
            return {
                "success": True,
                "message": f"Exécuté : {cmd}",
                "data":    {"cmd": cmd}
            }

    server, url = _start_mock_server()

    # Configurer l'URL pour le WebSocketClient
    old_url = s.AZURE_FUNCTION_URL
    s.AZURE_FUNCTION_URL = url

    try:
        # Envoyer une commande dans le mock
        body = json.dumps({
            "command": "liste les processus", "device_id": "t",
            "timestamp": int(time.time())
        }).encode()
        req = urllib.request.Request(
            f"{url}/api/command", data=body,
            headers={"Content-Type": "application/json", "X-Jarvis-Token": SECRET_TOKEN},
            method="POST"
        )
        resp    = urllib.request.urlopen(req, timeout=5)
        sent    = json.loads(resp.read())
        cmd_id  = sent["command_id"]

        # Simuler _fetch et _execute directement (sans asyncio)
        agent  = MockAgent()
        client = WebSocketClient(agent)

        # Récupérer les commandes via HTTP
        poll_req = urllib.request.Request(
            f"{url}/api/poll",
            headers={"X-Jarvis-Token": SECRET_TOKEN}
        )
        poll_resp = urllib.request.urlopen(poll_req, timeout=5)
        poll_data = json.loads(poll_resp.read())

        cmds = poll_data.get("commands", [])
        assert len(cmds) >= 1

        target_cmd = next((c for c in cmds if c["command_id"] == cmd_id), None)
        assert target_cmd is not None

        # Exécuter et collecter le résultat
        client._execute_and_report(target_cmd)

        # Récupérer le résultat de la queue et l'envoyer
        result_item = client._result_queue.get(timeout=3)
        assert result_item["command_id"] == cmd_id
        assert result_item["result"]["success"] == True

        # Confirmer que l'agent a bien été appelé
        assert "liste les processus" in executed_commands

    finally:
        s.AZURE_FUNCTION_URL = old_url
        server.shutdown()

def test_postman_test_runner():
    """Lance le script postman_test.py contre le mock server."""
    from communication.postman_test import run_postman_tests
    server, url = _start_mock_server()
    try:
        results = run_postman_tests(url, verbose=False)
        # Au moins 70% des tests doivent passer
        assert results["pct"] >= 70, (
            f"Seulement {results['pct']}% de succès. "
            f"Échecs : {results['failed_list']}"
        )
    finally:
        server.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
#  RUNNER MANUEL
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_funcs = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]

    passed, failed = [], []
    for name, fn in test_funcs:
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
        print("  Tous les tests passent. Semaine 6 VALIDÉE. 🚀")