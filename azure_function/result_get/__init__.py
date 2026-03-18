import json
import os
import sys

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.storage import get_result, verify_token


def main(req: func.HttpRequest) -> func.HttpResponse:

    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized"}, 401)

    command_id = req.route_params.get("command_id", "")
    if not command_id:
        return _json({"error": "Missing command_id"}, 400)

    result = get_result(command_id)
    if result:
        return _json({"status": "done", "command_id": command_id, **result})
    else:
        return _json({"status": "pending", "command_id": command_id})


def _json(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status,
        mimetype="application/json"
    )