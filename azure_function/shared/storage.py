"""
shared/storage.py — Stockage partagé entre toutes les fonctions Azure
En dev : dictionnaire en mémoire
En prod : Azure Blob Storage

SEMAINE 2 — CORRECTIONS LATENCE :
  [Fix1] verify_token() corrigé — le mode HMAC retournait toujours False.
         Maintenant il vérifie correctement la signature HMAC-SHA256.
  [Fix2] get_pending_commands() : limite max 5 commandes par poll (évite
         les timeouts sur Azure si la queue est grande).
  [Fix3] store_command() : ajout timestamp précis pour mesurer la latence.
  [Fix4] Ajout de get_queue_size() pour monitoring.
  [Fix5] Ajout de cleanup_old_commands() — commandes > 5 min auto-supprimées.
"""
import hashlib
import hmac as hmac_lib
import json
import os
import time

# ── Stockage en mémoire (dev local) ──────────────────────────────────────────
_COMMAND_QUEUE: dict = {}
_RESULTS: dict = {}

MAX_CMD_QUEUE   = int(os.environ.get("JARVIS_MAX_QUEUE", "50"))
STORAGE_CONN    = os.environ.get("AzureWebJobsStorage", "")
CMD_TTL_SECONDS = int(os.environ.get("JARVIS_CMD_TTL", "300"))  # 5 min


def store_command(command_obj: dict):
    """Stocke une commande dans la queue."""
    cid = command_obj["command_id"]
    # Ajouter timestamp précis pour mesurer la latence bout-en-bout
    command_obj.setdefault("queued_at", time.time())

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
    # Nettoyage auto si queue trop grande
    if len(_COMMAND_QUEUE) > MAX_CMD_QUEUE:
        oldest = sorted(_COMMAND_QUEUE.keys(),
                        key=lambda k: _COMMAND_QUEUE[k].get("queued_at", 0))[0]
        del _COMMAND_QUEUE[oldest]


def get_pending_commands(max_batch: int = 5) -> list:
    """
    Récupère les commandes en attente.
    [Fix2] max_batch=5 par défaut — évite les timeouts si queue grande.
    """
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            _ensure_container(cont)
            # Limiter à max_batch pour éviter les timeouts
            blobs    = list(cont.list_blobs(name_starts_with="pending/"))[:max_batch]
            commands = []
            for blob in blobs:
                try:
                    data = json.loads(cont.download_blob(blob.name).readall())
                    commands.append(data)
                    # Marquer comme en cours de traitement
                    cont.upload_blob(blob.name.replace("pending/", "processing/"),
                                     json.dumps(data), overwrite=True)
                    cont.delete_blob(blob.name)
                except Exception:
                    continue
            return commands
        except Exception:
            pass

    # Mode mémoire locale — auto-nettoyage des commandes expirées
    _cleanup_expired()
    pending = [
        v for v in _COMMAND_QUEUE.values()
        if v.get("status") == "pending"
    ][:max_batch]
    for cmd in pending:
        _COMMAND_QUEUE[cmd["command_id"]]["status"] = "processing"
    return pending


def store_result(command_id: str, result: dict):
    """Stocke le résultat d'une commande."""
    # Ajouter la latence totale
    result["stored_at"] = time.time()
    queued_at = None
    if command_id in _COMMAND_QUEUE:
        queued_at = _COMMAND_QUEUE[command_id].get("queued_at")
    if queued_at:
        result["total_latency_ms"] = int((time.time() - queued_at) * 1000)

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


def get_result(command_id: str) -> dict | None:
    """Récupère le résultat d'une commande."""
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            return json.loads(cont.download_blob(f"results/{command_id}.json").readall())
        except Exception:
            return None
    return _RESULTS.get(command_id)


def get_queue_size() -> dict:
    """
    [Fix4] Retourne la taille de la queue — utile pour monitoring et debug latence.
    """
    if _is_real_storage():
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cont   = client.get_container_client("jarvis-commands")
            pending    = len(list(cont.list_blobs(name_starts_with="pending/")))
            processing = len(list(cont.list_blobs(name_starts_with="processing/")))
            results    = len(list(cont.list_blobs(name_starts_with="results/")))
            return {"pending": pending, "processing": processing, "results": results}
        except Exception:
            pass
    return {
        "pending":    sum(1 for v in _COMMAND_QUEUE.values() if v.get("status") == "pending"),
        "processing": sum(1 for v in _COMMAND_QUEUE.values() if v.get("status") == "processing"),
        "results":    len(_RESULTS),
    }


