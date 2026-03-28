"""Core/Telegram API.

Semaine 11: App control messagerie + email.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from config.logger import get_logger
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = get_logger(__name__)


class TelegramAPI:
    """Client minimal pour envoyer des messages via l'API Bot Telegram."""

    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.default_chat_id = TELEGRAM_CHAT_ID

    def is_configured(self) -> bool:
        return bool(self.token and self.default_chat_id)

    def _build_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send_message(self, to: str, message: str) -> dict:
        if not self.token:
            return {"success": False, "message": "TELEGRAM_BOT_TOKEN non configuré"}

        target = to or self.default_chat_id
        if not target:
            return {"success": False, "message": "TELEGRAM_CHAT_ID non configuré"}

        if not message:
            return {"success": False, "message": "Message vide"}

        data = urllib.parse.urlencode({
            "chat_id": target,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": "false",
        }).encode("utf-8")

        try:
            req = urllib.request.Request(self._build_url("sendMessage"), data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
                payload = json.loads(content)
                if payload.get("ok"):
                    return {"success": True, "message": "Message Telegram envoyé", "data": payload}
                return {"success": False, "message": payload.get("description", "Erreur Telegram"), "data": payload}

        except urllib.error.HTTPError as e:
            try:
                err = e.read().decode("utf-8")
                payload = json.loads(err)
                return {"success": False, "message": payload.get("description", str(e)), "data": payload}
            except Exception:
                return {"success": False, "message": f"HTTPError Telegram: {e}", "data": {} }
        except Exception as e:
            return {"success": False, "message": f"Erreur de réseau Telegram: {e}"}


_telegram_client = None


def get_telegram_client() -> TelegramAPI:
    global _telegram_client
    if _telegram_client is None:
        _telegram_client = TelegramAPI()
    return _telegram_client
