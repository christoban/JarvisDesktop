"""
modules/screen_share/capture.py — Capture d'écran continue + streaming
=======================================================================
Semaine 9 — Screen Share

Responsabilités :
  - Capture continue du bureau Windows (via mss — le plus rapide)
  - Compression JPEG intelligente (qualité adaptative selon le FPS réel)
  - Détection de changement : n'encode pas si le frame est identique
  - Thread de capture séparé → file de frames thread-safe
  - API simple : start() / stop() / get_latest_frame() / iter_frames()
  - Stats en temps réel : FPS réel, taille frame, bande passante estimée

Dépendances :
    pip install mss pillow numpy

Architecture :
    ScreenCapture
    ├── _capture_loop()    — thread dédié, tourne en continu
    ├── get_latest_frame() — retourne le dernier JPEG (bytes + metadata)
    ├── iter_frames()      — générateur bloquant pour le streaming MJPEG
    └── get_stats()        — FPS, taille, bande passante

Intégration jarvis_bridge.py :
    _screen_capture = ScreenCapture()

    GET  /api/screen/start  → _screen_capture.start()
    GET  /api/screen/stop   → _screen_capture.stop()
    GET  /api/screen/frame  → _screen_capture.get_latest_frame()
    GET  /api/screen/stream → MJPEG streaming (iter_frames)
    GET  /api/screen/status → _screen_capture.get_stats()
"""

from __future__ import annotations

import hashlib
import io
import threading
import time
from collections import deque
from typing import Generator

from config.logger import get_logger

logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
DEFAULT_FPS       = 10          # FPS cible
MIN_FPS           = 1           # FPS minimum garanti
MAX_FPS           = 30          # FPS maximum
DEFAULT_QUALITY   = 60          # Qualité JPEG initiale (0-100)
MIN_QUALITY       = 20          # Qualité minimale (bande passante faible)
MAX_QUALITY       = 90          # Qualité maximale
MAX_FRAME_QUEUE   = 3           # Taille max de la file (évite la latence accumulée)
CHANGE_THRESHOLD  = 0.002       # Seuil de détection de changement (0.2% pixels différents)
SCALE_FACTOR      = 1.0         # 1.0 = pleine résolution, 0.5 = demi-résolution
MAX_WIDTH         = 1920        # Largeur max (downscale si dépasse)
MAX_HEIGHT        = 1080        # Hauteur max
MAX_FRAME_SOFT_BYTES = 200_000  # Ajuste la qualité si un frame dépasse cette taille


class FrameData:
    """Données d'un frame capturé."""
    __slots__ = ("jpeg_bytes", "timestamp", "width", "height",
                 "size_bytes", "frame_id", "changed")

    def __init__(
        self,
        jpeg_bytes: bytes,
        width: int,
        height: int,
        frame_id: int,
        changed: bool = True,
    ):
        self.jpeg_bytes  = jpeg_bytes
        self.timestamp   = time.time()
        self.width       = width
        self.height      = height
        self.size_bytes  = len(jpeg_bytes)
        self.frame_id    = frame_id
        self.changed     = changed

    def to_dict(self, include_data: bool = True) -> dict:
        d = {
            "frame_id":   self.frame_id,
            "timestamp":  self.timestamp,
            "width":      self.width,
            "height":     self.height,
            "size_bytes": self.size_bytes,
            "size_kb":    round(self.size_bytes / 1024, 1),
            "changed":    self.changed,
        }
        if include_data:
            import base64
            d["jpeg_b64"] = base64.b64encode(self.jpeg_bytes).decode()
        return d


