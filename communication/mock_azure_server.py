"""
mock_azure_server.py — Serveur Azure Function simulé pour les tests locaux
Reproduit exactement les 5 routes de la vraie Azure Function.

SEMAINE 6 — MERCREDI — SERVEUR DE TEST
  Démarre un serveur HTTP local sur 127.0.0.1:7071
  Permet de tester le flux complet sans Azure réel :
    - Test Postman : POST http://localhost:7071/api/command
    - Flux PC      : GET  http://localhost:7071/api/poll

LANCER :
  python communication/mock_azure_server.py
  # → Serveur actif sur http://localhost:7071

TESTER AVEC CURL :
  # Envoyer une commande
  curl -X POST http://localhost:7071/api/command \\
    -H "Content-Type: application/json" \\
    -H "X-Jarvis-Token: changeme" \\
    -d '{"command": "ouvre chrome", "device_id": "test", "timestamp": 1704067200}'

  # Voir les commandes en attente
  curl -X GET http://localhost:7071/api/poll \\
    -H "X-Jarvis-Token: changeme"

  # Santé
  curl http://localhost:7071/api/health
"""

import hashlib
import hmac
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ── Ajouter le projet au path pour les imports ───────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from config.settings import SECRET_TOKEN
except ImportError:
    SECRET_TOKEN = "changeme"

# ── Stockage en mémoire ───────────────────────────────────────────────────────
_COMMAND_QUEUE:  dict = {}   # command_id → command_obj
_RESULTS:        dict = {}   # command_id → result_obj
_REQUEST_LOG:    list = []   # log de toutes les requêtes

MOCK_VERSION = "1.0.0-mock"


