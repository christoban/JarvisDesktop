"""
modules/music — Module Musique Jarvis
Semaine 3 — Bibliothèque + Playlists + VLC

Composants :
  MusicManager    — bibliothèque JSON, scan, recherche, recommandations
  PlaylistManager — CRUD playlists persistantes
  VLCController   — contrôle VLC Media Player via python-vlc
"""

from .music_manager    import MusicManager
from .playlist_manager import PlaylistManager
from .vlc_controller   import VLCController

__all__ = ["MusicManager", "PlaylistManager", "VLCController"]
