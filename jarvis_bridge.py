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
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor
from socketserver import ThreadingMixIn
from http.server import HTTPServer

# ── Configuration du logger ───────────────────────────────────────────────────
try:
    from config.logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# ── Thread Pool HTTP Server (FIX #4) ───────────────────────────────────────
class ThreadPoolHTTPServer(ThreadingMixIn, HTTPServer):
    """
    HTTP Server with bounded thread pool to prevent memory DOS.
    Limits concurrent requests to MAX_WORKERS instead of unlimited threads.
    """
    def __init__(self, *args, max_workers=20, **kwargs):
        super().__init__(*args, **kwargs)
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="http-worker")
        print(f"  📊 HTTP Server: max {max_workers} concurrent workers")

    def process_request(self, request, client_address):
        """Override to use thread pool instead of unlimited threads."""
        self.executor.submit(self.process_request_thread, request, client_address)

    def process_request_thread(self, request, client_address):
        """Process request in thread pool."""
        try:
            self.finish_request(request, client_address)
            self.shutdown_request(request)
        except Exception:
            self.handle_error(request, client_address)
            self.shutdown_request(request)
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from modules.screen_share.capture import get_capture, reset_capture, ScreenCapture


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

_AZURE_STREAM_URL = ""   # ex: "https://jarvis-func.azurewebsites.net/api/stream"
try:
    from config.settings import AZURE_STREAM_URL as _AZURE_STREAM_URL_CFG
    _AZURE_STREAM_URL = _AZURE_STREAM_URL_CFG
except ImportError:
    pass
_screen_pusher_lock   = threading.Lock()

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

# Lazy initialization globals
_agent_init_thread = None
_agent_init_event = threading.Event()

_results:    dict        = {}
_store_lock  = threading.Lock()
_results_cleanup_thread = None
_results_cleanup_stop   = threading.Event()
_notifications: list     = []
_notifications_lock       = threading.Lock()

# ── Screen Share ─────────────────────────────────────────────────────────
_screen_pusher_thread: threading.Thread = None
_screen_pusher_stop   = threading.Event()

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


def _init_agent_background():
    """
    Run Agent() initialization on a background thread.
    Sets _agent_init_event when done (success or failure).
    """
    global _agent
    try:
        print("  ⏳ [Background] Initializing Agent (this takes 3-5 sec)...")
        _agent = Agent()
        print("  ✅ [Background] Agent ready!")
    except Exception as e:
        print(f"  ❌ [Background] Agent init failed: {e}")
        _agent = None
    finally:
        _agent_init_event.set()  # Signal initialization complete (success or fail)


def start_agent_init_once():
    """
    Kick off Agent initialization on background thread.
    Safe to call multiple times (only starts once).
    """
    global _agent_init_thread
    if _agent_init_thread is not None and _agent_init_thread.is_alive():
        return  # Already running

    _agent_init_thread = threading.Thread(
        target=_init_agent_background,
        daemon=False,  # Important: not a daemon — we wait for cleanup
        name="jarvis-agent-init"
    )
    _agent_init_thread.start()


def get_agent_async():
    """
    Get Agent without blocking.
    Returns None if still initializing.

    USAGE: In HTTP handlers that can handle 202 Accepted response
    """
    global _agent

    if _agent is not None:
        return _agent  # Already loaded

    if not _agent_init_event.is_set():
        return None  # Still initializing

    # Initialization finished but failed
    return _agent


def get_agent(wait_timeout=30):
    """
    Get Agent, optionally waiting for initialization.

    USAGE: For code that needs to block until Agent is ready.
    For HTTP handlers, prefer get_agent_async() + 202 response.

    Args:
        wait_timeout: Max seconds to wait for Agent to init (0 = no wait)

    Returns:
        Agent instance, or None if unavailable
    """
    global _agent

    if _agent is not None:
        return _agent  # Already ready

    if wait_timeout > 0 and AGENT_AVAILABLE:
        # Wait for background init to complete
        if _agent_init_event.wait(timeout=wait_timeout):
            return _agent  # Ready (success or failed)

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


