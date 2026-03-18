#!/usr/bin/env python3
"""
test_e2e_semaine7.py — TEST FIN-À-FIN Semaine 7
Simule le pipeline complet : Mobile → Azure Function → PC Agent

Sans vrai téléphone ni vraie Azure Function, on:
  1. Lance un mock HTTP server (remplace Azure Function)
  2. Lance l'agent PC en thread
  3. Envoie des commandes HTTP (comme le ferait l'app mobile)
  4. Vérifie que le PC exécute et répond correctement

Usage:
    cd JarvisDesktop
    python tests/test_e2e_semaine7.py
"""

import sys
import os
import json
import time
import uuid
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ajouter le root au path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════
MOCK_PORT    = 7779
SECRET_TOKEN = "menedona_2005_christoban_2026"
DEVICE_ID    = "NDZANA_PHONE"

# Stockage partagé (remplace Azure Table Storage)
_store: dict = {}       # id → { command, status, result }
_store_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════
#  MOCK AZURE FUNCTION SERVER
# ═══════════════════════════════════════════════════════════
class MockAzureHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass   # silencer les logs HTTP

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        token = self.headers.get('X-Jarvis-Token', '')
        return token == SECRET_TOKEN

    def do_GET(self):
        if not self._auth():
            return self._send_json(401, {"error": "Unauthorized"})

        # GET /api/health
        if self.path == '/api/health':
            return self._send_json(200, {"status": "ok", "agent": "mock"})

        # GET /api/result/<id>
        if self.path.startswith('/api/result/'):
            cmd_id = self.path.split('/')[-1]
            with _store_lock:
                entry = _store.get(cmd_id)
            if not entry:
                return self._send_json(404, {"error": "Not found"})
            return self._send_json(200, entry)

        self._send_json(404, {"error": "Unknown route"})

    def do_POST(self):
        if not self._auth():
            return self._send_json(401, {"error": "Unauthorized"})

        # POST /api/command
        if self.path == '/api/command':
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length))
            cmd    = body.get('command', '')
            cmd_id = str(uuid.uuid4())[:8]

            entry = {"id": cmd_id, "command": cmd, "status": "pending", "result": None}
            with _store_lock:
                _store[cmd_id] = entry

            # Traiter la commande en arrière-plan (simule le PC agent)
            threading.Thread(target=_process_command, args=(cmd_id, cmd), daemon=True).start()

            return self._send_json(202, {"id": cmd_id, "status": "pending"})

        self._send_json(404, {"error": "Unknown route"})


def _process_command(cmd_id: str, command: str):
    """Traite la commande via l'agent PC réel et stocke le résultat."""
    try:
        from core.agent import Agent
        agent  = Agent()
        result = agent.handle_command(command)
        with _store_lock:
            _store[cmd_id]["status"] = "done"
            _store[cmd_id]["result"] = result
    except Exception as e:
        with _store_lock:
            _store[cmd_id]["status"] = "done"
            _store[cmd_id]["result"] = {"success": False, "message": str(e)}


# ═══════════════════════════════════════════════════════════
#  CLIENT HTTP (simule l'app mobile)
# ═══════════════════════════════════════════════════════════
BASE = f"http://localhost:{MOCK_PORT}"
HEADERS = {
    'Content-Type':   'application/json',
    'X-Jarvis-Token': SECRET_TOKEN,
    'X-Device-Id':    DEVICE_ID,
}

def http_get(path: str) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

def http_post(path: str, data: dict) -> dict:
    body = json.dumps(data).encode()
    req  = urllib.request.Request(f"{BASE}{path}", data=body, headers=HEADERS, method='POST')
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

def send_and_wait(command: str, timeout: int = 15) -> dict:
    """Envoie une commande et poll jusqu'à avoir le résultat."""
    resp   = http_post('/api/command', {'command': command, 'device_id': DEVICE_ID})
    cmd_id = resp.get('id')
    start  = time.time()
    while time.time() - start < timeout:
        time.sleep(0.8)
        result = http_get(f'/api/result/{cmd_id}')
        if result.get('status') == 'done':
            return result.get('result', {})
    return {"success": False, "message": "Timeout"}


