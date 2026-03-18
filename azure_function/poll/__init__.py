import json
import os
import sys

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.storage import get_pending_commands, verify_token


def main(req: func.HttpRequest) -> func.HttpResponse:

    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized", "reason": auth.get("reason", "")}, 401)

    try:
        pending = get_pending_commands()
        return _json({"commands": pending, "count": len(pending)})
    except Exception as e:
        return _json({"error": str(e)}, 500)


def _json(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status,
        mimetype="application/json"
    )