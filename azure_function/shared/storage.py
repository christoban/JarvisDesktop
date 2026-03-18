"""
shared/storage.py — Stockage partagé entre toutes les fonctions
En dev : dictionnaire en mémoire
En prod : Azure Blob Storage
"""
import json
import os
import time

# Stockage en mémoire (dev local)
_COMMAND_QUEUE: dict = {}
_RESULTS: dict = {}

MAX_CMD_QUEUE = int(os.environ.get("JARVIS_MAX_QUEUE", "50"))
STORAGE_CONN  = os.environ.get("AzureWebJobsStorage", "")


def store_command(command_obj: dict):
    cid = command_obj["command_id"]
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            _ensure_container(cont)
            cont.upload_blob(f"pending/{cid}.json",
                             json.dumps(command_obj), overwrite=True)
            return
        except Exception:
            pass
    _COMMAND_QUEUE[cid] = command_obj
    if len(_COMMAND_QUEUE) > MAX_CMD_QUEUE:
        oldest = sorted(_COMMAND_QUEUE.keys())[0]
        del _COMMAND_QUEUE[oldest]


def get_pending_commands() -> list:
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            _ensure_container(cont)
            blobs    = list(cont.list_blobs(name_starts_with="pending/"))[:10]
            commands = []
            for blob in blobs:
                data = json.loads(cont.download_blob(blob.name).readall())
                commands.append(data)
                cont.upload_blob(blob.name.replace("pending/", "processing/"),
                                 json.dumps(data), overwrite=True)
                cont.delete_blob(blob.name)
            return commands
        except Exception:
            pass
    pending = [v for v in _COMMAND_QUEUE.values() if v.get("status") == "pending"]
    for cmd in pending:
        _COMMAND_QUEUE[cmd["command_id"]]["status"] = "processing"
    return pending


def store_result(command_id: str, result: dict):
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            _ensure_container(cont)
            cont.upload_blob(f"results/{command_id}.json",
                             json.dumps(result), overwrite=True)
            try:
                cont.delete_blob(f"processing/{command_id}.json")
            except Exception:
                pass
            return
        except Exception:
            pass
    _RESULTS[command_id] = result
    if command_id in _COMMAND_QUEUE:
        _COMMAND_QUEUE[command_id]["status"] = "done"


def get_result(command_id: str):
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            return json.loads(cont.download_blob(f"results/{command_id}.json").readall())
        except Exception:
            return None
    return _RESULTS.get(command_id)


def verify_token(headers) -> dict:
    """Vérifie X-Jarvis-Token ou signature HMAC."""
    import hashlib, hmac as hmac_lib
    secret = os.environ.get("JARVIS_SECRET_TOKEN", "changeme")

    # Méthode simple : token direct
    token = headers.get("x-jarvis-token") or headers.get("X-Jarvis-Token", "")
    if token == secret:
        return {"valid": True}

    # Méthode HMAC
    signature  = headers.get("x-jarvis-signature") or headers.get("X-Jarvis-Signature", "")
    timestamp  = headers.get("x-jarvis-timestamp") or headers.get("X-Jarvis-Timestamp", "")
    if signature and timestamp:
        age = abs(int(time.time()) - int(timestamp))
        if age > 300:
            return {"valid": False, "reason": f"Timestamp trop ancien ({age}s)"}
        # On ne peut pas reconstruire le body_hash ici sans le body → on vérifie juste token
        # Pour la vraie vérification HMAC complète, chaque fonction le fait elle-même
        return {"valid": False, "reason": "HMAC non vérifié ici"}

    return {"valid": False, "reason": "Token manquant"}


def _is_real_storage() -> bool:
    return bool(STORAGE_CONN and "UseDevelopmentStorage" not in STORAGE_CONN
                and "AccountName=devstoreaccount1" not in STORAGE_CONN)


def _ensure_container(cont):
    try:
        cont.create_container()
    except Exception:
        pass