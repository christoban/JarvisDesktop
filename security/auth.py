"""
auth.py — Authentification par token HMAC + registre d'appareils
SEMAINE 10 — LUNDI

Architecture :
  Chaque appareil autorisé a un DEVICE_ID unique enregistré dans
  data/devices.json. Chaque requête doit porter :
    - X-Jarvis-Token   : SECRET_TOKEN (authentification simple, rétro-compatible)
    - X-Jarvis-Sig     : HMAC-SHA256(SECRET_TOKEN, "METHOD:PATH:TIMESTAMP:BODY_HASH")
    - X-Timestamp      : Unix timestamp (±5 min tolérance anti-replay)
    - X-Device-Id      : identifiant de l'appareil

  Modes :
    MODE_SIMPLE  → vérifie seulement X-Jarvis-Token (semaines 7-9, rétro-compat)
    MODE_HMAC    → vérifie signature HMAC + timestamp + device registré
    MODE_STRICT  → HMAC obligatoire, rejette les appareils non enregistrés
"""

import hashlib
import hmac as _hmac
import json
import os
import time
import threading
from pathlib import Path
from config.logger   import get_logger
from config.settings import SECRET_TOKEN, DEVICE_ID, BASE_DIR

logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
DEVICES_FILE    = BASE_DIR / "data" / "devices.json"
TIMESTAMP_SLACK = 300          # ±5 minutes de tolérance anti-replay
NONCE_TTL       = 600          # nonces mémorisés 10 min
MAX_NONCES      = 10_000       # limite mémoire

MODE_SIMPLE = "simple"
MODE_HMAC   = "hmac"
MODE_STRICT = "strict"