class MockAzureHandler(BaseHTTPRequestHandler):
    """Gestionnaire HTTP qui simule l'Azure Function."""

    def log_message(self, fmt, *args):
        """Surcharge pour un log plus propre."""
        method = self.command
        path   = self.path
        ts     = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {method} {path} → {args[1] if len(args) > 1 else ''}")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mock-Server", "Jarvis-Azure-Mock")
        self.end_headers()
        self.wfile.write(body)

    def _verify_token(self, body: bytes = b"") -> dict:
        """Vérifie le token d'authentification (même logique que la vraie Function)."""
        # Méthode simple
        token = self.headers.get("X-Jarvis-Token", "")
        if token == SECRET_TOKEN:
            return {"valid": True, "method": "simple_token"}

        # Méthode HMAC
        signature = self.headers.get("X-Jarvis-Signature", "")
        timestamp = self.headers.get("X-Jarvis-Timestamp", "")

        if signature and timestamp:
            age = abs(int(time.time()) - int(timestamp))
            if age > 300:
                return {"valid": False, "reason": f"Timestamp trop ancien ({age}s)"}

            body_hash = hashlib.sha256(body).hexdigest()
            path      = self.path.split("?")[0].split("/api/")[-1]
            message   = f"{self.command}:{path}:{timestamp}:{body_hash}"
            expected  = hmac.new(
                SECRET_TOKEN.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            if hmac.compare_digest(expected, signature):
                return {"valid": True, "method": "hmac_sha256"}
            return {"valid": False, "reason": "Signature invalide"}

        return {"valid": False, "reason": "Token ou signature manquant(e)"}

    # ── Routes GET ────────────────────────────────────────────────────────────

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/health":
            self._send_json({
                "status":    "healthy",
                "version":   MOCK_VERSION,
                "service":   "Jarvis Azure Mock Server",
                "timestamp": int(time.time()),
                "queue_size": len([c for c in _COMMAND_QUEUE.values()
                                   if c.get("status") == "pending"]),
                "results_count": len(_RESULTS),
            })

        elif path == "/api/poll":
            auth = self._verify_token()
            if not auth["valid"]:
                self._send_json({"error": "Unauthorized", "reason": auth["reason"]}, 401)
                return

            # Récupérer les commandes pending
            pending = [v for v in _COMMAND_QUEUE.values()
                       if v.get("status") == "pending"]
            # Marquer comme processing
            for cmd in pending:
                _COMMAND_QUEUE[cmd["command_id"]]["status"] = "processing"

            self._send_json({"commands": pending, "count": len(pending)})

        elif path.startswith("/api/result/"):
            command_id = path.split("/api/result/")[-1]
            auth       = self._verify_token()
            if not auth["valid"]:
                self._send_json({"error": "Unauthorized"}, 401)
                return

            result = _RESULTS.get(command_id)
            if result:
                self._send_json({"status": "done", "command_id": command_id, **result})
            else:
                cmd = _COMMAND_QUEUE.get(command_id)
                if cmd:
                    self._send_json({
                        "status": cmd.get("status", "pending"),
                        "command_id": command_id,
                        "command": cmd.get("command", ""),
                    })
                else:
                    self._send_json({"error": "Command not found"}, 404)

        elif path == "/api/queue":
            # Route bonus : voir toute la queue (debug)
            self._send_json({
                "queue":   list(_COMMAND_QUEUE.values()),
                "results": {k: v for k, v in _RESULTS.items()},
            })

        else:
            self._send_json({"error": f"Route inconnue : {path}"}, 404)

    # ── Routes POST ───────────────────────────────────────────────────────────

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]

        if path == "/api/command":
            auth = self._verify_token(body)
            if not auth["valid"]:
                self._send_json({"error": "Unauthorized", "reason": auth["reason"]}, 401)
                return

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            command   = data.get("command", "").strip()
            device_id = data.get("device_id", "unknown")
            timestamp = int(data.get("timestamp", time.time()))

            if not command:
                self._send_json({"error": "Missing 'command' field"}, 400)
                return

            # Vérifier âge timestamp
            age = abs(int(time.time()) - timestamp)
            if age > 300:
                self._send_json({"error": "Request too old", "age_seconds": age}, 400)
                return

            command_id  = str(uuid.uuid4())
            command_obj = {
                "command_id":  command_id,
                "command":     command,
                "device_id":   device_id,
                "timestamp":   timestamp,
                "received_at": int(time.time()),
                "status":      "pending",
                "result":      None,
            }
            _COMMAND_QUEUE[command_id] = command_obj

            print(f"\n  📱 Nouvelle commande : '{command}' (id={command_id[:8]})")

            self._send_json({
                "status":     "queued",
                "command_id": command_id,
                "message":    f"Commande '{command[:30]}' reçue.",
            }, 202)

        elif path == "/api/result":
            auth = self._verify_token(body)
            if not auth["valid"]:
                self._send_json({"error": "Unauthorized"}, 401)
                return

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            command_id  = data.get("command_id", "")
            success     = data.get("success", False)
            message     = data.get("message", "")
            result_data = data.get("data")
            executed_at = data.get("executed_at", int(time.time()))

            if not command_id:
                self._send_json({"error": "Missing command_id"}, 400)
                return

            _RESULTS[command_id] = {
                "success":     success,
                "message":     message,
                "data":        result_data,
                "executed_at": executed_at,
            }
            if command_id in _COMMAND_QUEUE:
                _COMMAND_QUEUE[command_id]["status"] = "done"

            icon = "✅" if success else "❌"
            print(f"\n  {icon} Résultat reçu : '{message[:60]}' (id={command_id[:8]})")

            self._send_json({"status": "stored", "command_id": command_id})

        else:
            self._send_json({"error": f"Route inconnue : {path}"}, 404)


def start_mock_server(host: str = "127.0.0.1", port: int = 7071,
                      background: bool = False) -> HTTPServer:
    """
    Lance le serveur mock Azure Function.

    Args:
        host       : adresse d'écoute (défaut 127.0.0.1)
        port       : port (défaut 7071 = port dev Azure Functions)
        background : True = thread daemon, False = bloquant

    Returns:
        Instance HTTPServer (pour l'arrêter : server.shutdown())
    """
    server = HTTPServer((host, port), MockAzureHandler)

    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.1)  # Laisser le temps de démarrer
        print(f"  🚀 Mock Azure Server en arrière-plan : http://{host}:{port}")
    else:
        print(f"\n{'='*60}")
        print(f"  🚀 Mock Azure Function Server")
        print(f"  URL    : http://{host}:{port}")
        print(f"  Token  : {SECRET_TOKEN}")
        print(f"{'='*60}")
        print(f"  Routes disponibles :")
        print(f"    POST /api/command  — Envoyer une commande")
        print(f"    GET  /api/poll     — PC récupère les commandes")
        print(f"    POST /api/result   — PC envoie le résultat")
        print(f"    GET  /api/result/{{id}} — App récupère le résultat")
        print(f"    GET  /api/health   — Diagnostic")
        print(f"    GET  /api/queue    — Debug : voir toute la queue")
        print(f"{'='*60}\n")
        print(f"  Ctrl+C pour arrêter\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  Serveur arrêté.")

    return server


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Mock Azure Function Server")
    parser.add_argument("--port", type=int, default=7071)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    start_mock_server(args.host, args.port, background=False)