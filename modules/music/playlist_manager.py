"""
modules/music/playlist_manager.py — Gestionnaire de Playlists
==============================================================

Gère les playlists de Jarvis en JSON persistant.
Thread-safe, format cohérent avec le reste du projet.

Stockage : data/music/playlists.json

Structure :
{
  "chill": {
    "id": "chill",
    "name": "chill",
    "songs": [{"id":"abc", "title":"Lofi", "path":"C:/..."}],
    "created_at": 1234567890,
    "play_count": 5
  }
}
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from config.logger   import get_logger
from config.settings import BASE_DIR

logger = get_logger(__name__)

PLAYLISTS_FILE = BASE_DIR / "data" / "music" / "playlists.json"


class PlaylistManager:
    """
    CRUD sur les playlists Jarvis.
    Toutes les méthodes retournent { success, message, data }.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._playlists: dict = {}
        PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        logger.info(f"PlaylistManager — {len(self._playlists)} playlist(s) chargée(s).")

    # ── API publique ──────────────────────────────────────────────────────────

    def create_playlist(self, name: str) -> dict:
        """Crée une nouvelle playlist vide."""
        name = name.strip().lower()
        if not name:
            return self._err("Nom de playlist vide.")
        with self._lock:
            if name in self._playlists:
                return self._ok(
                    f"La playlist '{name}' existe déjà.",
                    {"name": name, "already_exists": True, **self._playlists[name]}
                )
            self._playlists[name] = {
                "id":         name,
                "name":       name,
                "songs":      [],
                "created_at": int(time.time()),
                "play_count": 0,
            }
            self._save()
        logger.info(f"Playlist '{name}' créée.")
        return self._ok(
            f"Playlist '{name}' créée.",
            {"name": name, "songs": [], "count": 0}
        )

    def delete_playlist(self, name: str) -> dict:
        """Supprime une playlist."""
        name = name.strip().lower()
        with self._lock:
            if name not in self._playlists:
                return self._err(f"Playlist '{name}' introuvable.")
            del self._playlists[name]
            self._save()
        return self._ok(f"Playlist '{name}' supprimée.", {"name": name})

    def list_playlists(self) -> dict:
        """Liste toutes les playlists."""
        with self._lock:
            playlists = list(self._playlists.values())

        result = sorted(
            [
                {
                    "id":         p["id"],
                    "name":       p["name"],
                    "count":      len(p.get("songs", [])),
                    "play_count": p.get("play_count", 0),
                    "created_at": p.get("created_at", 0),
                }
                for p in playlists
            ],
            key=lambda x: x["play_count"],
            reverse=True,
        )

        lines = [f"{'PLAYLIST':<20} {'TITRES':>7}  ÉCOUTES"]
        lines.append("─" * 40)
        for p in result:
            lines.append(f"{p['name']:<20} {p['count']:>7}  {p['play_count']}")

        return self._ok(
            f"{len(result)} playlist(s).",
            {"playlists": result, "count": len(result), "display": "\n".join(lines)}
        )

    def get_playlist(self, name: str) -> dict | None:
        """Retourne une playlist par nom (None si inexistante)."""
        name = name.strip().lower()
        with self._lock:
            # Correspondance exacte
            if name in self._playlists:
                return dict(self._playlists[name])
            # Correspondance partielle
            for key, pl in self._playlists.items():
                if name in key or key in name:
                    return dict(pl)
        return None

    def add_song(self, playlist_name: str, song: dict) -> dict:
        """
        Ajoute une chanson à une playlist.

        song doit avoir au minimum : {"id": str, "title": str, "path": str}
        """
        name = playlist_name.strip().lower()
        if not song.get("path"):
            return self._err("La chanson doit avoir un chemin (path).")

        with self._lock:
            if name not in self._playlists:
                # Créer la playlist automatiquement
                self._playlists[name] = {
                    "id": name, "name": name,
                    "songs": [], "created_at": int(time.time()), "play_count": 0,
                }

            songs = self._playlists[name]["songs"]
            # Éviter les doublons par path
            existing_paths = {s.get("path") for s in songs}
            if song["path"] in existing_paths:
                return self._ok(
                    f"'{song.get('title', song['path'])}' est déjà dans '{name}'.",
                    {"duplicate": True, "name": name}
                )
            songs.append({
                "id":     song.get("id", str(int(time.time()))),
                "title":  song.get("title", Path(song["path"]).stem),
                "artist": song.get("artist", ""),
                "album":  song.get("album", ""),
                "path":   song["path"],
                "added_at": int(time.time()),
            })
            self._save()

        return self._ok(
            f"'{song.get('title', Path(song['path']).stem)}' ajouté à '{name}'.",
            {"name": name, "count": len(self._playlists[name]["songs"])}
        )

    def remove_song(self, playlist_name: str, song_path: str) -> dict:
        """Supprime une chanson d'une playlist par son chemin."""
        name = playlist_name.strip().lower()
        with self._lock:
            if name not in self._playlists:
                return self._err(f"Playlist '{name}' introuvable.")
            before = len(self._playlists[name]["songs"])
            self._playlists[name]["songs"] = [
                s for s in self._playlists[name]["songs"]
                if s.get("path") != song_path
            ]
            after = len(self._playlists[name]["songs"])
            if before == after:
                return self._err(f"Chanson non trouvée dans '{name}'.")
            self._save()
        return self._ok(
            f"Chanson supprimée de '{name}'.",
            {"name": name, "count": after}
        )

    def add_folder_to_playlist(self, playlist_name: str, folder_path: str) -> dict:
        """Ajoute tous les fichiers audio d'un dossier à une playlist."""
        from modules.music.music_manager import MUSIC_EXTENSIONS
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            return self._err(f"Dossier introuvable : '{folder_path}'")

        files = []
        for ext in MUSIC_EXTENSIONS:
            files.extend(folder.rglob(f"*{ext}"))
            files.extend(folder.rglob(f"*{ext.upper()}"))

        files = sorted(set(files), key=lambda f: f.name.lower())
        if not files:
            return self._err(f"Aucun fichier musical trouvé dans '{folder_path}'.")

        added = 0
        for f in files:
            result = self.add_song(playlist_name, {
                "id":    f.stem,
                "title": f.stem,
                "path":  str(f),
            })
            if result["success"] and not result.get("data", {}).get("duplicate"):
                added += 1

        return self._ok(
            f"{added} chanson(s) ajoutée(s) à '{playlist_name}' depuis '{folder.name}'.",
            {"name": playlist_name, "added": added, "total_in_folder": len(files)}
        )

    def get_songs(self, playlist_name: str) -> list[str]:
        """Retourne la liste des chemins de fichiers d'une playlist."""
        pl = self.get_playlist(playlist_name)
        if not pl:
            return []
        return [s["path"] for s in pl.get("songs", []) if Path(s["path"]).exists()]

    def increment_play_count(self, name: str):
        """Incrémente le compteur de lectures d'une playlist."""
        name = name.strip().lower()
        with self._lock:
            if name in self._playlists:
                self._playlists[name]["play_count"] = \
                    self._playlists[name].get("play_count", 0) + 1
                self._save()

    def auto_create_by_genre(self, library: list[dict]) -> dict:
        """
        Crée automatiquement des playlists par genre depuis la bibliothèque.
        Regroupe par dossier parent si pas de tag genre.
        """
        groups: dict[str, list] = {}
        for song in library:
            genre = song.get("genre", "").strip().lower()
            if not genre:
                # Fallback : nom du dossier parent
                parent = Path(song.get("path", "")).parent.name.lower()
                genre = parent or "divers"
            groups.setdefault(genre, []).append(song)

        created = []
        for genre_name, songs in groups.items():
            if len(songs) < 2:
                continue
            result = self.create_playlist(genre_name)
            for song in songs:
                self.add_song(genre_name, song)
            created.append({"genre": genre_name, "count": len(songs)})

        return self._ok(
            f"{len(created)} playlist(s) créée(s) automatiquement.",
            {"created": created}
        )

    # ── Persistance ───────────────────────────────────────────────────────────

    def _load(self):
        if not PLAYLISTS_FILE.exists():
            return
        try:
            self._playlists = json.loads(
                PLAYLISTS_FILE.read_text(encoding="utf-8")
            )
        except Exception as e:
            logger.error(f"Chargement playlists échoué : {e}")
            self._playlists = {}

    def _save(self):
        try:
            PLAYLISTS_FILE.write_text(
                json.dumps(self._playlists, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Sauvegarde playlists échouée : {e}")

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}
