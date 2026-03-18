"""
websocket_client.py — Connexion persistante PC ↔ Azure Cloud
Le PC écoute en permanence les commandes venant du cloud.

SEMAINE 6 — MARDI — IMPLÉMENTATION COMPLÈTE

ARCHITECTURE :
  Mode WebSocket (préféré) :
    PC ←──── WebSocket persistant ────→ Azure Function (ws trigger)
    Latence ~10ms, connexion always-on

  Mode HTTP Polling (fallback, utilisé si WebSocket indisponible) :
    PC ──GET /api/poll──→ Azure Function (toutes les N secondes)
    PC ←──commandes────── Azure Function
    PC ──POST /api/result→ Azure Function

  Le client tente WebSocket en premier, bascule sur Polling si échec.

FLUX COMPLET :
  1. WebSocketClient.start()         → démarre dans un thread dédié
  2. _connect_websocket() ou _poll() → écoute les commandes
  3. _dispatch_command(cmd)          → passe à Agent.handle_command()
  4. _send_result(cmd_id, result)    → POST le résultat à Azure
  5. En cas de déconnexion → reconnexion automatique (backoff exponentiel)
"""

import asyncio
import hashlib
import hmac
import json
import queue
import threading
import time
import uuid
from typing import Callable, Optional
from urllib.parse import urljoin
from config.logger import get_logger
from config.settings import (
    AZURE_FUNCTION_URL,
    AZURE_FUNCTION_KEY,
    SECRET_TOKEN,
    DEVICE_ID,
)

logger = get_logger(__name__)

# ── Timeouts et retry ─────────────────────────────────────────────────────────
POLL_INTERVAL_SEC    = 3     # Intervalle de polling HTTP
RECONNECT_DELAY_BASE = 2     # Délai de reconnexion initial (secondes)
RECONNECT_DELAY_MAX  = 60    # Délai maximum de reconnexion
MAX_RETRY_ATTEMPTS   = 0     # 0 = infini
HTTP_TIMEOUT_SEC     = 10    # Timeout par requête HTTP
RESULT_TIMEOUT_SEC   = 30    # Timeout pour envoyer le résultat


