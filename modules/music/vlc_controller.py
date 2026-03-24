"""
modules/music/vlc_controller.py — Contrôle VLC Media Player
=============================================================

Utilise python-vlc pour piloter VLC directement en mémoire,
sans ouvrir l'interface graphique (headless optionnel).

Installation : pip install python-vlc
VLC requis    : installé à C:/Program Files/VideoLAN/VLC/

Fonctionnalités :
  - Lire un fichier ou une liste de fichiers
  - Pause / Resume / Stop
  - Suivant / Précédent
  - Volume (0-100)
  - Shuffle / Repeat
  - État courant (titre, durée, position, volume)
  - Détection si VLC est disponible

Architecture :
  Un seul MediaPlayer VLC est maintenu en singleton dans l'instance.
  La MediaList gère la playlist courante.
  Les commandes sont thread-safe via un verrou simple.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from config.logger import get_logger

logger = get_logger(__name__)

# ── Tentative d'import python-vlc ────────────────────────────────────────────
try:
    import vlc as _vlc
    _VLC_AVAILABLE = True
except ImportError:
    _vlc = None
    _VLC_AVAILABLE = False
    logger.warning("python-vlc non installé. Exécute : pip install python-vlc")
except Exception as e:
    _vlc = None
    _VLC_AVAILABLE = False
    logger.warning(f"python-vlc import échoué : {e}")


class VLCController:
    """
    Contrôle VLC Media Player via python-vlc.
    Toutes les méthodes retournent { success, message, data }.
    """

    def __init__(self):
        self._lock   = threading.Lock()
        self._instance     = None
        self._player       = None
        self._list_player  = None
        self._media_list   = None
        self._current_playlist: list[str] = []
        self._shuffle  = False
        self._repeat   = False
        self._volume   = 70
        self._init_vlc()

    def _init_vlc(self):
        if not _VLC_AVAILABLE:
            logger.warning("VLCController : python-vlc indisponible.")
            return
        try:
            # Instance VLC silencieuse (pas de fenêtre)
            self._instance    = _vlc.Instance("--no-xlib", "--quiet")
            self._player      = self._instance.media_player_new()
            self._media_list  = self._instance.media_list_new()
            self._list_player = self._instance.media_list_player_new()
            self._list_player.set_media_player(self._player)
            self._list_player.set_media_list(self._media_list)
            # Volume initial
            self._player.audio_set_volume(self._volume)
            logger.info("VLCController initialisé.")
        except Exception as e:
            logger.error(f"VLCController init échoué : {e}")
            self._instance = None

    # ── API publique ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return _VLC_AVAILABLE and self._instance is not None

    def play_file(self, path: str) -> dict:
        """Joue un fichier audio unique."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        p = Path(path)
        if not p.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        with self._lock:
            try:
                # Assure une transition propre: arrêter toute lecture en cours
                # (playlist précédente ou fichier isolé) avant de charger le nouveau média.
                self._hard_stop_locked()
                media = self._instance.media_new(str(p))
                self._player.set_media(media)
                self._player.play()
                self._current_playlist = [str(p)]
                logger.info(f"VLC lecture : {p.name}")
                return self._ok(
                    f"Lecture : '{p.name}'",
                    {"file": str(p), "name": p.name, "action": "play"}
                )
            except Exception as e:
                return self._err(f"Erreur lecture VLC : {e}")

    def play_playlist(self, paths: list[str]) -> dict:
        """Joue une liste de fichiers audio en ordre."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        if not paths:
            return self._err("Liste de fichiers vide.")

        valid = [p for p in paths if Path(p).exists()]
        if not valid:
            return self._err("Aucun fichier trouvé parmi les chemins fournis.")

        with self._lock:
            try:
                # Transition propre: couper la lecture active avant de remplacer la liste.
                self._hard_stop_locked()

                # Reconstruire la MediaList
                self._media_list.lock()
                while self._media_list.count() > 0:
                    self._media_list.remove_index(0)
                for path in valid:
                    media = self._instance.media_new(str(path))
                    self._media_list.add_media(media)
                self._media_list.unlock()

                # Configurer shuffle/repeat
                if self._shuffle:
                    self._list_player.set_playback_mode(_vlc.PlaybackMode.loop)
                elif self._repeat:
                    self._list_player.set_playback_mode(_vlc.PlaybackMode.loop)
                else:
                    self._list_player.set_playback_mode(_vlc.PlaybackMode.default)

                self._list_player.play()
                self._current_playlist = valid
                logger.info(f"VLC playlist : {len(valid)} fichier(s)")
                return self._ok(
                    f"Lecture de {len(valid)} morceau(x).",
                    {
                        "count":    len(valid),
                        "first":    Path(valid[0]).name,
                        "action":   "play_playlist",
                        "shuffle":  self._shuffle,
                        "repeat":   self._repeat,
                    }
                )
            except Exception as e:
                return self._err(f"Erreur playlist VLC : {e}")

    def pause(self) -> dict:
        """Pause ou reprise (toggle)."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            try:
                state = self._player.get_state()
                if state == _vlc.State.Playing:
                    self._player.pause()
                    return self._ok("Pause.", {"action": "pause"})
                elif state in (_vlc.State.Paused, _vlc.State.Stopped):
                    self._player.play()
                    return self._ok("Lecture reprise.", {"action": "resume"})
                return self._ok("Aucune lecture en cours.", {"action": "none"})
            except Exception as e:
                return self._err(f"Erreur pause VLC : {e}")

    def resume(self) -> dict:
        """Reprend la lecture si en pause."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            try:
                state = self._player.get_state()
                if state == _vlc.State.Paused:
                    self._player.play()
                    return self._ok("Lecture reprise.", {"action": "resume"})
                elif state == _vlc.State.Playing:
                    return self._ok("Déjà en lecture.", {"action": "already_playing"})
                return self._ok("Aucune lecture en cours.", {"action": "none"})
            except Exception as e:
                return self._err(f"Erreur resume VLC : {e}")

    def stop(self) -> dict:
        """Arrête complètement la lecture."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            try:
                self._hard_stop_locked()
                return self._ok("Lecture arrêtée.", {"action": "stop"})
            except Exception as e:
                return self._err(f"Erreur stop VLC : {e}")

    def next_track(self) -> dict:
        """Passe au morceau suivant."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            try:
                self._list_player.next()
                time.sleep(0.2)
                name = self._get_current_name()
                return self._ok(
                    f"Piste suivante{' : ' + name if name else '.'}",
                    {"action": "next", "current": name}
                )
            except Exception as e:
                return self._err(f"Erreur next VLC : {e}")

    def prev_track(self) -> dict:
        """Revient au morceau précédent."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            try:
                self._list_player.previous()
                time.sleep(0.2)
                name = self._get_current_name()
                return self._ok(
                    f"Piste précédente{' : ' + name if name else '.'}",
                    {"action": "prev", "current": name}
                )
            except Exception as e:
                return self._err(f"Erreur prev VLC : {e}")

    def set_volume(self, level: int) -> dict:
        """Règle le volume (0-100)."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        level = max(0, min(100, int(level)))
        with self._lock:
            try:
                self._player.audio_set_volume(level)
                self._volume = level
                return self._ok(f"Volume VLC : {level}%.", {"volume": level, "action": "volume"})
            except Exception as e:
                return self._err(f"Erreur volume VLC : {e}")

    def toggle_shuffle(self) -> dict:
        """Active/désactive la lecture aléatoire."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            self._shuffle = not self._shuffle
            state = "activé" if self._shuffle else "désactivé"
            return self._ok(f"Mode aléatoire {state}.", {"shuffle": self._shuffle})

    def toggle_repeat(self) -> dict:
        """Active/désactive la répétition."""
        if not self.is_available():
            return self._err("VLC non disponible.")
        with self._lock:
            self._repeat = not self._repeat
            try:
                mode = _vlc.PlaybackMode.loop if self._repeat else _vlc.PlaybackMode.default
                self._list_player.set_playback_mode(mode)
            except Exception:
                pass
            state = "activée" if self._repeat else "désactivée"
            return self._ok(f"Répétition {state}.", {"repeat": self._repeat})

    def get_status(self) -> dict:
        """Retourne l'état complet de VLC."""
        if not self.is_available():
            return self._ok("VLC non disponible.", {
                "playing": False, "available": False
            })
        with self._lock:
            try:
                state   = self._player.get_state()
                playing = state == _vlc.State.Playing
                paused  = state == _vlc.State.Paused

                # Titre du morceau courant
                name     = self._get_current_name()
                duration = self._player.get_length()   # ms
                position = self._player.get_time()     # ms
                volume   = self._player.audio_get_volume()

                state_str = {
                    _vlc.State.Playing:  "playing",
                    _vlc.State.Paused:   "paused",
                    _vlc.State.Stopped:  "stopped",
                    _vlc.State.Ended:    "ended",
                    _vlc.State.Error:    "error",
                }.get(state, "unknown")

                return self._ok(
                    f"VLC : {state_str}" + (f" — {name}" if name else ""),
                    {
                        "state":       state_str,
                        "playing":     playing,
                        "paused":      paused,
                        "current":     name,
                        "duration_ms": duration,
                        "position_ms": position,
                        "volume":      volume,
                        "shuffle":     self._shuffle,
                        "repeat":      self._repeat,
                        "playlist_count": len(self._current_playlist),
                        "available":   True,
                    }
                )
            except Exception as e:
                return self._err(f"Erreur status VLC : {e}")

    def current_song(self) -> dict:
        """Retourne le morceau en cours."""
        status = self.get_status()
        if not status["success"]:
            return status
        data = status.get("data") or {}
        name = data.get("current")
        if not name:
            return self._ok("Aucune musique en cours.", {"current": None, "playing": False})
        return self._ok(
            f"En cours : '{name}'",
            {
                "current":     name,
                "playing":     data.get("playing", False),
                "duration_ms": data.get("duration_ms"),
                "position_ms": data.get("position_ms"),
                "volume":      data.get("volume"),
            }
        )

    # ── Helpers privés ────────────────────────────────────────────────────────

    def _hard_stop_locked(self):
        """Stoppe de façon robuste toutes les voies de lecture VLC.

        Méthode appelée sous verrou uniquement.
        """
        try:
            self._list_player.stop()
        except Exception:
            pass
        try:
            self._player.stop()
        except Exception:
            pass
        # Petite latence pour laisser VLC basculer d'état avant rechargement.
        time.sleep(0.05)

    def _get_current_name(self) -> str:
        """Retourne le nom du morceau courant (sans verrou — appelé dans verrou)."""
        try:
            media = self._player.get_media()
            if media is None:
                return ""
            mrl = media.get_mrl() or ""
            # Décoder le chemin depuis l'URL file:///...
            from urllib.parse import unquote
            path = unquote(mrl.replace("file:///", "").replace("file://", ""))
            return Path(path).name if path else ""
        except Exception:
            return ""

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}
