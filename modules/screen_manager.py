"""
screen_manager.py — Contrôle de l'écran
Capture d'écran, envoi vers téléphone, enregistrement.

⚠️  IMPLÉMENTATION COMPLÈTE : Semaine 9
"""

import base64
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from config.logger import get_logger
from config.settings import BASE_DIR

logger = get_logger(__name__)

SCREENSHOTS_DIR = BASE_DIR / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_BYTES = 2 * 1024 * 1024


try:
    import mss  # type: ignore
    import mss.tools  # type: ignore
    _MSS_AVAILABLE = True
except Exception:
    _MSS_AVAILABLE = False

try:
    from PIL import Image, ImageGrab  # type: ignore
    _PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageGrab = None
    _PIL_AVAILABLE = False


class ScreenManager:

    def __init__(self):
        self.SYSTEM = platform.system().lower()
        self.backend = "stub"

        if _MSS_AVAILABLE:
            self.backend = "mss"
        elif _PIL_AVAILABLE and "linux" not in self.SYSTEM:
            self.backend = "pil"
        elif "linux" in self.SYSTEM and (shutil.which("scrot") or shutil.which("import")):
            self.backend = "scrot"

        self.screenshots_dir = SCREENSHOTS_DIR
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"ScreenManager initialisé — backend={self.backend}")

    def capture_screen(self, send_to_phone: bool = False, monitor: int = 1) -> dict:
        """Capture PNG, encode en base64 et optionnellement envoie au telephone."""
        if self.backend == "stub":
            return self._err("Aucun backend capture disponible (mss/PIL/scrot).")

        filename = f"screenshot_{int(time.time())}.png"
        filepath = self.screenshots_dir / filename

        if self.backend == "mss":
            ok, err = self._capture_mss(filepath, monitor=monitor)
        elif self.backend == "pil":
            ok, err = self._capture_pil(filepath)
        else:
            ok, err = self._capture_scrot(filepath)

        if not ok:
            return self._err(f"Capture ecran echouee: {err}")

        self._compress_image(filepath, max_size=MAX_IMAGE_BYTES)
        img_b64 = self._encode_b64(filepath)
        if img_b64 is None:
            return self._err("Impossible d'encoder la capture en base64.")

        size_kb = round(filepath.stat().st_size / 1024, 1)
        data = {
            "path": str(filepath),
            "filename": filepath.name,
            "screenshots_dir": str(self.screenshots_dir),
            "base64": img_b64,
            "size_kb": size_kb,
            "backend": self.backend,
        }

        message = f"Capture ecran enregistree dans {filepath}."
        if send_to_phone:
            notif = self.send_screenshot_to_phone(str(filepath))
            data["notification"] = notif
            if notif.get("success"):
                message = (
                    f"Capture ecran enregistree dans {filepath} et envoyee dans Jarvis Mobile > Historique."
                )
            else:
                message = (
                    f"Capture ecran enregistree dans {filepath}, mais l'envoi au telephone a echoue."
                )

        return self._ok(message, data)

    def record_screen(self, duration: int = 30) -> dict:
        """Stub simple pour enregistrement d'ecran."""
        return self._err(f"Enregistrement ecran non implemente (duree demandee: {duration}s).")

    def send_screenshot_to_phone(self, image_path: str = "", share_mode: str = "") -> dict:
        """Envoie une capture via NotificationSender (bridge/azure)."""
        path = Path(image_path) if image_path else None

        if path and path.exists():
            filepath = path
        else:
            capture = self.capture_screen(send_to_phone=False)
            if not capture.get("success"):
                return capture
            filepath = Path(capture["data"]["path"])

        img_b64 = self._encode_b64(filepath)
        if img_b64 is None:
            return self._err("Impossible d'encoder la capture avant envoi.")

        try:
            from communication.notification_sender import NotificationSender
            sender = NotificationSender()
            sent = sender.notify_screenshot(path=str(filepath), b64=img_b64)
            destination = "Jarvis Mobile > Historique"
            if share_mode == "share":
                message = (
                    "Partage d'ecran direct non implemente : une capture fixe a ete envoyee "
                    f"dans {destination}."
                )
            else:
                message = f"Capture envoyee au telephone dans {destination}."

            return self._ok(message, {
                "path": str(filepath),
                "filename": filepath.name,
                "destination": destination,
                "share_mode": share_mode or "snapshot",
                "notification": sent,
            })
        except Exception as e:
            return self._err(f"Echec envoi capture au telephone: {e}")

    def get_screen_info(self) -> dict:
        """Retourne la liste des moniteurs (id/width/height/left/top)."""
        last_error = ""
        if _MSS_AVAILABLE:
            try:
                screens = []
                with mss.mss() as sct:
                    # monitors[0] = ecran virtuel total, on ignore.
                    for idx, mon in enumerate(sct.monitors[1:], start=1):
                        screens.append({
                            "id": idx,
                            "width": int(mon.get("width", 0)),
                            "height": int(mon.get("height", 0)),
                            "left": int(mon.get("left", 0)),
                            "top": int(mon.get("top", 0)),
                            "resolution": f"{int(mon.get('width', 0))}x{int(mon.get('height', 0))}",
                        })
                summary = ", ".join(s["resolution"] for s in screens[:3])
                return self._ok(f"{len(screens)} ecran(s) detecte(s) : {summary}.", {"screens": screens})
            except Exception as e:
                last_error = str(e)

        if _PIL_AVAILABLE and ImageGrab is not None:
            try:
                img = ImageGrab.grab()
                w, h = img.size
                return self._ok(
                    f"1 ecran detecte : {w}x{h}.",
                    {"screens": [{"id": 1, "width": w, "height": h, "left": 0, "top": 0, "resolution": f"{w}x{h}"}]},
                )
            except Exception as e:
                last_error = str(e)

        details = f" Derniere erreur: {last_error}" if last_error else ""
        return self._err(f"Impossible de detecter les ecrans avec le backend actuel.{details}")

    def set_brightness(self, level: int) -> dict:
        """Regle la luminosite (0-100)."""
        level = max(0, min(100, int(level)))

        if "windows" in self.SYSTEM:
            ps = (
                "$b = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods;"
                f"$b | ForEach-Object {{ $_.WmiSetBrightness(1,{level}) }}"
            )
            ok, out = self._run(["powershell", "-NoProfile", "-Command", ps], timeout=20)
            if not ok:
                return self._err(f"Reglage luminosite echoue: {out}")
            return self._ok(f"Luminosite reglee a {level}%.", {"level": level})

        if "linux" in self.SYSTEM:
            value = max(0.1, min(1.0, level / 100.0))
            ok_out, out = self._run(["xrandr", "--query"])
            if not ok_out:
                return self._err(f"xrandr indisponible: {out}")
            output_name = ""
            for line in out.splitlines():
                if " connected" in line:
                    output_name = line.split()[0]
                    break
            if not output_name:
                return self._err("Aucune sortie ecran active trouvee via xrandr.")
            ok, out = self._run(["xrandr", "--output", output_name, "--brightness", str(value)])
            if not ok:
                return self._err(f"Reglage luminosite echoue: {out}")
            return self._ok(f"Luminosite reglee a {level}%.", {"level": level, "output": output_name})

        return self._err("Reglage luminosite non supporte sur ce systeme.")

    def lock_screen(self) -> dict:
        """Verrouille l'ecran selon l'OS."""
        if "windows" in self.SYSTEM:
            ok, out = self._run(["rundll32.exe", "user32.dll,LockWorkStation"])
            if not ok:
                return self._err(f"Verrouillage ecran echoue: {out}")
            return self._ok("Ecran verrouille.")

        if "linux" in self.SYSTEM:
            attempts = [
                ["gnome-screensaver-command", "-l"],
                ["xscreensaver-command", "-lock"],
                ["loginctl", "lock-session"],
            ]
            for cmd in attempts:
                ok, _ = self._run(cmd)
                if ok:
                    return self._ok("Ecran verrouille.")
            return self._err("Impossible de verrouiller l'ecran sur Linux.")

        return self._err("Verrouillage ecran non supporte sur ce systeme.")

    def health_check(self) -> dict:
        """Etat rapide du module ecran pour monitoring/tests."""
        return self._ok("ScreenManager OK.", {
            "backend": self.backend,
            "system": self.SYSTEM,
            "screenshots_dir": str(self.screenshots_dir),
            "screenshots_dir_exists": self.screenshots_dir.exists(),
            "capture_available": self.backend != "stub",
        })

    def _capture_mss(self, filepath: Path, monitor: int = 1) -> tuple[bool, str]:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if len(monitors) <= 1:
                    return False, "Aucun moniteur detecte"
                idx = max(1, min(int(monitor), len(monitors) - 1))
                shot = sct.grab(monitors[idx])
                mss.tools.to_png(shot.rgb, shot.size, output=str(filepath))
            return True, ""
        except Exception as e:
            return False, str(e)

    def _capture_pil(self, filepath: Path) -> tuple[bool, str]:
        if not _PIL_AVAILABLE or ImageGrab is None:
            return False, "PIL ImageGrab non disponible"
        try:
            img = ImageGrab.grab()
            img.save(str(filepath), format="PNG")
            return True, ""
        except Exception as e:
            return False, str(e)

    def _capture_scrot(self, filepath: Path) -> tuple[bool, str]:
        if shutil.which("scrot"):
            ok, out = self._run(["scrot", str(filepath)])
            if ok:
                return True, ""

        if shutil.which("import"):
            ok, out = self._run(["import", "-window", "root", str(filepath)])
            if ok:
                return True, ""

        return False, out if 'out' in locals() else "Ni scrot ni import disponibles"

    def _compress_image(self, filepath: Path, max_size: int = MAX_IMAGE_BYTES):
        try:
            if filepath.stat().st_size <= max_size:
                return
            if not _PIL_AVAILABLE or Image is None:
                return

            with Image.open(filepath) as img:
                img.thumbnail((800, 800))
                img.save(filepath, format="PNG", optimize=True)
        except Exception as e:
            logger.warning(f"Compression capture ignoree: {e}")

    @staticmethod
    def _encode_b64(filepath: Path) -> str | None:
        try:
            return base64.b64encode(filepath.read_bytes()).decode("utf-8")
        except Exception:
            return None

    @staticmethod
    def _run(cmd, timeout: int = 15) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
                shell=False,
            )
            output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            return proc.returncode == 0, output.strip()
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}