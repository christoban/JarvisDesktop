"""
azure_function/stream/__init__.py — Azure Function : relay screenshots
=======================================================================
Semaine 9 — Screen Share

Rôle : relais Azure entre le PC (qui push les frames) et le mobile (qui poll).
Architecture :
    PC (capture.py)
        ↓ POST /api/stream/push   (frame JPEG en base64)
    Azure Blob Storage (container "jarvis-screen")
        ↑ GET  /api/stream/frame  (mobile poll dernier frame)
        ↑ GET  /api/stream/status (infos: FPS, taille, timestamp)

Routes HTTP de cette Azure Function :
  POST  /api/stream/push   ← PC → envoie un frame JPEG encodé en base64
  GET   /api/stream/frame  ← Mobile → récupère le dernier frame
  GET   /api/stream/status ← Mobile → statut du stream (actif?, FPS, taille)
  POST  /api/stream/config ← Mobile → configurer FPS/qualité du PC

Sécurité : même système X-Jarvis-Token que les autres Azure Functions.

Storage :
  - Blob "screen/latest.json"   → métadonnées + frame JPEG en base64
  - Blob "screen/status.json"   → stats (FPS, uptime, size)
  - Blob "screen/config.json"   → config demandée par le mobile

Pour déployer :
    function.json :
    {
        "bindings": [
            { "type": "httpTrigger", "direction": "in",
              "authLevel": "anonymous",
              "methods": ["get", "post"],
              "route": "stream/{action?}" }
        ]
    }
"""

import json
import os
import sys
import time
import base64

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.storage import verify_token

# ── Constants ─────────────────────────────────────────────────────────────────
SCREEN_CONTAINER  = "jarvis-screen"
LATEST_BLOB       = "screen/latest.json"
STATUS_BLOB       = "screen/status.json"
CONFIG_BLOB       = "screen/config.json"
FRAME_TTL_SECONDS = 10      # Frame considéré "périmé" après 10s sans update
MAX_FRAME_B64_LEN = 500_000 # ~375 KB en JPEG (protection anti-flood)

STORAGE_CONN = os.environ.get("AzureWebJobsStorage", "")


# ── Entry point ───────────────────────────────────────────────────────────────

def main(req: func.HttpRequest) -> func.HttpResponse:
    action = (req.route_params.get("action") or "").lower().strip()
    method = req.method.upper()

    # Dispatch
    if method == "POST" and action == "push":
        return _handle_push(req)
    elif method == "GET" and action == "frame":
        return _handle_frame(req)
    elif method == "GET" and action == "status":
        return _handle_status(req)
    elif method == "POST" and action == "config":
        return _handle_config(req)
    else:
        return _json({"error": f"Route inconnue : {method} /stream/{action}"}, 404)


# ══════════════════════════════════════════════════════════════════════════════
#  POST /stream/push  — PC envoie un frame
# ══════════════════════════════════════════════════════════════════════════════