def _cleanup_expired_results():
    """
    Background thread to clean expired results from _results dict.
    Prevents memory leak when clients don't poll results.
    """
    while not _results_cleanup_stop.is_set():
        try:
            current_time = time.time()
            expired_keys = []

            with _store_lock:
                for key, data in _results.items():
                    timestamp = data.get("timestamp", 0)
                    if current_time - timestamp > RESULT_TTL_SECONDS:
                        expired_keys.append(key)

                for key in expired_keys:
                    del _results[key]

            if expired_keys:
                print(f"  🧹 Cleaned {len(expired_keys)} expired results")

        except Exception as e:
            print(f"  ⚠️  Result cleanup error: {e}")

        # Sleep for 5 minutes between cleanups
        _results_cleanup_stop.wait(300)


def _start_results_cleanup_once():
    """
    Start background cleanup thread for expired results.
    Safe to call multiple times.
    """
    global _results_cleanup_thread
    if _results_cleanup_thread is not None and _results_cleanup_thread.is_alive():
        return

    _results_cleanup_thread = threading.Thread(
        target=_cleanup_expired_results,
        daemon=True,
        name="results-cleanup"
    )
    _results_cleanup_thread.start()
    print("  🧹 Results cleanup thread started")


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

# ══════════════════════════════════════════════════════════════════════════════
#  FONCTIONS SCREEN — à ajouter dans jarvis_bridge.py
# ══════════════════════════════════════════════════════════════════════════════
 
def _screen_start(params: dict) -> dict:
    """
    Démarre la capture d'écran.
    Params (tous optionnels) : fps, quality, scale, monitor, push_to_azure
    """
    from modules.screen_share.capture import get_capture, reset_capture
 
    # Récupérer les paramètres
    fps     = int(params.get("fps", 10))
    quality = int(params.get("quality", 60))
    scale   = float(params.get("scale", 1.0))
    monitor = int(params.get("monitor", 1))
 
    # Réinitialiser si les paramètres changent
    cap = get_capture()
    if cap.fps != fps or cap.quality != quality or cap.scale != scale or cap.monitor_idx != monitor:
        reset_capture()
        from modules.screen_share.capture import ScreenCapture
        import modules.screen_share.capture as _cap_module
        with _cap_module._capture_lock:
            _cap_module._capture_instance = ScreenCapture(
                fps=fps, quality=quality, scale=scale, monitor=monitor,
                detect_changes=True, adaptive_quality=True,
            )
        cap = _cap_module._capture_instance
 
    result = cap.start()
 
    # Démarrer le pusher Azure si configuré
    push_azure = bool(params.get("push_to_azure", False)) and bool(_AZURE_STREAM_URL)
    if push_azure and result["success"]:
        _start_azure_pusher(cap)
 
    return result
 
 
def _screen_stop() -> dict:
    """Arrête la capture et le pusher Azure si actif."""
    from modules.screen_share.capture import get_capture
    cap    = get_capture()
    result = cap.stop()
    _stop_azure_pusher()
    return result
 
 
def _screen_set_config(params: dict) -> dict:
    """Applique la configuration à la capture en cours."""
    from modules.screen_share.capture import get_capture
    cap = get_capture()
    changes = []
 
    if "fps" in params:
        r = cap.set_fps(int(params["fps"]))
        changes.append(r["message"])
    if "quality" in params:
        r = cap.set_quality(int(params["quality"]))
        changes.append(r["message"])
    if "scale" in params:
        cap.scale = max(0.1, min(1.0, float(params["scale"])))
        changes.append(f"Scale → {cap.scale}")
    if "monitor" in params:
        r = cap.set_monitor(int(params["monitor"]))
        changes.append(r["message"])
 
    if not changes:
        return {"success": False, "message": "Aucun paramètre valide fourni."}
 
    return {
        "success": True,
        "message": " | ".join(changes),
        "data":    cap.get_stats(),
    }
 
 
