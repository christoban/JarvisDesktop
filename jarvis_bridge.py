"""
jarvis_bridge.py — Pont Local WiFi : Téléphone ↔ PC Agent + TTS
SEMAINE 7+8 — Communication directe sur le réseau local

Routes :
  POST /api/command          ← Commande texte (Semaines 7)
    POST /api/voice            ← Audio Azure Speech → transcription → exécution (Semaine 8)
  GET  /api/result/<id>      ← Polling résultat
  GET  /api/health           ← Statut PC + TTS + IA
  OPTIONS *                  ← CORS preflight (Expo dev)

LANCER :
    cd JarvisDesktop
    python jarvis_bridge.py

Puis dans api.service.ts :
    const BASE_URL = 'http://<IP_PC>:7071';
"""

import base64
import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── Imports projet ────────────────────────────────────────────────────────────
try:
    from config.settings import (
        SECRET_TOKEN, OPENAI_API_KEY,
        AZURE_SPEECH_KEY, AZURE_SPEECH_REGION,
        OPENAI_TTS_MODEL, OPENAI_TTS_VOICE,
    )
except ImportError:
    SECRET_TOKEN         = "menedona_2005_christoban_2026"
    OPENAI_API_KEY       = ""
    AZURE_SPEECH_KEY     = ""
    AZURE_SPEECH_REGION  = "uaenorth"
    OPENAI_TTS_MODEL     = "tts-1"
    OPENAI_TTS_VOICE     = "alloy"

try:
    from core.agent import Agent
    AGENT_AVAILABLE = True
except ImportError as e:
    print(f"⚠  Import agent : {e}")
    AGENT_AVAILABLE = False

try:
    from voice.tts_engine import TTSEngine
    TTS_AVAILABLE = True
except ImportError as e:
    print(f"⚠  Import TTS : {e}")
    TTS_AVAILABLE = False

# Azure Speech SDK
try:
    import azure.cognitiveservices.speech as speechsdk
    _AZURE_SPEECH_SDK = True
except ImportError:
    _AZURE_SPEECH_SDK = False

def _resolve_ffmpeg_exe() -> str:
    """
    Resolve ffmpeg executable path.
    Priority:
      1) imageio-ffmpeg bundled binary
      2) system ffmpeg from PATH
    """
    try:
        mod = importlib.import_module("imageio_ffmpeg")
        return mod.get_ffmpeg_exe()
    except Exception:
        pass

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    return ""


def _clean_env_value(value: str) -> str:
    if value is None:
        return ""
    return str(value).strip().strip('"').strip("'")


def _validate_azure_speech_config() -> tuple[bool, str, str, str]:
    key = _clean_env_value(AZURE_SPEECH_KEY)
    region = _clean_env_value(AZURE_SPEECH_REGION).lower()

    if not key or key.startswith("VOTRE"):
        return False, "AZURE_SPEECH_KEY non configuree", key, region
    if not region:
        return False, "AZURE_SPEECH_REGION non configuree", key, region

    # Accept classic 32-char keys and longer Azure-issued keys.
    # We only enforce alphanumeric characters and reasonable length bounds.
    if not re.fullmatch(r"[A-Za-z0-9]{32,128}", key):
        return False, (
            "AZURE_SPEECH_KEY invalide (attendu: cle alphanumerique entre 32 et 128 caracteres)"
        ), key, region

    # Region should be a plain identifier (e.g. eastus, francecentral, uaenorth)
    if not re.fullmatch(r"[a-z0-9]+", region):
        return False, "AZURE_SPEECH_REGION invalide (exemple: eastus, francecentral, uaenorth)", key, region

    return True, "", key, region


