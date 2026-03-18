import json
import os
import sys
import time
import uuid

import azure.functions as func

# Ajouter le dossier parent au path pour importer shared
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.storage import store_command, verify_token


def main(req: func.HttpRequest) -> func.HttpResponse:

    # Auth
    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized", "reason": auth.get("reason", "")}, 401)

    # Parsing
    try:
        body = req.get_json()
    except ValueError:
        return _json({"error": "Invalid JSON body"}, 400)

    command   = (body.get("command") or "").strip()
    device_id = body.get("device_id", "unknown")
    timestamp = int(body.get("timestamp", time.time()))

    if not command:
        return _json({"error": "Missing 'command' field"}, 400)

    # Anti-replay
    age = abs(int(time.time()) - timestamp)
    if age > 300:
        return _json({"error": "Request too old", "age_seconds": age}, 400)

    # Stocker la commande
    command_id  = str(uuid.uuid4())
    command_obj = {
        "command_id":  command_id,
        "command":     command,
        "device_id":   device_id,
        "timestamp":   timestamp,
        "received_at": int(time.time()),
        "status":      "pending",
    }
    store_command(command_obj)

    return _json({
        "status":     "queued",
        "command_id": command_id,
        "message":    f"Commande '{command[:30]}' reçue.",
    }, 202)


def _json(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status,
        mimetype="application/json"
    )