class WebSocketClient:
    """
    Client de communication PC ↔ Azure Cloud.

    Supporte deux modes :
      - WebSocket : connexion persistante, latence minimale
      - HTTP Polling : requêtes périodiques, compatible tous environnements

    Usage :
        agent  = Agent()
        client = WebSocketClient(agent)
        client.start()   # non-bloquant, lance un thread dédié

    Pour arrêter proprement :
        client.stop()
    """

    def __init__(self, agent, on_status_change: Optional[Callable] = None):
        """
        Args:
            agent            : instance Agent (handle_command)
            on_status_change : callback(status: str, detail: str) pour l'UI
        """
        self.agent            = agent
        self.on_status_change = on_status_change

        # État de connexion
        self.connected        = False
        self.running          = False
        self.mode             = "unknown"    # "websocket" | "polling"
        self.reconnect_count  = 0
        self.last_error       = ""
        self.commands_received = 0
        self.commands_executed = 0
        self.start_time        = None

        # Threads
        self._thread          = None
        self._loop            = None

        # Queue interne pour les résultats à envoyer
        self._result_queue    = queue.Queue(maxsize=100)
        self._result_thread   = None

        logger.info(
            f"WebSocketClient initialisé — "
            f"URL={AZURE_FUNCTION_URL[:40] + '...' if len(AZURE_FUNCTION_URL) > 40 else AZURE_FUNCTION_URL}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  LANCEMENT / ARRÊT
    # ══════════════════════════════════════════════════════════════════════════

    def start(self) -> bool:
        """
        Lance le client en arrière-plan (thread non-bloquant).

        Returns:
            True si démarré, False si déjà en cours ou config manquante
        """
        if self.running:
            logger.warning("WebSocketClient déjà en cours d'exécution.")
            return False

        from config.settings import AZURE_FUNCTION_URL as _url
        if not _url:
            logger.error(
                "AZURE_FUNCTION_URL non configuré. "
                "Ajoute l'URL de ta Function dans config/.env"
            )
            return False

        self.running    = True
        self.start_time = time.time()

        # Thread principal : connexion et écoute
        self._thread = threading.Thread(
            target=self._run_loop,
            name="jarvis-cloud-listener",
            daemon=True
        )
        self._thread.start()

        # Thread résultats : envoi asynchrone des résultats
        self._result_thread = threading.Thread(
            target=self._result_sender_loop,
            name="jarvis-result-sender",
            daemon=True
        )
        self._result_thread.start()

        logger.info(
            f"WebSocketClient démarré — threads: "
            f"{self._thread.name}, {self._result_thread.name}"
        )
        self._notify_status("starting", "Connexion au cloud en cours...")
        return True

    def stop(self):
        """Arrête proprement le client."""
        logger.info("Arrêt WebSocketClient demandé...")
        self.running   = False
        self.connected = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._notify_status("stopped", "Déconnecté du cloud.")
        logger.info("WebSocketClient arrêté.")

    def get_status(self) -> dict:
        """Retourne l'état actuel de la connexion."""
        uptime = int(time.time() - self.start_time) if self.start_time else 0
        return {
            "connected":          self.connected,
            "running":            self.running,
            "mode":               self.mode,
            "reconnect_count":    self.reconnect_count,
            "commands_received":  self.commands_received,
            "commands_executed":  self.commands_executed,
            "uptime_seconds":     uptime,
            "last_error":         self.last_error,
            "azure_url":          AZURE_FUNCTION_URL[:50] if AZURE_FUNCTION_URL else "",
        }

    # ══════════════════════════════════════════════════════════════════════════
    #  BOUCLE PRINCIPALE
    # ══════════════════════════════════════════════════════════════════════════

    def _run_loop(self):
        """Thread principal : essaie WebSocket, bascule sur Polling si échec."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            logger.error(f"Boucle principale terminée avec erreur : {e}")
        finally:
            self._loop.close()

    async def _main_loop(self):
        """Gère la reconnexion automatique avec backoff exponentiel."""
        delay = RECONNECT_DELAY_BASE

        while self.running:
            try:
                # Essayer WebSocket en premier
                ws_ok = await self._try_websocket()
                if not ws_ok:
                    # Fallback polling HTTP
                    logger.info("WebSocket indisponible → mode HTTP Polling")
                    await self._polling_loop()

                delay = RECONNECT_DELAY_BASE  # Reset délai si succès

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error   = str(e)
                self.connected    = False
                self.reconnect_count += 1

                logger.warning(
                    f"Déconnexion #{self.reconnect_count} : {e}. "
                    f"Reconnexion dans {delay}s..."
                )
                self._notify_status("reconnecting",
                                    f"Reconnexion dans {delay}s (tentative #{self.reconnect_count})")

                if self.running:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_DELAY_MAX)  # Backoff exponentiel

    # ══════════════════════════════════════════════════════════════════════════
    #  MODE WEBSOCKET
    # ══════════════════════════════════════════════════════════════════════════

    async def _try_websocket(self) -> bool:
        """
        Tente d'établir une connexion WebSocket avec Azure.
        Retourne True si la connexion a été établie et maintenue.
        """
        # Construire l'URL WebSocket
        ws_url = AZURE_FUNCTION_URL
        if ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[8:]
        elif ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[7:]

        # Ajouter le path WebSocket si pas déjà présent
        if "/api/ws" not in ws_url:
            ws_url = ws_url.rstrip("/") + "/api/ws"

        try:
            import websockets
            headers = self._build_auth_headers("GET", "ws")
            logger.info(f"Tentative WebSocket : {ws_url}")

            async with websockets.connect(
                ws_url,
                extra_headers=headers,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                self.connected = True
                self.mode      = "websocket"
                self._notify_status("connected", f"Connecté via WebSocket à {ws_url}")
                logger.info("WebSocket connecté ✓")

                # Envoyer un message d'enregistrement
                await ws.send(json.dumps({
                    "type":      "register",
                    "device_id": DEVICE_ID or "jarvis-pc-01",
                    "version":   "1.0.0",
                }))

                # Écouter les messages
                async for message in ws:
                    if not self.running:
                        break
                    await self._handle_ws_message(message, ws)

            return True

        except ImportError:
            logger.debug("websockets non installé → mode polling")
            return False
        except Exception as e:
            logger.debug(f"WebSocket échoué : {e}")
            return False

    async def _handle_ws_message(self, message: str, ws):
        """Traite un message WebSocket entrant."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "command")

            if msg_type == "command":
                await self._dispatch_command(data)
            elif msg_type == "ping":
                await ws.send(json.dumps({"type": "pong"}))
            elif msg_type == "ack":
                logger.debug(f"ACK reçu : {data.get('command_id')}")
            else:
                logger.debug(f"Message WS inconnu : {msg_type}")

        except json.JSONDecodeError:
            logger.warning(f"Message WS non-JSON : {message[:100]}")

    # ══════════════════════════════════════════════════════════════════════════
    #  MODE HTTP POLLING (MERCREDI — flux complet)
    # ══════════════════════════════════════════════════════════════════════════

    async def _polling_loop(self):
        """
        Boucle de polling HTTP : interroge Azure toutes les N secondes.
        Mode robuste qui fonctionne même sans WebSocket natif.
        """
        self.mode = "polling"
        logger.info(f"Démarrage HTTP Polling (intervalle={POLL_INTERVAL_SEC}s)")

        # Vérifier d'abord que Azure répond
        healthy = await self._check_health()
        if not healthy:
            raise ConnectionError(
                f"Azure Function inaccessible : {AZURE_FUNCTION_URL}. "
                "Vérifie AZURE_FUNCTION_URL dans .env"
            )

        self.connected = True
        self._notify_status("connected", f"Connecté via HTTP Polling à {AZURE_FUNCTION_URL}")
        logger.info("HTTP Polling actif ✓")

        while self.running and self.connected:
            try:
                # Récupérer les commandes en attente
                commands = await self._fetch_pending_commands()

                for cmd in commands:
                    self.commands_received += 1
                    logger.info(
                        f"[{self.commands_received}] Commande reçue : "
                        f"'{cmd.get('command', '')[:50]}' (id={cmd.get('command_id', '')[:8]})"
                    )
                    # Exécuter dans un thread pour ne pas bloquer la boucle
                    threading.Thread(
                        target=self._execute_and_report,
                        args=(cmd,),
                        daemon=True
                    ).start()

                if commands:
                    # Petit délai après réception pour laisser le temps d'exécuter
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(POLL_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Erreur polling : {e}")
                self.connected  = False
                raise

    def _execute_and_report(self, cmd: dict):
        """
        Exécute une commande et envoie le résultat à Azure.
        Tourne dans son propre thread pour ne pas bloquer le polling.
        """
        command_id = cmd.get("command_id", str(uuid.uuid4()))
        command    = cmd.get("command", "")

        try:
            logger.info(f"Exécution : '{command}'")
            result = self.agent.handle_command(command)
            self.commands_executed += 1

            # Enrichir le résultat
            result["command_id"]   = command_id
            result["executed_at"]  = int(time.time())
            result["device_id"]    = DEVICE_ID or "jarvis-pc-01"

            logger.info(
                f"Résultat : success={result.get('success')} | "
                f"{result.get('message', '')[:60]}"
            )

            # Mettre en queue pour envoi asynchrone
            try:
                self._result_queue.put_nowait({
                    "command_id": command_id,
                    "result":     result,
                })
            except queue.Full:
                logger.warning("Queue résultats pleine, résultat perdu")

        except Exception as e:
            logger.error(f"Erreur exécution commande : {e}", exc_info=True)
            try:
                self._result_queue.put_nowait({
                    "command_id": command_id,
                    "result": {
                        "success":     False,
                        "message":     f"Erreur d'exécution : {str(e)}",
                        "data":        None,
                        "command_id":  command_id,
                        "executed_at": int(time.time()),
                    }
                })
            except queue.Full:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  ENVOI DES RÉSULTATS (thread dédié)
    # ══════════════════════════════════════════════════════════════════════════

    def _result_sender_loop(self):
        """Thread dédié à l'envoi des résultats vers Azure."""
        logger.info("Thread result-sender démarré")
        while self.running:
            try:
                item = self._result_queue.get(timeout=1.0)
                command_id = item["command_id"]
                result     = item["result"]

                success = self._send_result_http(command_id, result)
                if success:
                    logger.info(f"Résultat envoyé : {command_id[:8]}")
                else:
                    logger.warning(f"Échec envoi résultat : {command_id[:8]} — sera retenté")
                    # Remettre en queue pour retry
                    try:
                        self._result_queue.put_nowait(item)
                    except queue.Full:
                        pass

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur result-sender : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  HTTP HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    async def _check_health(self) -> bool:
        """Vérifie que l'Azure Function répond."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._http_get("/health")
            )
            healthy = result.get("status") == "healthy"
            if healthy:
                logger.info(
                    f"Azure Function OK — version={result.get('version', '?')}"
                )
            else:
                logger.warning(f"Health check inattendu : {result}")
            return healthy
        except Exception as e:
            logger.error(f"Health check échoué : {e}")
            return False

    async def _fetch_pending_commands(self) -> list:
        """Appel HTTP GET /api/poll pour récupérer les commandes en attente."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self._http_get("/poll")),
                timeout=HTTP_TIMEOUT_SEC
            )
            return result.get("commands", [])
        except asyncio.TimeoutError:
            logger.warning("Poll timeout")
            return []
        except Exception as e:
            logger.error(f"Erreur fetch_pending : {e}")
            raise

    def _send_result_http(self, command_id: str, result: dict) -> bool:
        """Envoie le résultat d'une commande via HTTP POST /api/result."""
        try:
            payload = {
                "command_id":  command_id,
                "success":     result.get("success", False),
                "message":     result.get("message", ""),
                "data":        result.get("data"),
                "executed_at": result.get("executed_at", int(time.time())),
                "device_id":   DEVICE_ID or "jarvis-pc-01",
            }
            response = self._http_post("/result", payload)
            return response.get("status") == "stored"
        except Exception as e:
            logger.error(f"Erreur envoi résultat : {e}")
            return False

    async def _dispatch_command(self, cmd: dict):
        """Dispatch asynchrone d'une commande vers l'agent."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._execute_and_report(cmd)
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  COUCHE HTTP BAS NIVEAU
    # ══════════════════════════════════════════════════════════════════════════

    def _http_get(self, path: str) -> dict:
        """GET vers l'Azure Function avec authentification."""
        return self._http_request("GET", path)

    def _http_post(self, path: str, body: dict) -> dict:
        """POST vers l'Azure Function avec authentification."""
        return self._http_request("POST", path, body)

    def _http_request(self, method: str, path: str, body: dict = None) -> dict:
        """
        Requête HTTP vers Azure Function.
        Utilise requests si disponible, sinon urllib stdlib.
        """
        from config.settings import AZURE_FUNCTION_URL as _fn_url
        url = _fn_url.rstrip("/") + "/api" + path

        # Ajouter la clé Azure Function si présente
        if AZURE_FUNCTION_KEY:
            sep = "&" if "?" in url else "?"
            url += f"{sep}code={AZURE_FUNCTION_KEY}"

        headers = self._build_auth_headers(method, path.lstrip("/"), body)
        headers["Content-Type"] = "application/json"

        body_bytes = json.dumps(body).encode("utf-8") if body else b""

        try:
            import requests as req_lib
            if method == "GET":
                resp = req_lib.get(url, headers=headers, timeout=HTTP_TIMEOUT_SEC)
            else:
                resp = req_lib.post(url, headers=headers, data=body_bytes,
                                    timeout=HTTP_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except ImportError:
            # Fallback urllib
            import urllib.request
            request = urllib.request.Request(
                url, data=body_bytes if method == "POST" else None,
                headers=headers, method=method
            )
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as r:
                return json.loads(r.read().decode("utf-8"))

    # ══════════════════════════════════════════════════════════════════════════
    #  AUTHENTIFICATION — VENDREDI
    # ══════════════════════════════════════════════════════════════════════════

    def _build_auth_headers(self, method: str, path: str,
                             body: dict = None) -> dict:
        """
        Construit les headers d'authentification HMAC-SHA256.

        Header X-Jarvis-Token     : token simple (dev)
        Header X-Jarvis-Signature : HMAC(secret, method+path+timestamp+body_hash)
        Header X-Jarvis-Timestamp : timestamp Unix (anti-replay)
        Header X-Jarvis-Device    : identifiant du device
        """
        timestamp = str(int(time.time()))
        body_bytes = json.dumps(body).encode("utf-8") if body else b""
        body_hash  = hashlib.sha256(body_bytes).hexdigest()

        # Message à signer : method:path:timestamp:body_hash
        message    = f"{method.upper()}:{path}:{timestamp}:{body_hash}"
        signature  = hmac.new(
            SECRET_TOKEN.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return {
            "X-Jarvis-Token":     SECRET_TOKEN,       # Simple (dev)
            "X-Jarvis-Signature": signature,           # HMAC-SHA256 (prod)
            "X-Jarvis-Timestamp": timestamp,
            "X-Jarvis-Device":    DEVICE_ID or "jarvis-pc-01",
        }

    def _notify_status(self, status: str, detail: str = ""):
        """Notifie l'UI d'un changement de statut."""
        icons = {
            "connected":    "🟢",
            "reconnecting": "🟡",
            "stopped":      "🔴",
            "starting":     "🔵",
        }
        icon = icons.get(status, "⚪")
        logger.info(f"{icon} WebSocketClient : {status} — {detail}")
        if self.on_status_change:
            try:
                self.on_status_change(status, detail)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP POLLING CLIENT — Classe simplifiée pour les tests et le mode standalone
# ══════════════════════════════════════════════════════════════════════════════

class HttpPollingClient:
    """
    Client HTTP Polling simple — sans asyncio, idéal pour les tests.
    Peut être utilisé directement depuis main.py en mode synchrone.
    """

    def __init__(self, agent, poll_interval: int = POLL_INTERVAL_SEC):
        self.agent         = agent
        self.poll_interval = poll_interval
        self.running       = False
        self._thread       = None
        self._ws_client    = WebSocketClient(agent)

    def start(self) -> bool:
        """Lance le polling dans un thread daemon."""
        if not AZURE_FUNCTION_URL:
            logger.warning(
                "AZURE_FUNCTION_URL non configuré — "
                "mode cloud désactivé. Jarvis fonctionne en local uniquement."
            )
            return False

        self.running = True
        self._thread = threading.Thread(
            target=self._ws_client.start,
            daemon=True,
            name="jarvis-http-polling"
        )
        self._ws_client.start()
        logger.info("HttpPollingClient démarré via WebSocketClient")
        return True

    def stop(self):
        self.running = False
        self._ws_client.stop()

    def get_status(self) -> dict:
        return self._ws_client.get_status()