def _convert_to_wav_for_azure(audio_bytes: bytes, fmt: str) -> tuple[bool, str, str]:
    """
    Convert input audio to PCM WAV 16k mono for Azure STT.
    Returns: (ok, wav_path, error)
    """
    fmt = (fmt or "m4a").lower().strip().lstrip(".")
    in_suffix = f".{fmt or 'm4a'}"

    with tempfile.NamedTemporaryFile(delete=False, suffix=in_suffix) as in_file:
        in_file.write(audio_bytes)
        in_path = in_file.name

    out_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    out_path = out_file.name
    out_file.close()

    try:
        ffmpeg_exe = _resolve_ffmpeg_exe()
        if not ffmpeg_exe:
            return False, "", "ffmpeg introuvable (installe imageio-ffmpeg ou ffmpeg systeme)"
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", in_path,
            "-ac", "1",
            "-ar", "16000",
            "-f", "wav",
            out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if "No such file or directory" in stderr and "ffmpeg" in stderr.lower():
                return False, "", "ffmpeg introuvable (installe imageio-ffmpeg ou ffmpeg systeme)"
            return False, "", f"Conversion audio vers wav echouee: {stderr[:300]}"

        return True, out_path, ""
    finally:
        try:
            os.unlink(in_path)
        except Exception:
            pass

PORT = 7071

# ── Singletons partagés ───────────────────────────────────────────────────────
_agent:      "Agent"     = None
_tts:        "TTSEngine" = None
_agent_lock  = threading.Lock()
_results:    dict        = {}
_store_lock  = threading.Lock()
_notifications: list     = []
_notifications_lock       = threading.Lock()

_auth_singleton = None
_perms_singleton = None
_crypto_singleton = None
_security_lock = threading.Lock()
_SECURITY_AVAILABLE = True

try:
    from security.auth import Auth, MODE_HMAC
    from security.permissions import Permissions, LEVEL_DANGER
    from security.crypto import MessageCrypto
except Exception as _security_import_error:
    _SECURITY_AVAILABLE = False
    Auth = None
    MODE_HMAC = "hmac"
    Permissions = None
    LEVEL_DANGER = 3
    MessageCrypto = None
    print(f"⚠  Import security : {_security_import_error} — mode dev sans securite avancee")

MAX_NOTIFICATIONS = 200
RESULT_TTL_SECONDS = int(os.getenv("JARVIS_RESULT_TTL_SECONDS", "1800"))
MAX_STORED_RESULTS = int(os.getenv("JARVIS_MAX_STORED_RESULTS", "1000"))
BATTERY_CHECK_INTERVAL_SEC = 300
BATTERY_LOW_THRESHOLD = int(os.getenv("JARVIS_BATTERY_NOTIFY_THRESHOLD", "20"))
NOTIFY_ON_SUCCESS = os.getenv("JARVIS_NOTIFY_ON_SUCCESS", "1").lower() not in {"0", "false", "no"}

_battery_monitor_started = False
_battery_monitor_lock = threading.Lock()
_last_battery_notif_at = 0
_last_battery_level = None

def get_agent() -> "Agent":
    global _agent
    with _agent_lock:
        if _agent is None and AGENT_AVAILABLE:
            _agent = Agent()
    return _agent

def get_tts() -> "TTSEngine":
    global _tts
    with _agent_lock:
        if _tts is None and TTS_AVAILABLE:
            _tts = TTSEngine()
    return _tts


def get_auth():
    global _auth_singleton
    with _security_lock:
        if _auth_singleton is None and _SECURITY_AVAILABLE and Auth is not None:
            _auth_singleton = Auth(mode=MODE_HMAC)
    return _auth_singleton


def get_perms():
    global _perms_singleton
    with _security_lock:
        if _perms_singleton is None and _SECURITY_AVAILABLE and Permissions is not None:
            _perms_singleton = Permissions()
    return _perms_singleton


def get_crypto():
    global _crypto_singleton
    with _security_lock:
        if _crypto_singleton is None and _SECURITY_AVAILABLE and MessageCrypto is not None:
            _crypto_singleton = MessageCrypto()
    return _crypto_singleton


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _notification_payload(title: str, body: str, notif_type: str = "info",
                          data: dict = None, priority: str = "normal") -> dict:
    return {
        "id": str(uuid.uuid4())[:12],
        "title": title,
        "body": body,
        "type": notif_type,
        "priority": priority,
        "timestamp": int(time.time()),
        "data": data or {},
    }


