import json
import time
import azure.functions as func


def main(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({
            "status":    "healthy",
            "version":   "1.0.0",
            "service":   "Jarvis Windows Azure Function",
            "timestamp": int(time.time()),
        }),
        status_code=200,
        mimetype="application/json"
    )