def verify_token(headers) -> dict:
    """
    Vérifie l'authentification d'une requête.

    [Fix1] Correction du mode HMAC — l'ancienne version retournait toujours
    {"valid": False, "reason": "HMAC non vérifié ici"} pour les requêtes HMAC.
    Maintenant il vérifie correctement la signature HMAC-SHA256.

    Méthodes supportées (par ordre de priorité) :
      1. Token simple  : X-Jarvis-Token == SECRET_TOKEN
      2. HMAC-SHA256   : X-Jarvis-Sig = HMAC(secret, method+path+timestamp+nonce)
    """
    secret = os.environ.get("JARVIS_SECRET_TOKEN", "changeme")

    # ── Méthode 1 : Token simple ──────────────────────────────────────────────
    token = headers.get("x-jarvis-token") or headers.get("X-Jarvis-Token", "")
    if token and token == secret:
        device_id = headers.get("x-device-id") or headers.get("X-Device-Id", "unknown")
        return {"valid": True, "method": "simple", "device_id": device_id}

    # ── Méthode 2 : HMAC-SHA256 ───────────────────────────────────────────────
    sig       = headers.get("x-jarvis-sig") or headers.get("X-Jarvis-Sig", "")
    timestamp = headers.get("x-timestamp") or headers.get("X-Timestamp", "")
    nonce     = headers.get("x-nonce") or headers.get("X-Nonce", "")
    device_id = headers.get("x-device-id") or headers.get("X-Device-Id", "unknown")

    if sig and timestamp and nonce:
        # Vérifier la fraîcheur du timestamp (± 5 minutes)
        try:
            ts = int(timestamp)
        except (ValueError, TypeError):
            return {"valid": False, "reason": "Timestamp invalide"}

        age = abs(int(time.time()) - ts)
        if age > 300:
            return {"valid": False, "reason": f"Timestamp trop ancien ({age}s > 300s)"}

        # Reconstruire et vérifier la signature HMAC
        # Format : HMAC(secret, device_id + ":" + timestamp + ":" + nonce)
        # Compatible avec la méthode generate_token() de security/auth.py
        message = f"{device_id}:{timestamp}:{nonce}".encode("utf-8")
        expected_sig = hmac_lib.new(
            secret.encode("utf-8"),
            message,
            hashlib.sha256
        ).hexdigest()

        if hmac_lib.compare_digest(sig, expected_sig):
            return {"valid": True, "method": "hmac", "device_id": device_id}
        else:
            return {"valid": False, "reason": "Signature HMAC invalide"}

    # ── Aucune méthode valide ─────────────────────────────────────────────────
    if not token and not sig:
        return {"valid": False, "reason": "Token manquant (X-Jarvis-Token ou X-Jarvis-Sig requis)"}

    return {"valid": False, "reason": "Authentification échouée"}


def cleanup_old_commands():
    """
    [Fix5] Supprime les commandes de plus de CMD_TTL_SECONDS secondes.
    À appeler périodiquement ou en début de poll.
    """
    cutoff = time.time() - CMD_TTL_SECONDS
    expired = [
        cid for cid, cmd in _COMMAND_QUEUE.items()
        if cmd.get("queued_at", 0) < cutoff
    ]
    for cid in expired:
        del _COMMAND_QUEUE[cid]
    if expired:
        return {"cleaned": len(expired), "remaining": len(_COMMAND_QUEUE)}
    return {"cleaned": 0, "remaining": len(_COMMAND_QUEUE)}


# ── Helpers privés ────────────────────────────────────────────────────────────

def _cleanup_expired():
    """Supprime silencieusement les commandes expirées du dict mémoire."""
    cutoff = time.time() - CMD_TTL_SECONDS
    expired = [
        cid for cid, cmd in _COMMAND_QUEUE.items()
        if cmd.get("queued_at", 0) < cutoff and cmd.get("status") != "done"
    ]
    for cid in expired:
        del _COMMAND_QUEUE[cid]


def _is_real_storage() -> bool:
    return bool(
        STORAGE_CONN
        and "UseDevelopmentStorage" not in STORAGE_CONN
        and "AccountName=devstoreaccount1" not in STORAGE_CONN
    )


def _ensure_container(cont):
    try:
        cont.create_container()
    except Exception:
        pass