def _notify_clients(payload: dict) -> dict:
    """
    Diffusion notifications vers clients mobiles.
    Actuellement: stockage pour polling (/api/notifications).
    """
    if not isinstance(payload, dict):
        return {"success": False, "message": "Payload invalide", "data": None}

    payload = dict(payload)
    payload.setdefault("id", str(uuid.uuid4())[:12])
    payload.setdefault("timestamp", int(time.time()))
    payload.setdefault("type", "info")
    payload.setdefault("priority", "normal")
    payload.setdefault("data", {})

    with _notifications_lock:
        _notifications.append(payload)
        if len(_notifications) > MAX_NOTIFICATIONS:
            del _notifications[: len(_notifications) - MAX_NOTIFICATIONS]
        queued = len(_notifications)

    return {"success": True, "message": "Notification queuee.", "data": {"queued": queued}}


def _drain_notifications(limit: int = 50) -> list:
    with _notifications_lock:
        take = max(1, min(limit, len(_notifications)))
        items = _notifications[:take]
        del _notifications[:take]
    return items


def _queued_notifications_count() -> int:
    with _notifications_lock:
        return len(_notifications)


def _prune_results_locked(now: int | None = None):
    """Prune expired/old command results while holding _store_lock."""
    if now is None:
        now = int(time.time())

    if RESULT_TTL_SECONDS > 0:
        expired = [
            key for key, entry in _results.items()
            if now - int((entry or {}).get("executed_at", now)) > RESULT_TTL_SECONDS
        ]
        for key in expired:
            _results.pop(key, None)

    if MAX_STORED_RESULTS > 0 and len(_results) > MAX_STORED_RESULTS:
        ordered = sorted(
            _results.items(),
            key=lambda kv: int((kv[1] or {}).get("executed_at", 0)),
        )
        to_remove = len(_results) - MAX_STORED_RESULTS
        for key, _ in ordered[:to_remove]:
            _results.pop(key, None)


