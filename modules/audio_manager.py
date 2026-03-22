"""
audio_manager.py — Contrôle audio et musique
Volume +/-, mute/unmute, lecture musique locale, informations audio.

SEMAINE 5 — MARDI — IMPLÉMENTATION COMPLÈTE

Backends audio (priorité décroissante) :
  Windows : pycaw (API Windows Core Audio) → commandes nircmd → PowerShell
  Linux   : pactl (PulseAudio) → amixer (ALSA)
  macOS   : osascript AppleScript

CORRECTIONS SEMAINE 1 :
  [B1] Suppression de la duplication de _adjust_volume_pycaw.
  [B2] Correction de _get_volume_pycaw, _set_volume_pycaw, _toggle_mute_pycaw
       pour utiliser la vraie API pycaw (cast + POINTER + IAudioEndpointVolume).

CORRECTION [B3] — COM Threading :
  pycaw utilise l'API COM de Windows. COM doit être initialisé dans CHAQUE
  thread qui l'utilise. Jarvis tourne en multi-thread (bridge HTTP) → les
  appels pycaw depuis les threads secondaires échouaient avec une exception
  "CoInitialize has not been called" → success=False malgré que le volume
  changeait parfois.

  Fix : chaque méthode pycaw appelle pythoncom.CoInitialize() avant et
  pythoncom.CoUninitialize() dans un finally. Si pythoncom n'est pas
  disponible, on continue sans (Windows gère parfois ça seul).
"""

import os
import re
import glob
import subprocess
import platform
import sys
import shutil
from pathlib import Path
from config.logger import get_logger


logger = get_logger(__name__)

SYSTEM = platform.system()   # "Windows" | "Linux" | "Darwin"

# Extensions de fichiers musicaux supportées
MUSIC_EXTENSIONS = [".mp3", ".flac", ".wav", ".ogg", ".aac", ".m4a", ".wma", ".opus"]

# Dossiers de recherche musique par défaut
DEFAULT_MUSIC_DIRS = [
    Path.home() / "Music",
    Path.home() / "Musique",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Téléchargements",
]


# ── Détection des backends disponibles ───────────────────────────────────────

def _has_pycaw() -> bool:
    try:
        import pycaw  # noqa
        return True
    except ImportError:
        return False

def _has_pactl() -> bool:
    return shutil.which("pactl") is not None

def _has_amixer() -> bool:
    return shutil.which("amixer") is not None

def _run(cmd: list, timeout: int = 5) -> tuple[bool, str]:
    """Lance une commande shell et retourne (succès, stdout)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, ""


def _coinit():
    """
    [B3] Initialise COM pour le thread courant.
    Retourne True si CoInitialize a été appelé (et doit être suivi de CoUninitialize).
    Silencieux si pythoncom absent ou déjà initialisé.
    """
    try:
        import pythoncom
        pythoncom.CoInitialize()
        return True
    except Exception:
        return False


def _couninit(did_init: bool):
    """[B3] Libère COM si on l'a initialisé."""
    if not did_init:
        return
    try:
        import pythoncom
        pythoncom.CoUninitialize()
    except Exception:
        pass


