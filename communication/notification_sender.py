"""
notification_sender.py — Push notifications vers le téléphone
SEMAINE 9 — LUNDI/MARDI

Backends (priorité):
  1) Azure Notification Hub
  2) Bridge local WiFi (POST /api/notify)
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from config.logger import get_logger
from config.settings import (
    AZURE_NOTIFICATION_HUB_CONNECTION,
    AZURE_NOTIFICATION_HUB_NAME,
    SECRET_TOKEN,
)

logger = get_logger(__name__)


PRIORITY_MAP = {
    "error": "high",
    "battery_low": "high",
    "task_done": "normal",
    "screenshot": "normal",
    "info": "low",
}


class NotificationSender:
    """Envoie des notifications push via Azure Hub ou bridge local."""

    def __init__(self, bridge_url: str = ""):
        self._lock = threading.Lock()
        self._queue: list[dict] = []
        self._bridge_url = (bridge_url or "http://localhost:7071").rstrip("/")

        self._hub_config = self._parse_hub_connection(AZURE_NOTIFICATION_HUB_CONNECTION)
        hub_ready = bool(self._hub_config and AZURE_NOTIFICATION_HUB_NAME)
        self.backend = "azure_hub" if hub_ready else "bridge"

        logger.info(f"NotificationSender initialisé — backend={self.backend}")

    def send(self, title: str, body: str, data: dict = None, type: str = "info") -> dict:
        """
        Fonction centrale d'envoi de notification.

        Args:
            title: titre push
            body: texte push
            data: payload additionnel
            type: info | task_done | error | battery_low | screenshot
        """
        if not title or not body:
            return self._err("Titre ou corps vide.")

        payload = {
            "title": title,
            "body": body,
            "data": data or {},
            "type": type,
            "priority": PRIORITY_MAP.get(type, "normal"),
            "timestamp": int(time.time()),
        }

        logger.info(f"Notification [{type}] → '{title}'")
        if self.backend == "azure_hub":
            return self._send_azure(payload)
        return self._send_bridge(payload)

    def notify_task_done(self, task: str, msg: str = "") -> dict:
        body = msg or f"{task} — commande exécutée."
        return self.send(
            "Tache terminee",
            body,
            data={"task": task},
            type="task_done",
        )

    def notify_error(self, msg: str, context: str = "") -> dict:
        return self.send(
            "Erreur critique Jarvis",
            msg,
            data={"context": context},
            type="error",
        )

    def notify_battery_low(self, level: int) -> dict:
        return self.send(
            "Batterie faible",
            f"Batterie a {level}%",
            data={"battery_level": int(level)},
            type="battery_low",
        )

    def notify_screenshot(self, path: str = "", b64: str = "") -> dict:
        preview = b64[:80] + "..." if len(b64) > 80 else b64
        body = "Votre capture est prete dans Jarvis mobile."
        if path:
            body = f"Capture disponible. Fichier source: {path}"
        return self.send(
            "Capture d'ecran prete",
            body,
            data={"path": path, "image": preview},
            type="screenshot",
        )

    def _send_bridge(self, payload: dict, queue_on_fail: bool = True) -> dict:
        """POST /api/notify vers jarvis_bridge.py."""
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self._bridge_url}/api/notify",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Jarvis-Token": SECRET_TOKEN,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            return self._ok("Notification envoyee via bridge.")

        except urllib.error.URLError:
            if queue_on_fail:
                with self._lock:
                    self._queue.append(payload)
                    pending = len(self._queue)
                return self._ok(f"Bridge hors ligne, notification en attente ({pending}).")
            return self._err("Bridge injoignable.")

        except Exception as e:
            logger.error(f"Erreur bridge notification: {e}")
            if queue_on_fail:
                with self._lock:
                    self._queue.append(payload)
                    pending = len(self._queue)
                return self._ok(f"Erreur bridge, notification en attente ({pending}).")
            return self._err(str(e))

    def _send_azure(self, payload: dict) -> dict:
        """
        Appel REST Azure Notification Hub.
        En cas d'echec: fallback automatique vers bridge.
        """
        try:
            if not self._hub_config or not AZURE_NOTIFICATION_HUB_NAME:
                return self._send_bridge(payload)

            endpoint = self._hub_config["endpoint"]
            key_name = self._hub_config["key_name"]
            key = self._hub_config["key"]

            resource_uri = f"{endpoint}{AZURE_NOTIFICATION_HUB_NAME}"
            expiry = str(int(time.time()) + 300)
            encoded_uri = urllib.parse.quote(resource_uri.lower(), safe="")
            to_sign = f"{encoded_uri}\n{expiry}".encode("utf-8")

            key_bytes = base64.b64decode(key)
            signature = base64.b64encode(hmac.new(key_bytes, to_sign, hashlib.sha256).digest()).decode("utf-8")
            encoded_sig = urllib.parse.quote(signature, safe="")

            sas_token = (
                f"SharedAccessSignature sr={encoded_uri}"
                f"&sig={encoded_sig}&se={expiry}&skn={key_name}"
            )

            notification = {
                "notification": {
                    "title": payload["title"],
                    "body": payload["body"],
                },
                "data": {
                    **payload.get("data", {}),
                    "type": payload.get("type", "info"),
                },
                "priority": "high" if payload.get("priority") == "high" else "normal",
            }

            req = urllib.request.Request(
                f"{resource_uri}/messages/?api-version=2015-01",
                data=json.dumps(notification).encode("utf-8"),
                method="POST",
            )
            req.add_header("Authorization", sas_token)
            req.add_header("Content-Type", "application/json;charset=utf-8")
            req.add_header("ServiceBusNotification-Format", "gcm")
            req.add_header("ServiceBusNotification-Tags", "all_devices")

            with urllib.request.urlopen(req, timeout=10):
                pass
            return self._ok("Notification envoyee via Azure Hub.")

        except Exception as e:
            logger.error(f"Erreur Azure Hub: {e} — fallback bridge")
            return self._send_bridge(payload)

    def flush_queue(self) -> int:
        """Retente l'envoi des notifications en attente. Retourne le nombre envoye."""
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()

        sent = 0
        for payload in pending:
            result = self._send_bridge(payload, queue_on_fail=False)
            if result.get("success"):
                sent += 1
            else:
                with self._lock:
                    self._queue.append(payload)
        return sent

    def health_check(self) -> dict:
        """Ping bridge local /api/health."""
        try:
            req = urllib.request.Request(
                f"{self._bridge_url}/api/health",
                headers={"X-Jarvis-Token": SECRET_TOKEN},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3):
                pass
            return {
                "available": True,
                "backend": self.backend,
                "message": "Bridge joignable.",
                "queued": self._queued_count(),
            }
        except Exception:
            return {
                "available": False,
                "backend": self.backend,
                "message": "Bridge inaccessible.",
                "queued": self._queued_count(),
            }

    @staticmethod
    def _parse_hub_connection(connection_string: str) -> dict | None:
        if not connection_string:
            return None
        if connection_string.startswith("VOTRE"):
            return None

        try:
            parts = {}
            for pair in connection_string.split(";"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    parts[k] = v

            endpoint = parts.get("Endpoint", "")
            key_name = parts.get("SharedAccessKeyName", "")
            key = parts.get("SharedAccessKey", "")
            if not endpoint or not key_name or not key:
                return None

            endpoint = endpoint.replace("sb://", "https://")
            if not endpoint.endswith("/"):
                endpoint += "/"

            return {
                "endpoint": endpoint,
                "key_name": key_name,
                "key": key,
            }
        except Exception:
            return None

    def _queued_count(self) -> int:
        with self._lock:
            return len(self._queue)

    @staticmethod
    def _ok(message: str, data: dict = None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data: dict = None) -> dict:
        return {"success": False, "message": message, "data": data}