def _screen_get_frame(handler, cap, since_id: int = -1, fmt: str = "json") -> None:
    """
    Retourne le dernier frame capturé.
    Gère deux formats : JSON (base64) ou JPEG brut.
    """
    frame = cap.get_latest_frame()
 
    if frame is None:
        handler._json({"success": False, "message": "Aucun frame disponible. Démarre la capture avec POST /api/stream/start."})
        return
 
    # Polling différentiel : le mobile a déjà ce frame
    if since_id >= 0 and frame.frame_id <= since_id:
        handler._json({
            "success":   True,
            "status":    "same_frame",
            "frame_id":  frame.frame_id,
            "age_ms":    int((time.time() - frame.timestamp) * 1000),
        })
        return
 
    age_ms = int((time.time() - frame.timestamp) * 1000)
 
    # Format JPEG brut — le mobile affiche directement via uri={jpeg_uri}
    if fmt == "jpeg":
        jpeg = frame.jpeg_bytes
        handler.send_response(200)
        handler.send_header("Content-Type",                "image/jpeg")
        handler.send_header("Content-Length",              str(len(jpeg)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("X-Frame-Id",                  str(frame.frame_id))
        handler.send_header("X-Frame-Age-Ms",              str(age_ms))
        handler.send_header("X-Fps-Real",                  str(round(cap._fps_real, 1)))
        handler.send_header("Cache-Control",               "no-store")
        handler.end_headers()
        handler.wfile.write(jpeg)
        return
 
    # Format JSON — compatible avec l'app React Native
    handler._json({
        "success":   True,
        "status":    "ok",
        "frame_id":  frame.frame_id,
        "frame_b64": base64.b64encode(frame.jpeg_bytes).decode(),
        "width":     frame.width,
        "height":    frame.height,
        "size_kb":   round(frame.size_bytes / 1024, 1),
        "age_ms":    age_ms,
        "changed":   frame.changed,
        "stats":     cap.get_stats(),
    })
 
 
# ── Azure Pusher (optionnel) ──────────────────────────────────────────────────
 
def _start_azure_pusher(cap):
    """
    Lance un thread qui pousse les frames vers Azure Function /stream/push.
    Utilisé quand le mobile est hors du réseau local.
    """
    global _screen_pusher_thread, _screen_pusher_stop
 
    with _screen_pusher_lock:
        if _screen_pusher_thread and _screen_pusher_thread.is_alive():
            return  # Déjà en cours
 
        _screen_pusher_stop.clear()
        _screen_pusher_thread = threading.Thread(
            target=_azure_push_loop,
            args=(cap,),
            name="jarvis-azure-pusher",
            daemon=True,
        )
        _screen_pusher_thread.start()
        print("  📡 Azure pusher démarré")
 
 
def _stop_azure_pusher():
    global _screen_pusher_thread
    _screen_pusher_stop.set()
    with _screen_pusher_lock:
        if _screen_pusher_thread and _screen_pusher_thread.is_alive():
            _screen_pusher_thread.join(timeout=2.0)
        _screen_pusher_thread = None
 
 
def _azure_push_loop(cap):
    """
    Boucle qui récupère les frames et les pousse vers Azure.
    S'arrête automatiquement si la capture s'arrête ou si le stop event est set.
    """
    import urllib.request
    import urllib.error
 
    try:
        from config.settings import SECRET_TOKEN
    except ImportError:
        SECRET_TOKEN = "changeme"
 
    push_url  = f"{_AZURE_STREAM_URL.rstrip('/')}/push"
    last_id   = -1
    errors    = 0
    MAX_ERRORS = 5
 
    print(f"  📡 Push Azure → {push_url}")
 
    for frame in cap.iter_frames(timeout=3600, only_changes=True):
        if _screen_pusher_stop.is_set():
            break
 
        if frame.frame_id == last_id:
            continue
 
        last_id = frame.frame_id
 
        payload = json.dumps({
            "frame_b64":  base64.b64encode(frame.jpeg_bytes).decode(),
            "frame_id":   frame.frame_id,
            "width":      frame.width,
            "height":     frame.height,
            "size_bytes": frame.size_bytes,
            "fps_real":   cap._fps_real,
            "quality":    cap.quality,
        }).encode()
 
        try:
            req = urllib.request.Request(
                push_url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type":  "application/json",
                    "X-Jarvis-Token": SECRET_TOKEN,
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            errors = 0  # Reset sur succès
 
        except urllib.error.HTTPError as e:
            print(f"  ❌ Push Azure HTTP {e.code} : {e.reason}")
            errors += 1
        except Exception as e:
            print(f"  ❌ Push Azure erreur : {e}")
            errors += 1
 
        if errors >= MAX_ERRORS:
            print(f"  ⛔ Azure pusher arrêté après {MAX_ERRORS} erreurs consécutives.")
            break
 
    print("  📡 Azure pusher arrêté")

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
                     "Content-Type, X-Jarvis-Token, X-Device-Id, "
                     "X-Timestamp, X-Audio-Format, X-Since-Frame-Id")
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
        
        # ──────────────────────────────────────────────────────────────────────
        # SCREEN SHARE — GET endpoints
        # ──────────────────────────────────────────────────────────────────────
        if path == "/api/stream/status":
            cap = get_capture()
            return self._json({"success": True, "data": cap.get_stats()})
    
        if path == "/api/stream/frame":
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized"}, 401)
            cap = get_capture()
            since_id = -1
            try:
                since_id = int(query.get("since_id", [-1])[0])
            except Exception:
                pass
            fmt = (query.get("format", ["json"])[0]).lower()
            return _screen_get_frame(self, cap, since_id, fmt)
 
        if path == "/api/stream/monitors":
            return self._json(ScreenCapture.list_monitors())
    
        if path == "/api/stream/config":
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized"}, 401)
            cap = get_capture()
            return self._json({
                "success": True,
                "data": {
                    "fps":     cap.fps,
                    "quality": cap.quality,
                    "scale":   cap.scale,
                    "monitor": cap.monitor_idx,
                    "running": cap._running,
                }
            })
        
        # ── SENSORY CONTEXT (TONY STARK V2) ───────────────────────────────
        if path == "/api/context":
            auth_result = self._auth(method="GET", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized", "reason": auth_result.get("reason", "Unauthorized")}, 401)
            
            try:
                from core.sensory import SensoryCapteur
                sensory_data = SensoryCapteur.capture_full_context()
                return self._json({
                    "status": "ok",
                    "sensory_context": sensory_data,
                    "timestamp": sensory_data.get("timestamp", int(time.time())),
                    "system": sensory_data.get("system", {}),
                    "window": sensory_data.get("window", {}),
                    "apps": sensory_data.get("apps", []),
                    "network": sensory_data.get("network", {}),
                })
            except Exception as e:
                print(f"❌ Erreur /api/context : {e}")
                return self._json({"error": str(e), "status": "error"}, 500)

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

            # ─── FIX: Check if Agent is ready ───
            agent = get_agent_async()
            if agent is None:
                # Agent still initializing
                return self._json({
                    "status": "agent_loading",
                    "message": "Agent initializing (3-5 sec), retry in 2 seconds",
                    "retry_after": 2,
                }, 202)  # 202 Accepted

            # Agent is ready, proceed as normal
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
        
        # ──────────────────────────────────────────────────────────────────────
        # SCREEN SHARE — POST endpoints
        # ──────────────────────────────────────────────────────────────────────
        if path == "/api/stream/start":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized"}, 401)
            try:
                data = json.loads(body or b"{}")
            except Exception:
                data = {}
            return _screen_start(data)
    
        if path == "/api/stream/stop":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized"}, 401)
            return _screen_stop()
    
        if path == "/api/stream/config":
            auth_result = self._auth(body=body, method="POST", path=path)
            if not auth_result["ok"]:
                return self._json({"error": "Unauthorized"}, 401)
            try:
                data = json.loads(body or b"{}")
            except Exception:
                return self._json({"error": "JSON invalide"}, 400)
            return _screen_set_config(data)

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
    print("    POST /api/stream/start     ← démarrer capture")
    print("    POST /api/stream/stop      ← arrêter capture")
    print("    POST /api/stream/config    ← changer FPS/qualité")
    print("    GET  /api/result/<id>")
    print("    GET  /api/notifications")
    print("    GET  /api/pending")
    print("    GET  /api/devices")
    print("    GET  /api/health")
    print("    GET  /api/stream/status    ← statut capture")
    print("    GET  /api/stream/frame     ← dernier frame (JSON ou JPEG)")
    print("    GET  /api/stream/monitors  ← liste moniteurs")
    print("    GET  /api/stream/config    ← config actuelle")
    
    print("=" * 64 + "\n")

    # Start Agent initialization in background (don't block)
    print("  ⏳ Starting Agent initialization in background...")
    start_agent_init_once()

    # Don't block here — server starts immediately
    # Agent will be ready in ~3-5 seconds
    print("  ℹ️  Note: First API requests may return 202 'Agent loading' until ready")

    # Load TTS immediately (fast)
    tts = get_tts()
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
    _start_results_cleanup_once()

    server = ThreadPoolHTTPServer(("0.0.0.0", PORT), BridgeHandler, max_workers=20)
    print("  ✅ Bridge actif — Ctrl+C pour arrêter\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  🛑 Bridge arrêté.")