class AudioManager:
    """
    Contrôle audio multi-plateforme.
    Toutes les méthodes retournent { "success": bool, "message": str, "data": dict | None }
    """

    def __init__(self):
        self._pycaw_available = _has_pycaw() and SYSTEM == "Windows"
        self._pactl_available = _has_pactl()
        self._amixer_available = _has_amixer()
        self._muted = False   # État local pour toggle mute

        backend = (
            "pycaw"   if self._pycaw_available   else
            "pactl"   if self._pactl_available   else
            "amixer"  if self._amixer_available  else
            "system"
        )
        logger.info(f"AudioManager initialisé — backend={backend} système={SYSTEM}")

    # ══════════════════════════════════════════════════════════════════════════
    #  VOLUME
    # ══════════════════════════════════════════════════════════════════════════

    def volume_up(self, step: int = 10) -> dict:
        step = max(1, min(step, 100))
        logger.info(f"Volume + {step}%")
        return self._adjust_volume(delta=+step)

    def volume_down(self, step: int = 10) -> dict:
        step = max(1, min(step, 100))
        logger.info(f"Volume - {step}%")
        return self._adjust_volume(delta=-step)

    def set_volume(self, level: int) -> dict:
        level = max(0, min(level, 100))
        logger.info(f"Volume -> {level}%")
        return self._set_volume_absolute(level)

    def get_volume(self) -> dict:
        logger.info("Lecture volume actuel")
        if self._pycaw_available:
            return self._get_volume_pycaw()
        if self._pactl_available:
            return self._get_volume_pactl()
        if self._amixer_available:
            return self._get_volume_amixer()
        if SYSTEM == "Darwin":
            return self._get_volume_macos()
        return self._ok("Volume actuel : inconnu (aucun backend disponible).",
                        {"level": -1, "backend": "none"})

    def mute(self) -> dict:
        logger.info("Toggle mute")
        if self._pycaw_available:
            return self._toggle_mute_pycaw()
        if self._pactl_available:
            return self._toggle_mute_pactl()
        if self._amixer_available:
            return self._toggle_mute_amixer()
        if SYSTEM == "Darwin":
            return self._toggle_mute_macos()
        if SYSTEM == "Windows":
            return self._toggle_mute_powershell()
        return self._err("Aucun backend audio disponible pour muter.")

    # ══════════════════════════════════════════════════════════════════════════
    #  BACKENDS VOLUME — Windows (pycaw)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_pycaw_endpoint_volume():
        """
        Retourne l'endpoint volume pycaw en mode compatible multi-versions.
        Certaines versions exposent `EndpointVolume`, d'autres nécessitent
        l'appel COM `Activate`.
        """
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()

        # pycaw récent: AudioDevice avec propriété EndpointVolume.
        endpoint = getattr(devices, "EndpointVolume", None)
        if endpoint is not None:
            return endpoint

        # pycaw legacy: IMMDevice avec Activate.
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def _adjust_volume_pycaw(self, delta: int) -> dict:
        """
        [B1] Version unique avec la vraie API pycaw.
        [B3] CoInitialize/CoUninitialize pour compatibilité multi-thread.
        """
        did_init = _coinit()
        try:
            volume = self._get_pycaw_endpoint_volume()
            current = round(volume.GetMasterVolumeLevelScalar() * 100)
            new_level = max(0, min(100, current + delta))
            volume.SetMasterVolumeLevelScalar(new_level / 100, None)
            action = "augmenté" if delta > 0 else "diminué"
            return self._ok(
                f"Volume {action} : {current}% → {new_level}%",
                {"old": current, "new": new_level, "delta": delta, "backend": "pycaw"}
            )
        except Exception as e:
            return self._err(f"pycaw erreur : {str(e)}")
        finally:
            _couninit(did_init)

    def _get_volume_pycaw(self) -> dict:
        """
        [B2] Vraie API pycaw avec cast + POINTER.
        [B3] CoInitialize/CoUninitialize pour compatibilité multi-thread.
        """
        did_init = _coinit()
        try:
            volume = self._get_pycaw_endpoint_volume()
            level = round(volume.GetMasterVolumeLevelScalar() * 100)
            muted = bool(volume.GetMute())
            return self._ok(f"Volume : {level}%", {"level": level, "muted": muted, "backend": "pycaw"})
        except Exception as e:
            return self._err(f"pycaw get_volume erreur : {str(e)}")
        finally:
            _couninit(did_init)

    def _set_volume_pycaw(self, level: int) -> dict:
        """
        [B2] Vraie API pycaw avec cast + POINTER.
        [B3] CoInitialize/CoUninitialize pour compatibilité multi-thread.
        """
        did_init = _coinit()
        try:
            volume = self._get_pycaw_endpoint_volume()
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            # Vérification : relire le volume pour confirmer le changement
            actual = round(volume.GetMasterVolumeLevelScalar() * 100)
            return self._ok(
                f"Volume réglé à {actual}%.",
                {"level": actual, "requested": level, "backend": "pycaw"}
            )
        except Exception as e:
            logger.warning(f"pycaw set_volume échoué : {e} — fallback PowerShell")
            # Fallback PowerShell si pycaw échoue vraiment
            return self._set_volume_powershell_direct(level)
        finally:
            _couninit(did_init)

    def _set_volume_powershell_direct(self, level: int) -> dict:
        """Fallback PowerShell pour régler le volume absolu."""
        script = (
            f"$vol = {level} / 100.0; "
            f"Add-Type -TypeDefinition '"
            f"using System.Runtime.InteropServices; "
            f"public class Vol {{"
            f"[DllImport(\"winmm.dll\")] public static extern int waveOutSetVolume(IntPtr h, uint v); "
            f"}}'; "
            f"$v = [uint32]($vol * 65535); "
            f"[Vol]::waveOutSetVolume([IntPtr]::Zero, ($v -bor ($v -shl 16)))"
        )
        ok, _ = _run(["powershell", "-Command", script])
        if ok:
            return self._ok(
                f"Volume réglé à {level}%.",
                {"level": level, "backend": "powershell_fallback"}
            )
        return self._err(f"Impossible de régler le volume à {level}%.")

    def _toggle_mute_pycaw(self) -> dict:
        """
        [B2] Vraie API pycaw avec cast + POINTER.
        [B3] CoInitialize/CoUninitialize pour compatibilité multi-thread.
        """
        did_init = _coinit()
        try:
            volume = self._get_pycaw_endpoint_volume()
            is_muted = bool(volume.GetMute())
            volume.SetMute(not is_muted, None)
            new_state = "coupé" if not is_muted else "rétabli"
            return self._ok(f"Son {new_state}.", {"muted": not is_muted, "backend": "pycaw"})
        except Exception as e:
            return self._err(f"pycaw mute erreur : {str(e)}")
        finally:
            _couninit(did_init)

    # ══════════════════════════════════════════════════════════════════════════
    #  BACKENDS VOLUME — Windows PowerShell (fallback)
    # ══════════════════════════════════════════════════════════════════════════

    def _adjust_volume_powershell(self, delta: int) -> dict:
        """Ajuste le volume via PowerShell (fallback si pycaw absent)."""
        get_script = (
            "$obj = New-Object -ComObject WScript.Shell; "
            "Add-Type -TypeDefinition '"
            "using System.Runtime.InteropServices; "
            "public class Audio { "
            "[DllImport(\"winmm.dll\")] public static extern int waveOutGetVolume(IntPtr h, out uint vol); "
            "}';"
            "$vol = 0; [Audio]::waveOutGetVolume([IntPtr]::Zero, [ref]$vol); "
            "$left = ($vol -band 0xFFFF) / 0xFFFF * 100; [int]$left"
        )
        ok_get, stdout = _run(["powershell", "-Command", get_script])
        current = int(stdout.strip()) if ok_get and stdout.strip().isdigit() else 50
        new_level = max(0, min(100, current + delta))

        set_script = (
            f"$wsh = New-Object -ComObject WScript.Shell; "
            f"$vol = [int]({new_level} * 655.35); "
            f"$combined = ($vol -bor ($vol -shl 16)); "
            f"Add-Type -TypeDefinition '"
            f"using System.Runtime.InteropServices; "
            f"public class Audio2 {{ "
            f"[DllImport(\"winmm.dll\")] public static extern int waveOutSetVolume(IntPtr h, uint v); "
            f"}}';"
            f"[Audio2]::waveOutSetVolume([IntPtr]::Zero, $combined)"
        )
        ok, _ = _run(["powershell", "-Command", set_script])
        if ok:
            action = "augmenté" if delta > 0 else "diminué"
            return self._ok(
                f"Volume {action} : {current}% → {new_level}%",
                {"old": current, "new": new_level, "backend": "powershell"}
            )
        return self._err("Impossible d'ajuster le volume via PowerShell.")

    def _toggle_mute_powershell(self) -> dict:
        """Toggle mute via PowerShell SendKeys."""
        script = (
            "$wsh = New-Object -ComObject WScript.Shell; "
            "$wsh.SendKeys([char]173)"
        )
        ok, _ = _run(["powershell", "-Command", script])
        if ok:
            self._muted = not self._muted
            state = "coupé" if self._muted else "rétabli"
            return self._ok(f"Son {state}.", {"muted": self._muted, "backend": "powershell"})
        return self._err("Impossible de toggler le mute via PowerShell.")

    # ══════════════════════════════════════════════════════════════════════════
    #  BACKENDS VOLUME — Linux PulseAudio (pactl)
    # ══════════════════════════════════════════════════════════════════════════

    def _adjust_volume_pactl(self, delta: int) -> dict:
        sign = "+" if delta >= 0 else ""
        ok, _ = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{delta}%"])
        if ok:
            vol_result = self._get_volume_pactl()
            new_level  = vol_result["data"].get("level", "?") if vol_result["success"] else "?"
            action = "augmenté" if delta > 0 else "diminué"
            return self._ok(
                f"Volume {action} de {abs(delta)}% → {new_level}%",
                {"delta": delta, "new": new_level, "backend": "pactl"}
            )
        return self._err("pactl : impossible d'ajuster le volume.")

    def _set_volume_pactl(self, level: int) -> dict:
        ok, _ = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        if ok:
            return self._ok(f"Volume réglé à {level}%.", {"level": level, "backend": "pactl"})
        return self._err("pactl : impossible de régler le volume.")

    def _get_volume_pactl(self) -> dict:
        ok, stdout = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        if ok:
            match = re.search(r"(\d+)%", stdout)
            level = int(match.group(1)) if match else -1
            return self._ok(f"Volume : {level}%", {"level": level, "backend": "pactl"})
        return self._err("pactl : impossible de lire le volume.")

    def _toggle_mute_pactl(self) -> dict:
        ok, _ = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        if ok:
            ok2, stdout = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            is_muted = "yes" in stdout.lower() if ok2 else None
            state = "coupé" if is_muted else "rétabli"
            return self._ok(f"Son {state}.", {"muted": is_muted, "backend": "pactl"})
        return self._err("pactl : impossible de toggler le mute.")

    # ══════════════════════════════════════════════════════════════════════════
    #  BACKENDS VOLUME — Linux ALSA (amixer)
    # ══════════════════════════════════════════════════════════════════════════

    def _adjust_volume_amixer(self, delta: int) -> dict:
        sign = "+" if delta >= 0 else ""
        ok, _ = _run(["amixer", "-q", "sset", "Master", f"{sign}{delta}%"])
        if ok:
            action = "augmenté" if delta > 0 else "diminué"
            return self._ok(f"Volume {action} de {abs(delta)}%.", {"delta": delta, "backend": "amixer"})
        return self._err("amixer : impossible d'ajuster le volume.")

    def _get_volume_amixer(self) -> dict:
        ok, stdout = _run(["amixer", "get", "Master"])
        if ok:
            match = re.search(r"\[(\d+)%\]", stdout)
            level = int(match.group(1)) if match else -1
            return self._ok(f"Volume : {level}%", {"level": level, "backend": "amixer"})
        return self._err("amixer : impossible de lire le volume.")

    def _toggle_mute_amixer(self) -> dict:
        ok, _ = _run(["amixer", "-q", "sset", "Master", "toggle"])
        if ok:
            return self._ok("Son basculé (mute/unmute).", {"backend": "amixer"})
        return self._err("amixer : impossible de toggler le mute.")

    # ══════════════════════════════════════════════════════════════════════════
    #  BACKENDS VOLUME — macOS (osascript)
    # ══════════════════════════════════════════════════════════════════════════

    def _adjust_volume_macos(self, delta: int) -> dict:
        ok, stdout = _run(["osascript", "-e", "output volume of (get volume settings)"])
        current = int(stdout) if ok and stdout.isdigit() else 50
        new_level = max(0, min(100, current + delta))
        ok2, _ = _run(["osascript", "-e", f"set volume output volume {new_level}"])
        if ok2:
            action = "augmenté" if delta > 0 else "diminué"
            return self._ok(f"Volume {action} : {current}% → {new_level}%",
                            {"old": current, "new": new_level, "backend": "osascript"})
        return self._err("osascript : impossible d'ajuster le volume.")

    def _get_volume_macos(self) -> dict:
        ok, stdout = _run(["osascript", "-e", "output volume of (get volume settings)"])
        level = int(stdout) if ok and stdout.isdigit() else -1
        return self._ok(f"Volume : {level}%", {"level": level, "backend": "osascript"})

    def _toggle_mute_macos(self) -> dict:
        ok, stdout = _run(["osascript", "-e", "output muted of (get volume settings)"])
        is_muted = stdout.strip().lower() == "true"
        new_muted = not is_muted
        _run(["osascript", "-e", f"set volume output muted {str(new_muted).lower()}"])
        state = "coupé" if new_muted else "rétabli"
        return self._ok(f"Son {state}.", {"muted": new_muted, "backend": "osascript"})

    # ══════════════════════════════════════════════════════════════════════════
    #  DISPATCHER VOLUME
    # ══════════════════════════════════════════════════════════════════════════

    def _adjust_volume(self, delta: int) -> dict:
        if self._pycaw_available:
            return self._adjust_volume_pycaw(delta)
        if self._pactl_available:
            return self._adjust_volume_pactl(delta)
        if self._amixer_available:
            return self._adjust_volume_amixer(delta)
        if SYSTEM == "Darwin":
            return self._adjust_volume_macos(delta)
        if SYSTEM == "Windows":
            return self._adjust_volume_powershell(delta)
        return self._err(
            f"Aucun backend audio trouvé pour ajuster le volume "
            f"(système={SYSTEM}). Installe pycaw (Windows) ou pactl (Linux)."
        )

    def _set_volume_absolute(self, level: int) -> dict:
        if self._pycaw_available:
            return self._set_volume_pycaw(level)
        if self._pactl_available:
            return self._set_volume_pactl(level)
        if self._amixer_available:
            ok, _ = _run(["amixer", "-q", "sset", "Master", f"{level}%"])
            return self._ok(f"Volume → {level}%.", {"level": level, "backend": "amixer"}) if ok else self._err("Erreur amixer")
        if SYSTEM == "Darwin":
            ok, _ = _run(["osascript", "-e", f"set volume output volume {level}"])
            return self._ok(f"Volume → {level}%.", {"level": level, "backend": "osascript"}) if ok else self._err("Erreur osascript")
        if SYSTEM == "Windows":
            return self._set_volume_powershell_direct(level)
        return self._err("Aucun backend disponible pour régler le volume.")

    # ══════════════════════════════════════════════════════════════════════════
    #  LECTURE MUSIQUE
    # ══════════════════════════════════════════════════════════════════════════

    def play(self, query: str, music_dirs: list = None) -> dict:
        """
        Joue une musique locale en recherchant par nom/artiste.
        Ouvre avec l'application par défaut du système.
        Pour un contrôle avancé (playlists, VLC), voir le module music/ (semaine 3).
        """
        query = query.strip()
        if not query:
            return self._err("Précise le nom d'une chanson ou d'un artiste.")

        logger.info(f"Lecture musique : '{query}'")

        # Cas 1 : chemin absolu fourni directement
        p = Path(query)
        if p.is_absolute() and p.exists() and p.suffix.lower() in MUSIC_EXTENSIONS:
            return self._play_file(str(p))

        # Cas 2 : rechercher par nom dans les dossiers musicaux
        dirs = music_dirs or DEFAULT_MUSIC_DIRS
        found = self._search_music(query, dirs)

        if not found:
            return self._err(
                f"Aucun fichier musical trouvé pour '{query}'.\n"
                f"  Dossiers scannés : {', '.join(str(d) for d in dirs if Path(d).exists())}",
                {"query": query, "searched_dirs": [str(d) for d in dirs]}
            )

        return self._play_file(found[0], all_results=found)

    def play_file(self, path: str) -> dict:
        """Joue directement un fichier musical par son chemin."""
        p = Path(path)
        if not p.exists():
            return self._err(f"Fichier introuvable : '{path}'")
        if p.suffix.lower() not in MUSIC_EXTENSIONS:
            return self._err(
                f"Format non supporté : '{p.suffix}'. "
                f"Formats acceptés : {', '.join(MUSIC_EXTENSIONS)}"
            )
        return self._play_file(str(p))

    def list_music(self, music_dirs: list = None) -> dict:
        """Liste tous les fichiers musicaux trouvés dans les dossiers par défaut."""
        dirs  = music_dirs or DEFAULT_MUSIC_DIRS
        files = []
        for d in dirs:
            d = Path(d)
            if d.exists():
                for ext in MUSIC_EXTENSIONS:
                    files.extend(d.glob(f"*{ext}"))
                    files.extend(d.glob(f"*{ext.upper()}"))

        files = sorted(set(files), key=lambda f: f.name.lower())
        file_dicts = [
            {"name": f.name, "path": str(f),
             "size": f"{f.stat().st_size / 1024**2:.1f} MB"}
            for f in files
        ]

        if not file_dicts:
            return self._ok(
                "Aucun fichier musical trouvé dans les dossiers par défaut.",
                {"files": [], "count": 0}
            )

        lines = [f"{'TITRE':<50} TAILLE", "-" * 65]
        for fdict in file_dicts[:20]:
            lines.append(f"{fdict['name'][:49]:<50} {fdict['size']:>8}")
        if len(file_dicts) > 20:
            lines.append(f"  ... et {len(file_dicts) - 20} autre(s)")

        return self._ok(
            f"{len(file_dicts)} fichier(s) musical(aux) trouvé(s).",
            {"files": file_dicts, "count": len(file_dicts), "display": "\n".join(lines)}
        )

    def pause(self) -> dict:
        logger.info("Pause/Resume")
        return self._send_media_key("pause")

    def next_track(self) -> dict:
        logger.info("Piste suivante")
        return self._send_media_key("next")

    def prev_track(self) -> dict:
        logger.info("Piste précédente")
        return self._send_media_key("prev")

    def stop(self) -> dict:
        logger.info("Stop")
        return self._send_media_key("stop")

    # ══════════════════════════════════════════════════════════════════════════
    #  LECTURE MUSIQUE — helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _search_music(self, query: str, dirs: list) -> list:
        """Cherche des fichiers musicaux correspondant à la requête."""
        query_lower = query.lower()
        results = []
        for base_dir in dirs:
            d = Path(base_dir)
            if not d.exists():
                continue
            try:
                for ext in MUSIC_EXTENSIONS:
                    for f in d.rglob(f"*{ext}"):
                        if query_lower in f.stem.lower():
                            results.append(str(f))
                    for f in d.rglob(f"*{ext.upper()}"):
                        if query_lower in f.stem.lower():
                            results.append(str(f))
            except PermissionError:
                continue
        seen = set()
        unique = []
        for r in results:
            if r not in seen:
                seen.add(r)
                unique.append(r)
        return unique

    def _play_file(self, path: str, all_results: list = None) -> dict:
        """Lance la lecture d'un fichier audio avec l'application par défaut."""
        import platform as _plat
        system = _plat.system()
        try:
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            name = Path(path).name
            logger.info(f"Lecture lancée : {name}")
            data = {"file": path, "name": name}
            if all_results and len(all_results) > 1:
                data["other_matches"] = all_results[1:5]
            return self._ok(f"Lecture : '{name}'", data)

        except AttributeError:
            try:
                subprocess.Popen(["xdg-open", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._ok(f"Lecture : '{Path(path).name}'", {"file": path})
            except Exception as e:
                return self._err(f"Impossible de lire '{Path(path).name}' : {str(e)}")
        except Exception as e:
            return self._err(f"Impossible de lire '{Path(path).name}' : {str(e)}")

    def _send_media_key(self, action: str) -> dict:
        """Envoie une touche multimédia (pause, suivant, précédent, stop)."""
        if SYSTEM == "Windows":
            key_map = {"pause": 179, "next": 176, "prev": 177, "stop": 178}
            vk = key_map.get(action, 179)
            script = (
                f"Add-Type -TypeDefinition '"
                f"using System.Runtime.InteropServices; "
                f"public class MediaKey {{"
                f"[DllImport(\"user32.dll\")] public static extern void keybd_event(byte k, byte s, int f, int e); "
                f"}}'; "
                f"[MediaKey]::keybd_event({vk}, 0, 1, 0); "
                f"[MediaKey]::keybd_event({vk}, 0, 3, 0)"
            )
            ok, _ = _run(["powershell", "-Command", script])
            if ok:
                labels = {"pause": "Pause/Resume", "next": "Piste suivante",
                          "prev": "Piste précédente", "stop": "Arrêt"}
                return self._ok(f"{labels.get(action, action)} envoyé.",
                                {"action": action, "backend": "powershell"})
        elif SYSTEM == "Linux":
            key_map = {"pause": "XF86AudioPlay", "next": "XF86AudioNext",
                       "prev": "XF86AudioPrev", "stop": "XF86AudioStop"}
            ok, _ = _run(["xdotool", "key", key_map.get(action, "XF86AudioPlay")])
            if ok:
                return self._ok(f"Touche média '{action}' envoyée.",
                                {"action": action, "backend": "xdotool"})
        elif SYSTEM == "Darwin":
            script_map = {
                "pause": 'tell app "Music" to playpause',
                "next":  'tell app "Music" to next track',
                "prev":  'tell app "Music" to previous track',
                "stop":  'tell app "Music" to stop',
            }
            ok, _ = _run(["osascript", "-e", script_map.get(action, "")])
            if ok:
                return self._ok(f"Commande '{action}' envoyée à Music.",
                                {"action": action, "backend": "osascript"})

        return self._ok(
            f"Commande '{action}' envoyée (résultat non confirmé).",
            {"action": action, "note": "Utilisez votre lecteur multimédia"}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}