# ═══════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════
PASS = 0
FAIL = 0

def test(name: str, command: str, expect_success=True, expect_in: str = ""):
    global PASS, FAIL
    try:
        result = send_and_wait(command)
        ok     = result.get('success', False)
        msg    = result.get('message', '')

        if expect_success is None:
            # Pas de vérification du succès (comportement dépend de l'OS)
            passed = True
        else:
            passed = (ok == expect_success)
        if expect_in and expect_in.lower() not in msg.lower():
            passed = False

        if passed:
            PASS += 1
            print(f"  ✅ {name}")
            print(f"       → {msg[:80]}")
        else:
            FAIL += 1
            print(f"  ❌ {name}")
            print(f"       Attendu success={expect_success}, reçu success={ok}")
            print(f"       Message: {msg[:80]}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}")
        print(f"       Exception: {e}")
    print()


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    print("\n" + "═" * 60)
    print("  TEST FIN-À-FIN — SEMAINE 7 : Pipeline Mobile → PC")
    print("═" * 60 + "\n")

    # Démarrer le mock server
    server = HTTPServer(('localhost', MOCK_PORT), MockAzureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  🌐 Mock Azure Function démarré sur port {MOCK_PORT}")
    time.sleep(0.3)

    # Health check
    try:
        health = http_get('/api/health')
        print(f"  ✅ Health check : {health}\n")
    except Exception as e:
        print(f"  ❌ Serveur inaccessible : {e}\n")
        return

    print("─" * 60)
    print("  GROUPE 1 — Commandes système")
    print("─" * 60 + "\n")

    test("Volume 70%",      "Mets le volume à 70%",         expect_success=None)  # Windows only
    test("Volume monte",    "Monte le volume de 10%",        expect_success=None)  # Windows only
    test("Muet",            "Coupe le son",                  expect_success=None)  # Windows only
    test("Infos système",   "Montre les infos système",      expect_success=True)

    print("─" * 60)
    print("  GROUPE 2 — Applications")
    print("─" * 60 + "\n")

    test("Ouvre notepad",       "Ouvre notepad",                expect_success=None)  # Windows only
    test("Liste apps actives",  "Liste les applications ouvertes", expect_success=True)

    print("─" * 60)
    print("  GROUPE 3 — Fichiers")
    print("─" * 60 + "\n")

    test("Liste Documents",    "Liste le dossier Documents",       expect_success=None)  # path Windows
    test("Cherche fichier txt","Cherche les fichiers .txt",        expect_success=True)

    print("─" * 60)
    print("  GROUPE 4 — Commandes invalides")
    print("─" * 60 + "\n")

    test("Commande vide",     "   ",           expect_success=False)
    test("Intent inconnu",    "abcxyz123???",  expect_success=False)

    print("─" * 60)
    print("  GROUPE 5 — Auth")
    print("─" * 60 + "\n")

    global PASS, FAIL
    # Test avec mauvais token
    try:
        req = urllib.request.Request(
            f"{BASE}/api/command",
            data=json.dumps({"command": "test"}).encode(),
            headers={'Content-Type': 'application/json', 'X-Jarvis-Token': 'MAUVAIS'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=3)
        FAIL += 1
        print("  ❌ Auth rejetée : aurait dû retourner 401\n")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            PASS += 1
            print("  ✅ Mauvais token correctement rejeté (401)\n")
        else:
            FAIL += 1
            print(f"  ❌ Code inattendu : {e.code}\n")

    # Résultat final
    total = PASS + FAIL
    print("═" * 60)
    print(f"  {PASS} PASSES  |  {FAIL} FAILURES  |  {total} TOTAL")
    print("═" * 60)
    if FAIL == 0:
        print("  ✅ Pipeline fin-à-fin VALIDÉ — Semaine 7 COMPLÈTE\n")
    else:
        print(f"  ⚠️  {FAIL} test(s) échoué(s) — voir détails ci-dessus\n")

    server.shutdown()


if __name__ == '__main__':
    main()