def _store_result(cmd_id: str, result: dict, duration_ms: int | None = None):
    payload = {
        "result": result,
        "executed_at": int(time.time()),
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms

    with _store_lock:
        _prune_results_locked()
        _results[cmd_id] = payload


def _start_battery_monitor_once():
    global _battery_monitor_started
    with _battery_monitor_lock:
        if _battery_monitor_started:
            return
        _battery_monitor_started = True
        threading.Thread(target=_battery_monitor_loop, daemon=True, name="jarvis-battery-monitor").start()


def _battery_monitor_loop():
    global _last_battery_notif_at, _last_battery_level

    try:
        import psutil
    except ImportError:
        print("  ⚠  psutil absent: surveillance batterie desactivee")
        return

    print(f"  🔋 Surveillance batterie active (seuil {BATTERY_LOW_THRESHOLD}%, toutes les 5 min)")

    while True:
        try:
            battery = psutil.sensors_battery()
            if battery is not None:
                level = int(battery.percent)
                charging = bool(battery.power_plugged)
                now = int(time.time())

                if not charging and level < BATTERY_LOW_THRESHOLD:
                    too_old = (now - _last_battery_notif_at) > 1800
                    big_drop = (_last_battery_level is None) or ((_last_battery_level - level) >= 5)
                    if too_old or big_drop:
                        payload = _notification_payload(
                            "Batterie faible",
                            f"Batterie PC a {level}%",
                            notif_type="battery_low",
                            priority="high",
                            data={"battery_level": level},
                        )
                        _notify_clients(payload)
                        _last_battery_notif_at = now
                        _last_battery_level = level
                elif charging:
                    _last_battery_level = level
        except Exception:
            pass

        # Ping pour garder le tunnel actif
        try:
            import urllib.request
            urllib.request.urlopen(f"http://localhost:{PORT}/api/health", timeout=2)
        except Exception:
            pass

        time.sleep(BATTERY_CHECK_INTERVAL_SEC)


# ── Transcription Azure Speech ────────────────────────────────────────────────
def transcribe_audio(audio_bytes: bytes, fmt: str = "m4a") -> dict:
    """
    Transcrit un fichier audio via Azure Speech-to-Text.
    
    Args:
        audio_bytes: bytes bruts du fichier audio
        fmt:         format ("m4a", "wav", "webm", "mp4")
    
    Returns:
        {"success": bool, "text": str, "error": str}
    """
    if not _AZURE_SPEECH_SDK:
        return {
            "success": False,
            "text": "",
            "error": "azure-cognitiveservices-speech absent — pip install azure-cognitiveservices-speech",
        }

    cfg_ok, cfg_err, speech_key, speech_region = _validate_azure_speech_config()
    if not cfg_ok:
        return {"success": False, "text": "", "error": cfg_err}

    # Azure SDK is most reliable with PCM WAV; convert incoming mobile formats first.
    conv_ok, wav_path, conv_err = _convert_to_wav_for_azure(audio_bytes, fmt)
    if not conv_ok:
        return {"success": False, "text": "", "error": conv_err}

    try:
        speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
        speech_config.speech_recognition_language = "fr-FR"
        audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

        result = recognizer.recognize_once_async().get()
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = (result.text or "").strip()
            return {"success": bool(text), "text": text, "error": "" if text else "Reconnaissance vide"}

        if result.reason == speechsdk.ResultReason.NoMatch:
            return {"success": False, "text": "", "error": "Aucune parole reconnue"}

        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            err = details.error_details or str(details.reason)
            return {"success": False, "text": "", "error": f"Azure Speech annule: {err}"}

        return {"success": False, "text": "", "error": f"Azure Speech reason inattendue: {result.reason}"}

    except Exception as e:
        return {"success": False, "text": "", "error": str(e)}
    finally:
        try:
            if wav_path:
                os.unlink(wav_path)
        except Exception:
            pass


# ── Exécution commande ────────────────────────────────────────────────────────
def _execute(cmd_id: str, command: str, speak: bool = False, device_id: str = "unknown"):
    """
    Exécute une commande via l'agent, stocke le résultat,
    et optionnellement prononce la réponse (TTS).
    """
    start = time.time()
    try:
        agent   = get_agent()
        if agent is None:
            raise RuntimeError("Agent indisponible")

        if _SECURITY_AVAILABLE:
            auth = get_auth()
            perms = get_perms()
            if auth is not None and perms is not None:
                # Verification permission rapide sans appel Groq supplementaire.
                # handle_command() fera ensuite un parse complet avec contexte.
                parsed = agent.parser._fallback_keywords(command)
                intent = parsed.get("intent", "UNKNOWN")
                level = auth.get_device_level(device_id)

                if not perms.is_allowed(intent, level):
                    denied_result = {
                        "success": False,
                        "message": "Permission refusee",
                        "data": {
                            "intent": intent,
                            "device_id": device_id,
                            "device_level": level,
                        },
                    }
                    _store_result(cmd_id, denied_result, int((time.time() - start) * 1000))
                    _notify_clients(_notification_payload(
                        "Permission refusee",
                        f"{command} — niveau insuffisant.",
                        notif_type="error",
                        priority="high",
                        data={"command": command, "device_id": device_id, "intent": intent},
                    ))
                    return

                if perms.requires_confirmation(intent):
                    confirm_req = perms.create_confirmation(intent, parsed.get("params", {}) or {}, command)
                    _store_result(
                        cmd_id,
                        {
                            "success": False,
                            "awaiting_confirm": True,
                            "confirm_id": confirm_req.id,
                            "message": confirm_req.to_dict().get("message", "Confirmation requise"),
                        },
                    )

                    _notify_clients(_notification_payload(
                        "Confirmation requise",
                        confirm_req.to_dict().get("message", "Confirmer action dangereuse"),
                        notif_type="info",
                        priority="high",
                        data={"confirm_id": confirm_req.id, "command": command, "intent": intent},
                    ))

                    confirmed = confirm_req.wait(30)
                    if not confirmed:
                        _store_result(
                            cmd_id,
                            {
                                "success": False,
                                "message": "Action annulee",
                                "awaiting_confirm": False,
                                "confirm_id": confirm_req.id,
                            },
                            int((time.time() - start) * 1000),
                        )
                        return

        result  = agent.handle_command(command)
        success = result.get("success", False)
        message = result.get("message", "")
        data_field = result.get("data") or {}
        is_clarification = isinstance(data_field, dict) and bool(
            data_field.get("awaiting_choice") or data_field.get("incomplete")
        )
        elapsed = int((time.time() - start) * 1000)
        icon    = "✅" if success else ("ℹ" if is_clarification else "❌")
        print(f"  {icon} [{elapsed}ms] {message[:70]}")

        # Injection display si présent — fix réponse complète sur mobile
        if isinstance(data_field, dict) and data_field.get("display"):
            result = dict(result)
            result["message"] = result.get("message", "") + "\n\n" + data_field["display"]
            message = result["message"]

        # TTS — le PC prononce la réponse
        if speak:
            tts = get_tts()
            if tts:
                tts.speak_result(result, command)

        _store_result(cmd_id, result, elapsed)

        if success:
            if NOTIFY_ON_SUCCESS and not speak:
                _notify_clients(_notification_payload(
                    "Tache terminee",
                    f"{command} — commande executee.",
                    notif_type="task_done",
                    data={"command": command, "duration_ms": elapsed},
                ))
        elif is_clarification:
            _notify_clients(_notification_payload(
                "Precisions requises",
                message or "Jarvis attend une precision pour continuer.",
                notif_type="info",
                data={"command": command},
            ))
        else:
            _notify_clients(_notification_payload(
                "Erreur execution",
                message or "La commande a echoue.",
                notif_type="error",
                priority="high",
                data={"command": command},
            ))

    except Exception as e:
        print(f"  ❌ Erreur agent : {e}")
        _store_result(cmd_id, {"success": False, "message": str(e)})
        _notify_clients(_notification_payload(
            "Erreur critique Jarvis",
            str(e),
            notif_type="error",
            priority="high",
            data={"command": command},
        ))


# ── Handler HTTP ──────────────────────────────────────────────────────────────
class BridgeHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {self.command:7} {self.path:<35} → {args[1] if len(args)>1 else '?'}")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _json(self, data: dict, code: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type",                "application/json")
        self.send_header("Content-Length",              str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _auth(self, body: bytes = b"", method: str = "GET", path: str = "/") -> dict:
        if not _SECURITY_AVAILABLE:
            ok = self.headers.get("X-Jarvis-Token", "") == SECRET_TOKEN
            return {
                "ok": ok,
                "device_id": self.headers.get("X-Device-Id", "unknown"),
                "reason": "simple_token" if ok else "Token invalide",
            }

        auth = get_auth()
        if auth is None:
            ok = self.headers.get("X-Jarvis-Token", "") == SECRET_TOKEN
            return {
                "ok": ok,
                "device_id": self.headers.get("X-Device-Id", "unknown"),
                "reason": "fallback_simple" if ok else "Token invalide",
            }

        return auth.verify_request(self.headers, body, method, path)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Jarvis-Token, X-Device-Id, X-Timestamp, X-Audio-Format")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Health
        if path == "/api/health":
            agent   = get_agent()
            tts     = get_tts()
            auth    = get_auth()
            crypto  = get_crypto()
            ai_ok   = (agent is not None and
                       getattr(getattr(agent, "parser", None), "ai_available", False))
            tts_ok  = tts is not None and tts.backend != "silent"
            return self._json({
                "status":       "healthy",
                "pc_connected": True,
                "agent_ready":  agent is not None,
                "ai_available": ai_ok,
                "tts_available": tts_ok,
                "tts_backend":  getattr(tts, "backend", "none") if tts else "none",
                "stt_provider": "azure_speech",
                "whisper_ready": _AZURE_SPEECH_SDK and bool(AZURE_SPEECH_KEY) and bool(AZURE_SPEECH_REGION),
                "security_available": _SECURITY_AVAILABLE,
                "security_mode": getattr(auth, "mode", "none") if auth is not None else "none",
                "crypto_available": bool(getattr(crypto, "available", False)),
                "notif_queue":  _queued_notifications_count(),
                "timestamp":    int(time.time()),
                "local_ip":     get_local_ip(),
                "port":         PORT,
            })

        # Notifications polling
        if path == "/api/notifications":
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)

            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50

            notifications = _drain_notifications(limit=limit)
            return self._json({
                "status": "ok",
                "notifications": notifications,
                "count": len(notifications),
                "remaining": _queued_notifications_count(),
            })

        if path == "/api/pending":
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)

            perms = get_perms()
            pending = perms.get_pending() if perms is not None else []
            return self._json({"pending": pending, "count": len(pending)})

        if path == "/api/devices":
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)

            auth = get_auth()
            devices = auth.list_devices() if auth is not None else []
            return self._json({"devices": devices, "count": len(devices)})

        # Résultat polling
        if path.startswith("/api/result/"):
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)
            cmd_id = path.split("/api/result/")[-1]
            with _store_lock:
                _prune_results_locked()
                entry = _results.get(cmd_id)
            if entry is None:
                return self._json({"status": "pending", "command_id": cmd_id}, 404)
            return self._json({"status": "done", "command_id": cmd_id, **entry})

        return self._json({"error": f"Route inconnue: {path}"}, 404)

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        body = self._body()
        path = self.path.split("?")[0]

        # ── Notification bridge (PC -> mobile) ──────────────────────────────
        if path == "/api/notify":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)

            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "JSON invalide"}, 400)

            title = str(data.get("title", "")).strip()
            message_body = str(data.get("body", "")).strip()
            if not title or not message_body:
                return self._json({"error": "title/body requis"}, 400)

            payload = {
                "id": data.get("id") or str(uuid.uuid4())[:12],
                "title": title,
                "body": message_body,
                "type": data.get("type", "info"),
                "priority": data.get("priority", "normal"),
                "timestamp": int(data.get("timestamp", time.time())),
                "data": data.get("data", {}) if isinstance(data.get("data", {}), dict) else {},
            }
            result = _notify_clients(payload)
            return self._json({"status": "queued", **result})

        # ── Commande texte ────────────────────────────────────────────────────
        if path == "/api/command":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return self._json({"error": "JSON invalide"}, 400)

            command   = data.get("command", "").strip()
            device_id = auth_result.get("device_id") or data.get("device_id", "inconnu")
            speak     = data.get("speak", False)   # true = TTS activé

            if not command:
                return self._json({"error": "Commande vide"}, 400)

            cmd_id = str(uuid.uuid4())[:12]
            print(f"\n  📱 [{device_id}] \"{command}\"  (id={cmd_id[:8]}, tts={'oui' if speak else 'non'})")

            threading.Thread(
                target=_execute, args=(cmd_id, command, speak, device_id), daemon=True
            ).start()

            return self._json({"id": cmd_id, "status": "pending"}, 202)

        if path == "/api/confirm":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)

            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "JSON invalide"}, 400)

            confirm_id = str(data.get("id", "")).strip()
            action = str(data.get("action", "")).strip().lower()
            if not confirm_id or action not in {"confirm", "refuse"}:
                return self._json({"error": "Parametres invalides: id/action requis"}, 400)

            perms = get_perms()
            if perms is None:
                return self._json({"error": "Permissions indisponibles"}, 503)

            result = perms.confirm(confirm_id) if action == "confirm" else perms.refuse(confirm_id)
            code = 200 if result.get("ok") else 404
            return self._json(result, code)

        # ── Commande vocale (audio → Azure Speech → Agent → TTS) ─────────────
        if path == "/api/voice":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)

            # Format audio envoyé dans le header (défaut: m4a)
            audio_fmt = self.headers.get("X-Audio-Format", "m4a").lower()

            # Body peut être raw bytes (audio) ou JSON avec base64
            content_type = self.headers.get("Content-Type", "")

            if "application/json" in content_type:
                # Audio encodé en base64 dans le JSON
                try:
                    data        = json.loads(body)
                    audio_b64   = data.get("audio_base64", "")
                    audio_fmt   = data.get("format", audio_fmt)
                    device_id   = auth_result.get("device_id") or data.get("device_id", "inconnu")
                    speak       = data.get("speak", True)
                    audio_bytes = base64.b64decode(audio_b64)
                except Exception as e:
                    return self._json({"error": f"JSON invalide : {e}"}, 400)
            else:
                # Audio brut dans le body
                audio_bytes = body
                device_id   = auth_result.get("device_id") or self.headers.get("X-Device-Id", "inconnu")
                speak       = True

            if not audio_bytes:
                return self._json({"error": "Audio vide"}, 400)

            print(f"\n  🎤 [{device_id}] Audio reçu ({len(audio_bytes)/1024:.1f} KB, fmt={audio_fmt})")

            # 1. Transcription Azure Speech
            t0          = time.time()
            transcribed = transcribe_audio(audio_bytes, audio_fmt)
            t_whisper   = int((time.time() - t0) * 1000)

            if not transcribed["success"]:
                print(f"  ❌ Azure Speech echoue : {transcribed['error']}")
                return self._json({
                    "success":    False,
                    "error":      transcribed["error"],
                    "transcript": "",
                    "step":       "stt",
                }, 500)

            transcript = transcribed["text"]
            print(f"  📝 Transcription ({t_whisper}ms) : \"{transcript}\"")

            if not transcript:
                return self._json({
                    "success":    False,
                    "error":      "Audio non reconnu (silence ou bruit ?)",
                    "transcript": "",
                    "step":       "stt",
                }, 422)

            # 2. Exécution via agent (synchrone ici pour retourner le résultat complet)
            cmd_id = str(uuid.uuid4())[:12]
            agent  = get_agent()
            if agent is None:
                return self._json({"error": "Agent indisponible"}, 503)

            t1     = time.time()
            result = agent.handle_command(transcript)
            t_exec = int((time.time() - t1) * 1000)
            success = result.get("success", False)
            message = result.get("message", "")
            print(f"  {'✅' if success else '❌'} Exécution ({t_exec}ms) : {message[:60]}")

            # 3. TTS — le PC répond à voix haute
            if speak:
                tts = get_tts()
                if tts:
                    tts.speak_result(result, transcript)

            # Stocker pour polling éventuel
            _store_result(cmd_id, result, t_whisper + t_exec)

            return self._json({
                "success":     success,
                "id":          cmd_id,
                "transcript":  transcript,
                "result":      result,
                "timings": {
                    "whisper_ms": t_whisper,
                    "exec_ms":    t_exec,
                    "total_ms":   t_whisper + t_exec,
                },
            })

        return self._json({"error": f"Route inconnue: {path}"}, 404)