class Auth:
    """
    Système d'authentification Jarvis.
    Thread-safe — utilisé depuis plusieurs threads dans le bridge.
    """

    def __init__(self, mode: str = MODE_SIMPLE):
        """
        Args:
            mode : MODE_SIMPLE | MODE_HMAC | MODE_STRICT
        """
        self.mode      = mode
        self._lock     = threading.Lock()
        self._nonces   = {}          # nonce → expiry timestamp (anti-replay)
        self._devices  = {}          # device_id → {name, level, registered_at}
        DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load_devices()
        # Toujours enregistrer l'appareil principal défini dans .env
        if DEVICE_ID and DEVICE_ID not in self._devices:
            self.register_device(DEVICE_ID, "Appareil principal", level=3)
        logger.info(f"Auth initialisé — mode={mode}, "
                    f"{len(self._devices)} appareil(s) enregistré(s)")

    # ── API publique ─────────────────────────────────────────────────────────

    def verify_request(self, headers: dict, body: bytes = b"",
                       method: str = "POST", path: str = "/") -> dict:
        """
        Vérifie une requête entrante.

        Args:
            headers : dict des headers HTTP
            body    : corps brut de la requête
            method  : GET | POST
            path    : ex: "/api/command"

        Returns:
            {"ok": bool, "device_id": str, "reason": str}
        """
        token     = headers.get("X-Jarvis-Token",  "") or \
                    headers.get("x-jarvis-token",  "")
        signature = headers.get("X-Jarvis-Sig",    "") or \
                    headers.get("x-jarvis-sig",    "")
        timestamp = headers.get("X-Timestamp",     "") or \
                    headers.get("x-timestamp",     "")
        device_id = headers.get("X-Device-Id",     "") or \
                    headers.get("x-device-id",     "")
        nonce     = headers.get("X-Nonce",         "") or \
                    headers.get("x-nonce",         "")

        if self.mode == MODE_SIMPLE:
            return self._verify_simple(token, device_id)

        elif self.mode == MODE_HMAC:
            # Fallback simple token UNIQUEMENT si aucune signature n'est fournie
            # Si X-Jarvis-Sig est présent → on vérifie HMAC sans exception
            if not signature and token == SECRET_TOKEN:
                return {"ok": True, "device_id": device_id or "unknown",
                        "reason": "simple_token_fallback"}
            return self._verify_hmac(signature, timestamp, nonce,
                                     device_id, method, path, body)

        elif self.mode == MODE_STRICT:
            return self._verify_hmac(signature, timestamp, nonce,
                                     device_id, method, path, body,
                                     require_registered=True)

        return {"ok": False, "device_id": "", "reason": "Mode inconnu"}

    def generate_token(self, device_id: str, method: str = "POST",
                       path: str = "/", body: bytes = b"") -> dict:
        """
        Génère les headers d'authentification pour une requête.
        Utilisé par les tests et par l'app mobile (côté Python).

        Returns:
            {"X-Jarvis-Token": ..., "X-Jarvis-Sig": ...,
             "X-Timestamp": ..., "X-Device-Id": ..., "X-Nonce": ...}
        """
        ts         = str(int(time.time()))
        nonce      = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        body_hash  = hashlib.sha256(body).hexdigest()
        clean_path = path.split("?")[0].split("/api/")[-1]
        msg        = f"{method}:{clean_path}:{ts}:{body_hash}:{nonce}"
        sig        = _hmac.new(
            SECRET_TOKEN.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "X-Jarvis-Token": SECRET_TOKEN,
            "X-Jarvis-Sig":   sig,
            "X-Timestamp":    ts,
            "X-Device-Id":    device_id,
            "X-Nonce":        nonce,
            "Content-Type":   "application/json",
        }

    def register_device(self, device_id: str, name: str = "",
                        level: int = 2) -> dict:
        """
        Enregistre un nouvel appareil autorisé.
        level : 1=lecture, 2=actions normales, 3=commandes critiques
        """
        if not device_id:
            return {"ok": False, "reason": "device_id vide"}
        entry = {
            "name":          name or device_id,
            "level":         level,
            "registered_at": int(time.time()),
            "last_seen":     None,
        }
        with self._lock:
            self._devices[device_id] = entry
            self._save_devices()
        logger.info(f"Appareil enregistré : {device_id} ({name}, level={level})")
        return {"ok": True, "device_id": device_id, "level": level}

    def revoke_device(self, device_id: str) -> dict:
        """Révoque l'accès d'un appareil."""
        with self._lock:
            if device_id not in self._devices:
                return {"ok": False, "reason": "Appareil inconnu"}
            del self._devices[device_id]
            self._save_devices()
        logger.warning(f"Appareil révoqué : {device_id}")
        return {"ok": True, "device_id": device_id}

    def list_devices(self) -> list:
        """Retourne la liste des appareils enregistrés."""
        with self._lock:
            return [{"device_id": k, **v}
                    for k, v in self._devices.items()]

    def get_device_level(self, device_id: str) -> int:
        """Retourne le niveau de l'appareil (1-3), 0 si inconnu."""
        with self._lock:
            return self._devices.get(device_id, {}).get("level", 0)

    def is_device_registered(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._devices

    # ── Vérifications internes ────────────────────────────────────────────────

    def _verify_simple(self, token: str, device_id: str) -> dict:
        """Mode simple : compare le token brut."""
        if not token:
            return {"ok": False, "device_id": "", "reason": "Token manquant"}
        if not _hmac.compare_digest(token, SECRET_TOKEN):
            logger.warning(f"Token invalide depuis {device_id or 'inconnu'}")
            return {"ok": False, "device_id": device_id,
                    "reason": "Token invalide"}
        self._touch_device(device_id)
        return {"ok": True, "device_id": device_id, "reason": "simple_token"}

    def _verify_hmac(self, signature: str, timestamp: str, nonce: str,
                     device_id: str, method: str, path: str, body: bytes,
                     require_registered: bool = False) -> dict:
        """Mode HMAC : vérifie signature + timestamp + nonce anti-replay."""
        if not signature:
            return {"ok": False, "device_id": device_id,
                    "reason": "Signature manquante"}

        # 1. Vérification timestamp
        try:
            ts  = int(timestamp)
            age = abs(int(time.time()) - ts)
            if age > TIMESTAMP_SLACK:
                logger.warning(f"Timestamp trop ancien : {age}s (device={device_id})")
                return {"ok": False, "device_id": device_id,
                        "reason": f"Timestamp expiré ({age}s)"}
        except (ValueError, TypeError):
            return {"ok": False, "device_id": device_id,
                    "reason": "Timestamp invalide"}

        # 2. Anti-replay (nonce unique)
        if nonce:
            if self._nonce_seen(nonce):
                logger.warning(f"Nonce réutilisé : {nonce} (device={device_id})")
                return {"ok": False, "device_id": device_id,
                        "reason": "Nonce déjà utilisé (replay attack?)"}
            self._store_nonce(nonce, ts + NONCE_TTL)

        # 3. Vérification HMAC
        body_hash  = hashlib.sha256(body).hexdigest()
        clean_path = path.split("?")[0].split("/api/")[-1]
        msg        = (f"{method}:{clean_path}:{timestamp}:{body_hash}"
                      + (f":{nonce}" if nonce else ""))
        expected   = _hmac.new(
            SECRET_TOKEN.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()

        if not _hmac.compare_digest(expected, signature):
            logger.warning(f"Signature HMAC invalide (device={device_id})")
            return {"ok": False, "device_id": device_id,
                    "reason": "Signature invalide"}

        # 4. Vérification appareil (mode strict)
        if require_registered and not self.is_device_registered(device_id):
            logger.warning(f"Appareil non enregistré : {device_id}")
            return {"ok": False, "device_id": device_id,
                    "reason": f"Appareil non autorisé : {device_id}"}

        self._touch_device(device_id)
        return {"ok": True, "device_id": device_id, "reason": "hmac_ok"}

    # ── Nonces anti-replay ────────────────────────────────────────────────────

    def _nonce_seen(self, nonce: str) -> bool:
        now = time.time()
        with self._lock:
            # Nettoyer les nonces expirés
            self._nonces = {k: v for k, v in self._nonces.items() if v > now}
            return nonce in self._nonces

    def _store_nonce(self, nonce: str, expiry: float):
        with self._lock:
            if len(self._nonces) < MAX_NONCES:
                self._nonces[nonce] = expiry

    # ── Persistance appareils ─────────────────────────────────────────────────

    def _load_devices(self):
        try:
            if DEVICES_FILE.exists():
                self._devices = json.loads(DEVICES_FILE.read_text())
                logger.info(f"Registre chargé : {len(self._devices)} appareil(s)")
        except Exception as e:
            logger.error(f"Chargement registre : {e}")
            self._devices = {}

    def _save_devices(self):
        try:
            DEVICES_FILE.write_text(
                json.dumps(self._devices, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error(f"Sauvegarde registre : {e}")

    def _touch_device(self, device_id: str):
        if device_id:
            with self._lock:
                if device_id in self._devices:
                    self._devices[device_id]["last_seen"] = int(time.time())