class ScreenCapture:
    """
    Capture d'écran continue en thread séparé.

    Usage basique :
        cap = ScreenCapture(fps=10, quality=60)
        cap.start()

        frame = cap.get_latest_frame()  # FrameData ou None
        if frame:
            with open("screen.jpg", "wb") as f:
                f.write(frame.jpeg_bytes)

        cap.stop()

    Usage streaming MJPEG :
        for frame in cap.iter_frames(timeout=30):
            # frame.jpeg_bytes → envoyer au client HTTP
            send_multipart_chunk(frame.jpeg_bytes)
    """

    def __init__(
        self,
        fps: int = DEFAULT_FPS,
        quality: int = DEFAULT_QUALITY,
        monitor: int = 1,           # 1 = écran principal, 0 = tous les écrans
        scale: float = SCALE_FACTOR,
        detect_changes: bool = True,
        adaptive_quality: bool = True,
    ):
        self.fps              = max(MIN_FPS, min(MAX_FPS, fps))
        self.quality          = max(MIN_QUALITY, min(MAX_QUALITY, quality))
        self.monitor_idx      = monitor
        self.scale            = max(0.1, min(1.0, scale))
        self.detect_changes   = detect_changes
        self.adaptive_quality = adaptive_quality

        # État interne
        self._running      = False
        self._thread: threading.Thread | None = None
        self._lock         = threading.Lock()
        self._new_frame    = threading.Event()

        # File de frames (deque thread-safe)
        self._queue: deque[FrameData] = deque(maxlen=MAX_FRAME_QUEUE)

        # Stats
        self._frame_id      = 0
        self._fps_real      = 0.0
        self._fps_history   = deque(maxlen=30)   # timestamps des derniers frames
        self._bw_history    = deque(maxlen=30)   # tailles des derniers frames
        self._started_at    = 0.0
        self._total_frames  = 0
        self._skipped       = 0

        # Hash du dernier frame pour détection de changement
        self._last_hash: str = ""

        # Availability check
        self._mss_ok   = self._check_mss()
        self._pil_ok   = self._check_pil()
        self._numpy_ok = self._check_numpy()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> dict:
        """Démarre la capture en arrière-plan."""
        if self._running:
            return {"success": True, "message": "Capture déjà en cours.", "data": self.get_stats()}

        if not self._mss_ok:
            return {"success": False, "message": "mss non installé. pip install mss"}
        if not self._pil_ok:
            return {"success": False, "message": "Pillow non installé. pip install pillow"}

        self._running    = True
        self._started_at = time.time()
        self._frame_id   = 0
        self._total_frames = 0
        self._skipped    = 0
        self._fps_history.clear()
        self._bw_history.clear()
        self._queue.clear()
        self._new_frame.clear()
        self._last_hash = ""

        self._thread = threading.Thread(
            target=self._capture_loop,
            name="jarvis-screen-capture",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Screen capture démarrée — {self.fps} FPS cible, qualité {self.quality}%")
        return {
            "success": True,
            "message": f"Capture démarrée ({self.fps} FPS, qualité {self.quality}%).",
            "data": self.get_stats(),
        }

    def stop(self) -> dict:
        """Arrête la capture."""
        if not self._running:
            return {"success": True, "message": "Capture déjà arrêtée.", "data": {}}

        self._running = False
        self._new_frame.set()   # Débloquer iter_frames en attente

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        stats = self.get_stats()
        logger.info(f"Screen capture arrêtée — {stats.get('total_frames', 0)} frames capturés")
        return {
            "success": True,
            "message": "Capture arrêtée.",
            "data": stats,
        }

    def get_latest_frame(self) -> FrameData | None:
        """Retourne le dernier frame capturé, ou None si aucun."""
        with self._lock:
            if self._queue:
                return self._queue[-1]
            return None

    def get_frame_dict(self, include_data: bool = True) -> dict:
        """Retourne le dernier frame en dict JSON-sérialisable."""
        frame = self.get_latest_frame()
        if frame is None:
            return {"success": False, "message": "Aucun frame disponible. Lance la capture d'abord."}
        return {"success": True, "message": "Frame prêt.", "data": frame.to_dict(include_data)}

    def iter_frames(
        self,
        timeout: float = 60.0,
        only_changes: bool = False,
    ) -> Generator[FrameData, None, None]:
        """
        Générateur bloquant de frames pour le streaming MJPEG.
        Bloque jusqu'à ce qu'un nouveau frame soit disponible.

        Args:
            timeout      : durée max de streaming en secondes
            only_changes : ne yield que les frames avec changement détecté

        Usage dans jarvis_bridge.py :
            for frame in capture.iter_frames(timeout=120):
                send_mjpeg_chunk(frame.jpeg_bytes)
        """
        start      = time.time()
        last_id    = -1

        while self._running and (time.time() - start) < timeout:
            # Attendre un nouveau frame (max 1 seconde pour pouvoir checker le timeout)
            self._new_frame.wait(timeout=1.0)
            self._new_frame.clear()

            if not self._running:
                break

            frame = self.get_latest_frame()
            if frame is None or frame.frame_id == last_id:
                continue
            if only_changes and not frame.changed:
                continue

            last_id = frame.frame_id
            yield frame

    def get_stats(self) -> dict:
        """Retourne les stats de capture en temps réel."""
        now = time.time()

        # FPS réel sur la dernière seconde
        recent = [t for t in self._fps_history if now - t < 1.0]
        fps_real = len(recent)

        # Bande passante estimée (KB/s)
        recent_bw = list(self._bw_history)[-fps_real:] if fps_real else []
        bw_kbps   = sum(recent_bw) / 1024 if recent_bw else 0

        # Dernier frame
        frame = self.get_latest_frame()
        last_size = frame.size_bytes if frame else 0
        last_ts   = frame.timestamp if frame else 0

        return {
            "running":       self._running,
            "fps_target":    self.fps,
            "fps_real":      fps_real,
            "quality":       self.quality,
            "monitor":       self.monitor_idx,
            "scale":         self.scale,
            "total_frames":  self._total_frames,
            "skipped":       self._skipped,
            "last_size_kb":  round(last_size / 1024, 1),
            "bw_kbps":       round(bw_kbps, 1),
            "uptime_s":      round(now - self._started_at, 1) if self._started_at else 0,
            "last_frame_ts": last_ts,
            "frame_id":      self._frame_id,
            "detect_changes": self.detect_changes,
            "adaptive_quality": self.adaptive_quality,
        }

    def set_fps(self, fps: int) -> dict:
        """Ajuste le FPS cible à la volée."""
        self.fps = max(MIN_FPS, min(MAX_FPS, fps))
        return {"success": True, "message": f"FPS cible → {self.fps}", "data": {"fps": self.fps}}

    def set_quality(self, quality: int) -> dict:
        """Ajuste la qualité JPEG à la volée."""
        self.quality = max(MIN_QUALITY, min(MAX_QUALITY, quality))
        return {"success": True, "message": f"Qualité → {self.quality}%", "data": {"quality": self.quality}}

    def set_monitor(self, monitor: int) -> dict:
        """Change le moniteur à capturer (nécessite restart)."""
        self.monitor_idx = monitor
        return {"success": True, "message": f"Moniteur → {monitor} (redémarre la capture)"}

    # ── Boucle de capture ─────────────────────────────────────────────────────

    def _capture_loop(self):
        """
        Thread principal de capture.
        Tourne en boucle à self.fps FPS cible.
        Adapte la qualité JPEG si le FPS réel est trop bas.
        """
        try:
            import mss
            import mss.tools
            from PIL import Image
        except ImportError as e:
            logger.error(f"Dépendance manquante pour la capture : {e}")
            self._running = False
            return

        logger.info("Boucle de capture démarrée")
        interval = 1.0 / self.fps

        with mss.mss() as sct:
            # Récupérer les moniteurs disponibles
            monitors = sct.monitors   # [0] = all, [1] = primary, [2] = secondary...
            monitor_count = len(monitors) - 1  # -1 car [0] = tous

            if self.monitor_idx > monitor_count:
                logger.warning(f"Moniteur {self.monitor_idx} inexistant, utilisation du monitor 1")
                self.monitor_idx = 1

            monitor = monitors[self.monitor_idx]
            logger.info(f"Moniteur sélectionné : {monitor}")

            while self._running:
                t_start = time.perf_counter()

                try:
                    frame = self._capture_one_frame(sct, monitor)
                    if frame and (frame.changed or not self.detect_changes):
                        with self._lock:
                            self._queue.append(frame)
                        self._fps_history.append(time.time())
                        self._bw_history.append(frame.size_bytes)
                        self._total_frames += 1
                        self._new_frame.set()

                        # Adaptation qualité
                        if self.adaptive_quality:
                            self._adapt_quality()
                    else:
                        self._skipped += 1

                except Exception as e:
                    logger.error(f"Erreur capture frame : {e}")
                    time.sleep(0.1)

                # Respect du FPS cible
                elapsed  = time.perf_counter() - t_start
                interval = 1.0 / self.fps
                sleep    = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)

        logger.info("Boucle de capture terminée")

    def _capture_one_frame(self, sct, monitor: dict) -> FrameData | None:
        """
        Capture et encode un frame.
        Retourne None si le frame est identique au précédent.
        """
        from PIL import Image

        # Capture brute via mss (très rapide — partage mémoire)
        screenshot = sct.grab(monitor)

        # Convertir en PIL Image (RGB)
        img = Image.frombytes(
            "RGB",
            (screenshot.width, screenshot.height),
            screenshot.rgb,
        )

        # Downscale si nécessaire
        w, h = img.size
        if w > MAX_WIDTH or h > MAX_HEIGHT or self.scale < 1.0:
            target_w = min(int(w * self.scale), MAX_WIDTH)
            target_h = min(int(h * self.scale), MAX_HEIGHT)
            # Garder le ratio
            ratio = min(target_w / w, target_h / h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        self._frame_id += 1
        w, h = img.size

        # Détection de changement (hash MD5 sur image downscalée)
        changed = True
        if self.detect_changes:
            # Utiliser une version très réduite pour le hash (rapide)
            thumb = img.resize((64, 36), Image.NEAREST)
            frame_hash = hashlib.md5(thumb.tobytes()).hexdigest()
            changed = frame_hash != self._last_hash
            self._last_hash = frame_hash

            if not changed:
                return None

        # Compression JPEG
        buf = io.BytesIO()
        img.save(
            buf,
            format="JPEG",
            quality=self.quality,
            optimize=False,         # optimize=True est lent
            progressive=False,
        )
        jpeg_bytes = buf.getvalue()

        if self.adaptive_quality and len(jpeg_bytes) > MAX_FRAME_SOFT_BYTES and self.quality > MIN_QUALITY:
            self.quality = max(MIN_QUALITY, self.quality - 5)

        return FrameData(
            jpeg_bytes=jpeg_bytes,
            width=w,
            height=h,
            frame_id=self._frame_id,
            changed=changed,
        )

    def _adapt_quality(self):
        """
        Qualité adaptative :
        - Si FPS réel < 60% du FPS cible → baisser la qualité
        - Si FPS réel >= 90% du FPS cible → remonter doucement
        """
        now     = time.time()
        recent  = [t for t in self._fps_history if now - t < 2.0]
        fps_now = len(recent) / 2.0

        target   = self.fps
        ratio    = fps_now / target if target else 1.0

        if ratio < 0.6 and self.quality > MIN_QUALITY:
            self.quality = max(MIN_QUALITY, self.quality - 5)
            logger.debug(f"Qualité baissée → {self.quality}% (FPS réel {fps_now:.1f}/{target})")
        elif ratio >= 0.9 and self.quality < MAX_QUALITY:
            self.quality = min(MAX_QUALITY, self.quality + 2)

    # ── Utilitaires ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_mss() -> bool:
        try:
            import mss
            return True
        except ImportError:
            logger.warning("mss non installé — pip install mss")
            return False

    @staticmethod
    def _check_pil() -> bool:
        try:
            from PIL import Image
            return True
        except ImportError:
            logger.warning("Pillow non installé — pip install pillow")
            return False

    @staticmethod
    def _check_numpy() -> bool:
        try:
            import numpy
            return True
        except ImportError:
            return False

    @staticmethod
    def list_monitors() -> dict:
        """Liste les moniteurs disponibles."""
        try:
            import mss
            with mss.mss() as sct:
                monitors = sct.monitors
                return {
                    "success": True,
                    "monitors": [
                        {
                            "index":  i,
                            "left":   m["left"],
                            "top":    m["top"],
                            "width":  m["width"],
                            "height": m["height"],
                            "label":  "Tous les écrans" if i == 0 else f"Écran {i}",
                        }
                        for i, m in enumerate(monitors)
                    ],
                    "count": len(monitors) - 1,
                }
        except Exception as e:
            return {"success": False, "message": f"Impossible de lister les moniteurs : {e}"}


# ── Singleton global (partagé avec jarvis_bridge.py) ─────────────────────────
_capture_instance: ScreenCapture | None = None
_capture_lock = threading.Lock()


def get_capture(fps: int = DEFAULT_FPS, quality: int = DEFAULT_QUALITY) -> ScreenCapture:
    """Retourne le singleton ScreenCapture (ou en crée un nouveau)."""
    global _capture_instance
    with _capture_lock:
        if _capture_instance is None:
            _capture_instance = ScreenCapture(fps=fps, quality=quality)
    return _capture_instance


def reset_capture():
    """Réinitialise le singleton (utile pour changer FPS/qualité)."""
    global _capture_instance
    with _capture_lock:
        if _capture_instance and _capture_instance._running:
            _capture_instance.stop()
        _capture_instance = None