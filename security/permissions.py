"""
permissions.py — Niveaux de permission + confirmation des commandes critiques
SEMAINE 10 — MARDI/MERCREDI

Niveaux :
  LEVEL_READ   (1) : lecture seule — infos système, lister fichiers
  LEVEL_WRITE  (2) : actions réversibles — ouvrir app, volume, navigateur
  LEVEL_DANGER (3) : actions destructives ou irréversibles — éteindre,
                     supprimer fichier, tuer un processus
  LEVEL_ADMIN  (4) : réservé futur — modification système

Confirmation :
  Les commandes LEVEL_DANGER génèrent une demande de confirmation.
  Le bridge attend un POST /api/confirm?id=... avant d'exécuter.
  Timeout configurable (défaut 30s) — passé ce délai, la commande est annulée.
"""

import threading
import time
import uuid
from config.logger import get_logger

logger = get_logger(__name__)

# ── Niveaux ────────────────────────────────────────────────────────────────────
LEVEL_READ   = 1
LEVEL_WRITE  = 2
LEVEL_DANGER = 3
LEVEL_ADMIN  = 4

LEVEL_NAMES = {
    LEVEL_READ:   "lecture",
    LEVEL_WRITE:  "écriture",
    LEVEL_DANGER: "dangereux",
    LEVEL_ADMIN:  "admin",
}

# ── Mapping complet intention → niveau ────────────────────────────────────────
PERMISSION_MAP: dict[str, int] = {
    # ── Lecture / Infos ───────────────────────────────────────────────────────
    "SYSTEM_INFO":         LEVEL_READ,
    "SYSTEM_NETWORK":      LEVEL_READ,
    "NETWORK_INFO":        LEVEL_READ,
    "WIFI_LIST":           LEVEL_READ,
    "BLUETOOTH_LIST":      LEVEL_READ,
    "SCREEN_INFO":         LEVEL_READ,
    "APP_LIST":            LEVEL_READ,
    "FILE_INFO":           LEVEL_READ,
    "FILE_LIST":           LEVEL_READ,
    "FOLDER_LIST":         LEVEL_READ,
    "FILE_SEARCH":         LEVEL_READ,
    "FILE_SEARCH_TYPE":    LEVEL_READ,
    "FILE_SEARCH_CONTENT": LEVEL_READ,
    "DOC_READ":            LEVEL_READ,
    "DOC_SEARCH_WORD":     LEVEL_READ,
    "HELP":                LEVEL_READ,

    # ── Actions réversibles ───────────────────────────────────────────────────
    "APP_OPEN":            LEVEL_WRITE,
    "APP_CLOSE":           LEVEL_WRITE,
    "BROWSER_OPEN":        LEVEL_WRITE,
    "BROWSER_SEARCH":      LEVEL_WRITE,
    "BROWSER_CLOSE":       LEVEL_WRITE,
    "AUDIO_VOLUME_UP":     LEVEL_WRITE,
    "AUDIO_VOLUME_DOWN":   LEVEL_WRITE,
    "AUDIO_VOLUME_SET":    LEVEL_WRITE,
    "AUDIO_MUTE":          LEVEL_WRITE,
    "AUDIO_PLAY":          LEVEL_WRITE,
    "FILE_OPEN":           LEVEL_WRITE,
    "FILE_COPY":           LEVEL_WRITE,
    "FOLDER_CREATE":       LEVEL_WRITE,
    "SCREEN_CAPTURE":      LEVEL_WRITE,
    "SCREENSHOT_TO_PHONE": LEVEL_WRITE,
    "SCREEN_BRIGHTNESS":   LEVEL_WRITE,
    "WIFI_CONNECT":        LEVEL_WRITE,
    "WIFI_DISCONNECT":     LEVEL_WRITE,
    "WIFI_ENABLE":         LEVEL_WRITE,
    "WIFI_DISABLE":        LEVEL_WRITE,
    "BLUETOOTH_ENABLE":    LEVEL_WRITE,
    "BLUETOOTH_DISABLE":   LEVEL_WRITE,
    "NOTIFY_SEND":         LEVEL_WRITE,
    "DOC_SUMMARIZE":       LEVEL_WRITE,
    "SCREEN_RECORD":       LEVEL_WRITE,

    # ── Dangereux / Irréversibles ─────────────────────────────────────────────
    "SYSTEM_SHUTDOWN":     LEVEL_DANGER,
    "SYSTEM_RESTART":      LEVEL_DANGER,
    "SYSTEM_SLEEP":        LEVEL_DANGER,
    "SYSTEM_LOCK":         LEVEL_WRITE,   # lock = réversible
    "SYSTEM_KILL_PROCESS": LEVEL_DANGER,
    "FILE_DELETE":         LEVEL_DANGER,
    "FILE_MOVE":           LEVEL_DANGER,
    "FILE_RENAME":         LEVEL_WRITE,
}

# ── Messages de confirmation par intention ────────────────────────────────────
CONFIRM_MESSAGES: dict[str, str] = {
    "SYSTEM_SHUTDOWN":     "⚠️ Éteindre le PC ?",
    "SYSTEM_RESTART":      "⚠️ Redémarrer le PC ?",
    "SYSTEM_SLEEP":        "Mettre le PC en veille ?",
    "SYSTEM_KILL_PROCESS": "⚠️ Forcer la fermeture du processus ?",
    "FILE_DELETE":         "⚠️ Supprimer définitivement ce fichier ?",
    "FILE_MOVE":           "Déplacer ce fichier ?",
}

