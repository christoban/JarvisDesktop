"""
core/telegram_bot.py — Telegram bot pour Jarvis
Reçoit les commandes via Telegram et les transmet à l'agent.

Long-polling : Telegram peut mettre jusqu'à `timeout` secondes avant de
répondre à une requête getUpdates si aucun message n'est arrivé. C'est un
comportement attendu (ce n'est PAS une erreur) que ce module gère
silencieusement.
"""

import threading
import time

import requests

from config.logger import get_logger

logger = get_logger("core.telegram_bot")

# Délai (secondes) entre deux tentatives après une vraie erreur réseau.
_RETRY_DELAY = 5

# Durée du long-polling (secondes). Telegram peut attendre autant de temps
# avant de répondre si aucun message n'arrive — ce n'est PAS un timeout.
_POLL_TIMEOUT = 30

# Timeout de la requête HTTP : un peu plus long que le long-polling pour
# laisser Telegram répondre même en fin de fenêtre.
_REQUEST_TIMEOUT = _POLL_TIMEOUT + 5


class TelegramBot:
    """
    Bot Telegram léger qui relaie les messages vers l'agent Jarvis.

    Usage ::

        bot = TelegramBot(token="YOUR_BOT_TOKEN", agent=agent)
        bot.start()   # démarre dans un thread daemon
        ...
        bot.stop()
    """

    def __init__(self, token: str, agent):
        self.token = token
        self.agent = agent
        self.running = False
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._base_url = f"https://api.telegram.org/bot{token}"

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Démarre le bot dans un thread daemon (non-bloquant)."""
        if self._thread and self._thread.is_alive():
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="TelegramBot")
        self._thread.start()
        logger.info("TelegramBot démarré.")

    def stop(self) -> None:
        """Arrête la boucle de polling."""
        self.running = False
        logger.info("TelegramBot arrêté.")

    # ── Boucle interne ────────────────────────────────────────────────────────

    def _run(self) -> None:
        while self.running:
            try:
                self.fetch_updates()
            except Exception as exc:
                # Erreur inattendue dans la boucle principale — on attend avant
                # de réessayer pour éviter un flood de logs.
                logger.warning(f"TelegramBot erreur interne: {exc}")
                if self.running:
                    time.sleep(_RETRY_DELAY)

    def fetch_updates(self) -> None:
        """
        Récupère les nouveaux messages via long-polling.

        Un ReadTimeout signifie simplement qu'aucun message n'est arrivé
        pendant la fenêtre de polling — c'est le comportement normal de
        Telegram et il est ignoré silencieusement.
        """
        try:
            response = requests.get(
                f"{self._base_url}/getUpdates",
                params={"offset": self._offset, "timeout": _POLL_TIMEOUT},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)

        except requests.exceptions.ReadTimeout:
            # Aucun message pendant la fenêtre de polling → comportement normal.
            pass

        except requests.exceptions.ConnectionError as exc:
            logger.warning(f"TelegramBot connexion impossible: {exc}")
            time.sleep(_RETRY_DELAY)

        except requests.exceptions.HTTPError as exc:
            logger.warning(f"TelegramBot erreur HTTP: {exc}")
            time.sleep(_RETRY_DELAY)

        except requests.exceptions.RequestException as exc:
            logger.warning(f"TelegramBot erreur réseau: {exc}")
            time.sleep(_RETRY_DELAY)

    # ── Traitement des mises à jour ───────────────────────────────────────────

    def _handle_update(self, update: dict) -> None:
        message = (
            update.get("message")
            or update.get("edited_message")
            or {}
        )
        text = (message.get("text") or "").strip()
        chat_id = message.get("chat", {}).get("id")

        if not text or not chat_id:
            return

        logger.info(f"TelegramBot message de chat {chat_id}: '{text}'")
        try:
            result = self.agent.handle_command(text, source="telegram")
            reply = result.get("message") or "Commande exécutée."
            self._send_message(chat_id, reply)
        except Exception as exc:
            logger.error(f"TelegramBot erreur traitement commande: {exc}")
            self._send_message(chat_id, "❌ Erreur lors du traitement de ta commande.")

    def _send_message(self, chat_id: int, text: str) -> None:
        try:
            requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as exc:
            logger.warning(f"TelegramBot impossible d'envoyer le message: {exc}")