# ── Démarrage ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    local_ip = get_local_ip()

    print()
    print("=" * 64)
    print("  🤖  JARVIS BRIDGE — Semaine 8 (Texte + Voix)")
    print("=" * 64)
    print(f"  IP locale  : {local_ip}")
    print(f"  Port       : {PORT}")
    print()
    print("  Dans api.service.ts, mets :")
    print(f"     const LOCAL_PC_IP = '{local_ip}';")
    print()
    print("  Routes :")
    print("    POST /api/command  ← commande texte")
    print("    POST /api/voice    ← audio (Azure Speech + TTS)")
    print("    POST /api/notify   ← push notification vers mobile")
    print("    POST /api/confirm  ← confirmer/refuser action dangereuse")
    print("    GET  /api/result/<id>")
    print("    GET  /api/notifications")
    print("    GET  /api/pending")
    print("    GET  /api/devices")
    print("    GET  /api/health")
    print("=" * 64 + "\n")

    # Pré-charger
    print("  ⏳ Chargement agent + TTS...")
    agent = get_agent()
    tts   = get_tts()

    if agent:
        ai_mode = "🤖 Groq" if getattr(getattr(agent,"parser",None),"ai_available",False) else "⚡ keywords"
        print(f"  ✅ Agent Jarvis — {ai_mode}")
    else:
        print("  ⚠  Agent indisponible")

    if tts:
        print(f"  ✅ TTS — backend={tts.backend}")
        if not OPENAI_API_KEY:
            print("     ⚠  OPENAI_API_KEY manquante → TTS pyttsx3 local")
    else:
        print("  ⚠  TTS indisponible")

    stt_ok = _AZURE_SPEECH_SDK and bool(AZURE_SPEECH_KEY) and bool(AZURE_SPEECH_REGION)
    if stt_ok:
        print(f"  ✅ Azure Speech STT — pret ({AZURE_SPEECH_REGION})")
    else:
        print("  ⚠  Azure Speech STT — config manquante (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION)")
    auth = get_auth()
    perms = get_perms()
    crypto = get_crypto()
    if _SECURITY_AVAILABLE and auth is not None and perms is not None:
        print(f"  ✅ Security — mode={auth.mode}, appareils={len(auth.list_devices())}")
    else:
        print("  ⚠  Security — mode dev (fallback token simple)")
    if crypto is not None and getattr(crypto, "available", False):
        print("  ✅ Crypto — AES-GCM pret")
    else:
        print("  ⚠  Crypto — indisponible")
    print()

    _start_battery_monitor_once()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), BridgeHandler)
    print("  ✅ Bridge actif — Ctrl+C pour arrêter\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  🛑 Bridge arrêté.")