CONFIRM_TIMEOUT = 30   # secondes


class ConfirmationRequest:
    """Représente une demande de confirmation en attente."""
    def __init__(self, intent: str, params: dict, command: str):
        self.id        = str(uuid.uuid4())[:12]
        self.intent    = intent
        self.params    = params
        self.command   = command
        self.created   = time.time()
        self.confirmed = None    # None=en attente, True=confirmé, False=refusé
        self._event    = threading.Event()

    def confirm(self):
        self.confirmed = True
        self._event.set()

    def refuse(self):
        self.confirmed = False
        self._event.set()

    def wait(self, timeout: float = CONFIRM_TIMEOUT) -> bool:
        """Bloque jusqu'à confirmation ou timeout. Retourne True si confirmé."""
        self._event.wait(timeout)
        if self.confirmed is None:
            self.confirmed = False   # timeout → refusé
        return self.confirmed

    def is_expired(self) -> bool:
        return time.time() - self.created > CONFIRM_TIMEOUT

    def to_dict(self) -> dict:
        return {
            "id":       self.id,
            "intent":   self.intent,
            "command":  self.command,
            "message":  CONFIRM_MESSAGES.get(self.intent,
                        f"Confirmer : {self.command} ?"),
            "level":    LEVEL_NAMES[PERMISSION_MAP.get(self.intent, LEVEL_WRITE)],
            "expires":  int(self.created + CONFIRM_TIMEOUT),
        }


class Permissions:
    """
    Gestion des permissions et confirmations.
    Instance partagée — thread-safe.
    """

    def __init__(self):
        self._lock    = threading.Lock()
        self._pending: dict[str, ConfirmationRequest] = {}
        # Nettoyage automatique des confirmations expirées
        self._start_cleanup()
        logger.info("Permissions initialisées")

    # ── Vérification ─────────────────────────────────────────────────────────

    def get_level(self, intent: str) -> int:
        """Retourne le niveau requis pour une intention."""
        return PERMISSION_MAP.get(intent, LEVEL_WRITE)

    def requires_confirmation(self, intent: str) -> bool:
        """True si l'intention nécessite une confirmation téléphone."""
        return self.get_level(intent) >= LEVEL_DANGER

    def is_allowed(self, intent: str, device_level: int) -> bool:
        """
        Vérifie si l'appareil a le niveau suffisant.
        device_level : niveau de l'appareil (1-3 depuis Auth.get_device_level)
        """
        required = self.get_level(intent)
        allowed  = device_level >= required
        if not allowed:
            logger.warning(
                f"Permission refusée : {intent} "
                f"(requis={required}, appareil={device_level})"
            )
        return allowed

    def describe(self, intent: str) -> dict:
        """Retourne la description de sécurité d'une intention."""
        level = self.get_level(intent)
        return {
            "intent":                intent,
            "level":                 level,
            "level_name":            LEVEL_NAMES.get(level, "inconnu"),
            "requires_confirmation": self.requires_confirmation(intent),
            "confirm_message":       CONFIRM_MESSAGES.get(intent, ""),
        }

    # ── Confirmations ─────────────────────────────────────────────────────────

    def create_confirmation(self, intent: str, params: dict,
                            command: str) -> ConfirmationRequest:
        """Crée et enregistre une demande de confirmation."""
        req = ConfirmationRequest(intent, params, command)
        with self._lock:
            self._pending[req.id] = req
        logger.info(f"Confirmation demandée : {intent} (id={req.id[:8]})")
        return req

    def confirm(self, confirm_id: str) -> dict:
        """Confirme une action en attente."""
        with self._lock:
            req = self._pending.get(confirm_id)
        if not req:
            return {"ok": False, "reason": "ID inconnu ou expiré"}
        if req.is_expired():
            return {"ok": False, "reason": "Délai de confirmation dépassé"}
        req.confirm()
        logger.info(f"Action confirmée : {req.intent} (id={confirm_id[:8]})")
        return {"ok": True, "intent": req.intent}

    def refuse(self, confirm_id: str) -> dict:
        """Refuse une action en attente."""
        with self._lock:
            req = self._pending.get(confirm_id)
        if not req:
            return {"ok": False, "reason": "ID inconnu"}
        req.refuse()
        logger.info(f"Action refusée : {req.intent} (id={confirm_id[:8]})")
        return {"ok": True, "intent": req.intent}

    def get_pending(self) -> list:
        """Retourne toutes les confirmations en attente (non expirées)."""
        with self._lock:
            return [r.to_dict() for r in self._pending.values()
                    if not r.is_expired() and r.confirmed is None]

    def get_confirmation(self, confirm_id: str) -> dict | None:
        with self._lock:
            req = self._pending.get(confirm_id)
            return req.to_dict() if req else None

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _cleanup(self):
        while True:
            time.sleep(60)
            with self._lock:
                expired = [k for k, v in self._pending.items()
                           if v.is_expired()]
                for k in expired:
                    del self._pending[k]
            if expired:
                logger.debug(f"Nettoyage : {len(expired)} confirmation(s) expirée(s)")

    def _start_cleanup(self):
        t = threading.Thread(target=self._cleanup, daemon=True)
        t.start()