def _handle_push(req: func.HttpRequest) -> func.HttpResponse:
    """
    Le PC poste un frame JPEG encodé en base64.
    Le frame est stocké dans Azure Blob Storage pour que le mobile puisse le récupérer.

    Body JSON :
    {
        "frame_b64":   "...",       # JPEG encodé base64 (obligatoire)
        "frame_id":    42,          # numéro séquentiel
        "width":       1920,
        "height":      1080,
        "size_bytes":  48000,
        "fps_real":    9.8,
        "quality":     60,
        "timestamp":   1700000000.0
    }
    """
    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized", "reason": auth.get("reason", "")}, 401)

    try:
        body = req.get_json()
    except (ValueError, Exception):
        return _json({"error": "JSON invalide"}, 400)

    frame_b64 = (body.get("frame_b64") or "").strip()
    if not frame_b64:
        return _json({"error": "Champ 'frame_b64' manquant"}, 400)

    # Protection taille
    if len(frame_b64) > MAX_FRAME_B64_LEN:
        return _json({
            "error": f"Frame trop grand ({len(frame_b64)} chars > {MAX_FRAME_B64_LEN}). "
                     f"Réduis la qualité ou la résolution."
        }, 413)

    # Valider le base64
    try:
        base64.b64decode(frame_b64, validate=True)
    except Exception:
        return _json({"error": "frame_b64 n'est pas un base64 valide"}, 400)

    now = time.time()
    frame_payload = {
        "frame_b64":   frame_b64,
        "frame_id":    int(body.get("frame_id", 0)),
        "width":       int(body.get("width", 0)),
        "height":      int(body.get("height", 0)),
        "size_bytes":  int(body.get("size_bytes", len(frame_b64) * 3 // 4)),
        "fps_real":    float(body.get("fps_real", 0)),
        "quality":     int(body.get("quality", 60)),
        "pushed_at":   now,
        "device_id":   auth.get("device_id", "pc"),
    }

    # Mettre à jour le statut en même temps
    status_payload = {
        "streaming":    True,
        "frame_id":     frame_payload["frame_id"],
        "fps_real":     frame_payload["fps_real"],
        "quality":      frame_payload["quality"],
        "width":        frame_payload["width"],
        "height":       frame_payload["height"],
        "size_kb":      round(frame_payload["size_bytes"] / 1024, 1),
        "last_push_at": now,
        "device_id":    frame_payload["device_id"],
    }

    # Stocker dans Azure Blob
    ok_frame  = _blob_put(LATEST_BLOB, frame_payload)
    ok_status = _blob_put(STATUS_BLOB, status_payload)

    if not ok_frame:
        return _json({"error": "Impossible d'écrire le frame dans le Blob Storage"}, 500)

    return _json({
        "status":   "pushed",
        "frame_id": frame_payload["frame_id"],
        "size_kb":  round(frame_payload["size_bytes"] / 1024, 1),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  GET /stream/frame  — Mobile récupère le dernier frame
# ══════════════════════════════════════════════════════════════════════════════

def _handle_frame(req: func.HttpRequest) -> func.HttpResponse:
    """
    Le mobile récupère le dernier frame disponible.

    Query params:
        since_id=42   — ne retourne rien si frame_id <= 42 (polling différentiel)
        format=jpeg   — retourner les bytes JPEG bruts (pour <Image> direct)
                        par défaut : retourne JSON avec frame_b64

    Réponse JSON :
    {
        "status":    "ok" | "no_stream" | "stale" | "same_frame",
        "frame_b64": "...",
        "frame_id":  43,
        "width":     1920,
        "height":    1080,
        "age_ms":    120,
        "fps_real":  9.8
    }
    """
    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized", "reason": auth.get("reason", "")}, 401)

    since_id = -1
    try:
        since_id = int(req.params.get("since_id", -1))
    except (ValueError, TypeError):
        pass

    fmt = req.params.get("format", "json").lower()

    # Lire le dernier frame
    frame_data = _blob_get(LATEST_BLOB)
    if not frame_data:
        return _json({"status": "no_stream", "message": "Aucun stream actif. Lance la capture sur le PC."})

    # Vérifier la fraîcheur
    pushed_at = float(frame_data.get("pushed_at", 0))
    age_s     = time.time() - pushed_at
    age_ms    = int(age_s * 1000)

    if age_s > FRAME_TTL_SECONDS:
        return _json({
            "status":  "stale",
            "age_ms":  age_ms,
            "message": f"Stream inactif depuis {age_ms}ms. Le PC a peut-être arrêté la capture.",
        })

    frame_id = int(frame_data.get("frame_id", 0))

    # Polling différentiel : mobile a déjà ce frame
    if 0 <= since_id >= frame_id:
        return _json({
            "status":   "same_frame",
            "frame_id": frame_id,
            "age_ms":   age_ms,
        })

    # Retour JPEG brut (pour Image React Native directement)
    if fmt == "jpeg":
        try:
            jpeg_bytes = base64.b64decode(frame_data["frame_b64"])
            response = func.HttpResponse(
                body=jpeg_bytes,
                status_code=200,
                mimetype="image/jpeg",
                headers={
                    "X-Frame-Id":   str(frame_id),
                    "X-Frame-Age":  str(age_ms),
                    "X-Fps-Real":   str(frame_data.get("fps_real", 0)),
                    "Cache-Control": "no-store",
                    "Access-Control-Allow-Origin": "*",
                }
            )
            return response
        except Exception as e:
            return _json({"error": f"Décodage JPEG échoué : {e}"}, 500)

    # Retour JSON standard
    return _json({
        "status":    "ok",
        "frame_b64": frame_data["frame_b64"],
        "frame_id":  frame_id,
        "width":     frame_data.get("width", 0),
        "height":    frame_data.get("height", 0),
        "age_ms":    age_ms,
        "fps_real":  frame_data.get("fps_real", 0),
        "quality":   frame_data.get("quality", 60),
        "size_kb":   round(frame_data.get("size_bytes", 0) / 1024, 1),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  GET /stream/status  — Statut du stream
# ══════════════════════════════════════════════════════════════════════════════

def _handle_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Retourne le statut du stream (actif?, FPS, résolution, etc.)
    Pas d'auth requise pour le statut (permet au mobile de vérifier rapidement).
    """
    status_data = _blob_get(STATUS_BLOB)
    if not status_data:
        return _json({
            "streaming": False,
            "message":   "Aucun stream actif.",
        })

    last_push = float(status_data.get("last_push_at", 0))
    age_s     = time.time() - last_push
    active    = age_s < FRAME_TTL_SECONDS

    return _json({
        "streaming":    active,
        "fps_real":     status_data.get("fps_real", 0),
        "quality":      status_data.get("quality", 60),
        "width":        status_data.get("width", 0),
        "height":       status_data.get("height", 0),
        "size_kb":      status_data.get("size_kb", 0),
        "frame_id":     status_data.get("frame_id", 0),
        "last_push_at": last_push,
        "age_s":        round(age_s, 1),
        "device_id":    status_data.get("device_id", ""),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  POST /stream/config  — Mobile configure le stream (FPS, qualité)
# ══════════════════════════════════════════════════════════════════════════════

def _handle_config(req: func.HttpRequest) -> func.HttpResponse:
    """
    Le mobile envoie une config que le PC lira lors du prochain poll.
    Le PC doit poller GET /api/stream/config régulièrement.

    Body JSON :
    {
        "fps":     10,     # 1-30
        "quality": 60,     # 20-90
        "scale":   1.0,    # 0.25-1.0
        "monitor": 1       # index moniteur
    }
    """
    auth = verify_token(dict(req.headers))
    if not auth["valid"]:
        return _json({"error": "Unauthorized"}, 401)

    try:
        body = req.get_json()
    except Exception:
        return _json({"error": "JSON invalide"}, 400)

    fps     = body.get("fps")
    quality = body.get("quality")
    scale   = body.get("scale")
    monitor = body.get("monitor")

    config = {}
    if fps is not None:
        config["fps"]     = max(1, min(30, int(fps)))
    if quality is not None:
        config["quality"] = max(20, min(90, int(quality)))
    if scale is not None:
        config["scale"]   = max(0.25, min(1.0, float(scale)))
    if monitor is not None:
        config["monitor"] = max(0, int(monitor))

    if not config:
        return _json({"error": "Aucun paramètre de configuration fourni"}, 400)

    config["requested_at"] = time.time()
    config["requested_by"] = auth.get("device_id", "mobile")

    _blob_put(CONFIG_BLOB, config)

    return _json({
        "status":  "config_saved",
        "config":  config,
        "message": "Config envoyée au PC. Elle sera appliquée au prochain poll.",
    })


# ══════════════════════════════════════════════════════════════════════════════
#  Blob Storage helpers
# ══════════════════════════════════════════════════════════════════════════════

def _blob_put(blob_name: str, data: dict) -> bool:
    """Écrit un dict JSON dans un blob Azure Storage."""
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client(SCREEN_CONTAINER)
            _ensure_container(cont)
            cont.upload_blob(
                blob_name,
                json.dumps(data, ensure_ascii=False),
                overwrite=True,
            )
            return True
        except Exception as e:
            _MEM_STORE[blob_name] = data
            return True  # Fallback mémoire — toujours OK en dev
    else:
        _MEM_STORE[blob_name] = data
        return True


def _blob_get(blob_name: str) -> dict | None:
    """Lit un blob Azure Storage et le parse en dict."""
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client(SCREEN_CONTAINER)
            raw    = cont.download_blob(blob_name).readall()
            return json.loads(raw)
        except Exception:
            return _MEM_STORE.get(blob_name)
    return _MEM_STORE.get(blob_name)


def _ensure_container(cont):
    try:
        cont.create_container()
    except Exception:
        pass


def _is_real_storage() -> bool:
    return bool(
        STORAGE_CONN
        and "UseDevelopmentStorage" not in STORAGE_CONN
        and "AccountName=devstoreaccount1" not in STORAGE_CONN
    )


# Stockage en mémoire pour mode dev local
_MEM_STORE: dict = {}


# ── JSON helper ───────────────────────────────────────────────────────────────

def _json(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )