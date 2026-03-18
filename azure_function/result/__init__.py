import json
import os
import sys
import time

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.storage import store_result, verify_token


def main(req: func.HttpRequest) -> func.HttpResponse:

    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized"}, 401)

    try:
        body = req.get_json()
    except ValueError:
        return _json({"error": "Invalid JSON"}, 400)

    command_id = body.get("command_id", "")
    if not command_id:
        return _json({"error": "Missing command_id"}, 400)

    store_result(command_id, {
        "success":     body.get("success", False),
        "message":     body.get("message", ""),
        "data":        body.get("data"),
        "executed_at": body.get("executed_at", int(time.time())),
    })

    return _json({"status": "stored", "command_id": command_id})


def _json(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status,
        mimetype="application/json"
    )