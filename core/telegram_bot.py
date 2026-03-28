"""
core/telegram_bot.py — Integration Telegram Bot pour Jarvis
========================================================

Permet de controler Jarvis a distance via Telegram.
Fonctionnalites :
  - Recevoir et traiter les commandes Telegram
  - Envoyer des notifications push (emails importants, alertes systeme)
  - Confirmation par bouton inline

Configuration :
  - TELEGRAM_BOT_TOKEN : token du bot (@BotFather)
  - TELEGRAM_CHAT_ID : ton chat ID (@userinfobot)

Usage :
  python -m core.telegram_bot  # Mode daemon
  ou via l'agent Jarvis directement
"""

import json
import time
import threading
import logging
from typing import Optional, Callable
from dataclasses import dataclass
from config.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TelegramMessage:
    """Message Telegram recu."""
    chat_id: str
    message_id: int
    text: str
    sender_id: str
    sender_name: str
    timestamp: int


class TelegramBot:
    """Bot Telegram pour Jarvis."""

    def __init__(self, token: str = None, allowed_chat_id: str = None):
        self._token = token
        self._allowed_chat_id = allowed_chat_id
        self._offset = 0
        self._running = False
        self._command_handler: Optional[Callable] = None
        self._update_thread: Optional[threading.Thread] = None
        self._connected = False

        if token:
            self._connect()
        else:
            logger.warning("TelegramBot: token non configure")

    def _connect(self):
        """Teste la connexion au bot."""
        try:
            import requests
            resp = requests.get(f"https://api.telegram.org/bot{self._token}/getMe", timeout=5)
            if resp.ok and resp.json().get("ok"):
                self._connected = True
                bot_name = resp.json().get("result", {}).get("first_name", "Unknown")
                logger.info(f"TelegramBot connecte : @{bot_name}")
            else:
                logger.error(f"TelegramBot: echec getMe — {resp.text}")
        except ImportError:
            logger.error("TelegramBot: requests non installe (pip install requests)")
        except Exception as e:
            logger.error(f"TelegramBot: erreur connexion — {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected and bool(self._token)

    def set_command_handler(self, handler: Callable[[str], dict]):
        """Definit le handler pour traiter les commandes."""
        self._command_handler = handler

    def _fetch_updates(self) -> list:
        """Recupere les nouveaux messages."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/getUpdates"
            params = {"offset": self._offset, "timeout": 30, "allowed_updates": ["message"]}
            resp = requests.get(url, params=params, timeout=35)
            if resp.ok:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
        except Exception as e:
            logger.error(f"Erreur fetch_updates: {e}")
        return []

    def _parse_message(self, update: dict) -> Optional[TelegramMessage]:
        """Parse un message Telegram."""
        try:
            msg = update.get("message", {})
            if not msg:
                return None

            chat = msg.get("chat", {})
            from_user = msg.get("from", {})

            return TelegramMessage(
                chat_id=str(chat.get("id", "")),
                message_id=msg.get("message_id", 0),
                text=msg.get("text", ""),
                sender_id=str(from_user.get("id", "")),
                sender_name=from_user.get("first_name", "Unknown"),
                timestamp=msg.get("date", 0),
            )
        except Exception as e:
            logger.error(f"Erreur parse_message: {e}")
            return None

    def _is_allowed(self, chat_id: str) -> bool:
        """Verifie si le chat est autorise."""
        if not self._allowed_chat_id:
            return True
        return chat_id == self._allowed_chat_id

    def process_updates(self) -> list[TelegramMessage]:
        """Traitement des messages recus."""
        messages = []
        updates = self._fetch_updates()

        for update in updates:
            msg = self._parse_message(update)
            if not msg:
                continue

            if not self._is_allowed(msg.chat_id):
                self.send_message(msg.chat_id, "Acces refuse. Chat non autorise.")
                self._offset = update.get("update_id", 0) + 1
                continue

            messages.append(msg)
            self._offset = update.get("update_id", 0) + 1

        return messages

    def _poll_loop(self):
        """Boucle de polling principale."""
        logger.info("TelegramBot: demarrage du polling...")
        while self._running:
            try:
                messages = self.process_updates()
                for msg in messages:
                    if msg.text and self._command_handler:
                        try:
                            result = self._command_handler(msg.text)
                            if result and isinstance(result, dict):
                                response = result.get("message", str(result))
                                if response:
                                    self.send_message(msg.chat_id, response)
                            elif result and isinstance(result, str):
                                self.send_message(msg.chat_id, result)
                        except Exception as e:
                            logger.error(f"Erreur traitement commande: {e}")
                            self.send_message(msg.chat_id, f"Erreur: {str(e)}")
            except Exception as e:
                logger.error(f"Erreur poll_loop: {e}")
                time.sleep(5)

    def start_polling(self):
        """Demarre le polling dans un thread."""
        if self._running:
            return
        self._running = True
        self._update_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._update_thread.start()
        logger.info("TelegramBot: polling demarre")

    def stop_polling(self):
        """Arrete le polling."""
        self._running = False
        if self._update_thread:
            self._update_thread.join(timeout=2)
        logger.info("TelegramBot: polling arrete")

    def send_message(self, chat_id: str, text: str, parse_mode: str = None, reply_markup: dict = None) -> dict:
        """Envoie un message Telegram."""
        if not self._connected:
            return {"success": False, "message": "Bot non connecte"}

        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                return {"success": True, "message": "Message envoye"}
            else:
                return {"success": False, "message": resp.text}
        except Exception as e:
            logger.error(f"Erreur send_message: {e}")
            return {"success": False, "message": str(e)}

    def send_notification(self, text: str, chat_id: str = None) -> dict:
        """Envoie une notification. Si chat_id non fourni, utilise allowed_chat_id."""
        target = chat_id or self._allowed_chat_id
        if not target:
            return {"success": False, "message": "Aucun chat_id configure"}
        return self.send_message(target, text)

    def send_photo(self, chat_id: str, photo_url: str, caption: str = None) -> dict:
        """Envoie une photo."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendPhoto"
            payload = {"chat_id": chat_id, "photo": photo_url}
            if caption:
                payload["caption"] = caption
            resp = requests.post(url, json=payload, timeout=15)
            return {"success": resp.ok, "message": resp.text}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def send_document(self, chat_id: str, file_path: str, caption: str = None) -> dict:
        """Envoie un document/fichier."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendDocument"
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption
                resp = requests.post(url, data=data, files=files, timeout=30)
            return {"success": resp.ok, "message": resp.text}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def answer_callback(self, callback_query_id: str, text: str = None, show_alert: bool = False) -> dict:
        """Repond a un callback query (bouton inline)."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/answerCallbackQuery"
            payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
            if text:
                payload["text"] = text
            resp = requests.post(url, json=payload, timeout=5)
            return {"success": resp.ok}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_chat_member_status(self, chat_id: str, user_id: str) -> str:
        """Recupere le statut d'un membre dans un chat."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/getChatMember"
            resp = requests.get(url, params={"chat_id": chat_id, "user_id": user_id}, timeout=5)
            if resp.ok:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", {}).get("status", "unknown")
        except:
            pass
        return "unknown"

    def set_webhook(self, url: str, certificate: str = None) -> dict:
        """Configure un webhook (avance)."""
        try:
            import requests
            url_api = f"https://api.telegram.org/bot{self._token}/setWebhook"
            payload = {"url": url}
            if certificate:
                with open(certificate, "rb") as f:
                    files = {"certificate": f}
                    resp = requests.post(url_api, data=payload, files=files, timeout=15)
            else:
                resp = requests.post(url_api, json=payload, timeout=10)
            return {"success": resp.ok, "message": resp.text}
        except Exception as e:
            return {"success": False, "message": str(e)}


class JarvisTelegramBridge:
    """Pont entre Jarvis et Telegram — envoie des alertes automatiques."""

    def __init__(self, bot: TelegramBot = None):
        self._bot = bot
        self._enabled = bot is not None and bot.is_connected

    def notify_email_important(self, emails: list, source: str = "Outlook") -> dict:
        """Envoie une notification pour emails importants."""
        if not self._enabled:
            return {"success": False, "message": "Telegram non active"}

        if not emails:
            return {"success": False, "message": "Aucun email a notifier"}

        lines = [f"📧 *Emails importants recents*\n"]
        for i, email in enumerate(emails[:5], 1):
            sender = email.get("sender_name", "Inconnu")
            subject = email.get("subject", "(sans objet)")
            date = email.get("date", "")
            lines.append(f"{i}. *{sender}*\n   {subject}\n   {date}\n")

        text = "\n".join(lines)
        return self._bot.send_notification(text, parse_mode="Markdown")

    def notify_system_alert(self, alert_type: str, message: str) -> dict:
        """Envoie une alerte systeme."""
        if not self._enabled:
            return {"success": False, "message": "Telegram non active"}

        icons = {
            "cpu": "💻",
            "ram": "🧠",
            "temperature": "🌡️",
            "error": "❌",
            "warning": "⚠️",
            "success": "✅",
        }
        icon = icons.get(alert_type, "📢")
        text = f"{icon} *Alerte {alert_type.upper()}*\n{message}"
        return self._bot.send_notification(text, parse_mode="Markdown")

    def notify_command_result(self, command: str, result: dict) -> dict:
        """Envoie le resultat d'une commande."""
        if not self._enabled:
            return {"success": False, "message": "Telegram non active"}

        success = result.get("success", False)
        icon = "✅" if success else "❌"
        message = result.get("message", str(result))

        text = f"{icon} *Commande:* `{command}`\n{message}"
        return self._bot.send_notification(text, parse_mode="Markdown")

    def notify_shutdown(self, delay_seconds: int = 0) -> dict:
        """Notifie extinction programmee."""
        if not self._enabled:
            return {"success": False}

        if delay_seconds > 0:
            minutes = delay_seconds // 60
            text = f"⏰ *Extinction programmee*\nLe PC s'eteindra dans {minutes} minute(s)."
        else:
            text = "🛑 *Extinction en cours*"
        return self._bot.send_notification(text, parse_mode="Markdown")


_telegram_bot = None

def get_telegram_bot() -> Optional[TelegramBot]:
    """Retourne le bot Telegram singleton."""
    global _telegram_bot
    if _telegram_bot is None:
        from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        if TELEGRAM_BOT_TOKEN:
            _telegram_bot = TelegramBot(token=TELEGRAM_BOT_TOKEN, allowed_chat_id=TELEGRAM_CHAT_ID)
    return _telegram_bot


def get_jarvis_bridge() -> Optional[JarvisTelegramBridge]:
    """Retourne le pont Jarvis-Telegram singleton."""
    bot = get_telegram_bot()
    if bot and bot.is_connected:
        return JarvisTelegramBridge(bot)
    return None
