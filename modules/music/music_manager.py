"""
modules/music/music_manager.py — Gestionnaire de Bibliothèque Musicale
=======================================================================

Cerveau du module musique. Orchestre VLCController + PlaylistManager.

Fonctionnalités :
  - Scan et indexation de ~/Music en JSON
  - Recherche par titre, artiste, album
  - Lecture d'une chanson, d'un artiste, d'une playlist
  - Contrôle de lecture (pause, stop, next, prev, volume)
  - Historique d'écoute + musique la plus jouée
  - Recommandations simples (même artiste, même dossier)
  - Lecture aléatoire intelligente

Stockage : data/music/library.json

Format library.json :
{
  "songs": [
    {
      "id": "abc123",
      "title": "Shape of You",
      "artist": "Ed Sheeran",
      "album": "",
      "duration": 0,
      "path": "C:/Users/.../Music/Ed Sheeran/Shape of You.mp3",
      "play_count": 3,
      "last_played": 1234567890
    }
  ],
  "last_scan": 1234567890,
  "history": [
    {"song_id": "abc123", "timestamp": 1234567890}
  ]
}
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from pathlib import Path

from config.logger   import get_logger
from config.settings import BASE_DIR
from modules.music.vlc_controller   import VLCController
from modules.music.playlist_manager import PlaylistManager

logger = get_logger(__name__)

# Extensions audio supportées
MUSIC_EXTENSIONS = [".mp3", ".flac", ".wav", ".ogg", ".aac", ".m4a", ".wma", ".opus"]

# Dossiers par défaut à scanner
DEFAULT_MUSIC_DIRS = [
    Path.home() / "Music",
    Path.home() / "Musique",
]

# Fichier de bibliothèque
LIBRARY_FILE   = BASE_DIR / "data" / "music" / "library.json"
MAX_HISTORY    = 200   # entrées d'historique conservées


def _song_id(path: str) -> str:
    """ID stable basé sur le chemin absolu."""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


def _extract_metadata(file_path: Path) -> dict:
    """
    Extrait les métadonnées d'un fichier audio.
    Essaie mutagen d'abord, fallback sur le nom de fichier.
    """
    title  = file_path.stem
    artist = ""
    album  = ""
    genre  = ""
    duration = 0

    try:
        import mutagen
        audio = mutagen.File(str(file_path), easy=True)
        if audio is not None:
            title  = str((audio.get("title",  [file_path.stem])[0]  or file_path.stem)).strip()
            artist = str((audio.get("artist", [""])[0]              or "")).strip()
            album  = str((audio.get("album",  [""])[0]              or "")).strip()
            genre  = str((audio.get("genre",  [""])[0]              or "")).strip()
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                duration = int(audio.info.length)
    except Exception:
        # Fallback : deviner l'artiste depuis le nom du dossier parent
        parent_name = file_path.parent.name
        if parent_name.lower() not in {"music", "musique", "downloads", "téléchargements"}:
            artist = parent_name

    return {
        "title":    title,
        "artist":   artist,
        "album":    album,
        "genre":    genre,
        "duration": duration,
    }


class MusicManager:
    """
    Gestionnaire complet de la bibliothèque musicale Jarvis.
    Toutes les méthodes retournent { success, message, data }.
    """

    def __init__(self, music_dirs: list[str] = None):
        self._lock      = threading.Lock()
        self._songs:    list[dict] = []
        self._history:  list[dict] = []
        self._vlc       = VLCController()
        self._playlists = PlaylistManager()
        self._music_dirs = [Path(d) for d in (music_dirs or [])] or DEFAULT_MUSIC_DIRS

        LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        logger.info(
            f"MusicManager — {len(self._songs)} chanson(s), "
            f"VLC={'OK' if self._vlc.is_available() else 'indisponible'}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  BIBLIOTHÈQUE — Scan et indexation
    # ══════════════════════════════════════════════════════════════════════════

    def scan_library(self, path: str = None) -> dict:
        """
        Scanne les dossiers musicaux et indexe les fichiers trouvés.

        Args:
            path : dossier spécifique à scanner (défaut : ~/Music)

        Exemple :
          jarvis: "analyse mon dossier musique"
          → scanne ~/Music, indexe tous les MP3/FLAC/...
        """
        dirs = [Path(path)] if path else self._music_dirs
        found_paths = set()

        for base_dir in dirs:
            if not base_dir.exists():
                continue
            try:
                for ext in MUSIC_EXTENSIONS:
                    for f in base_dir.rglob(f"*{ext}"):
                        found_paths.add(str(f))
                    for f in base_dir.rglob(f"*{ext.upper()}"):
                        found_paths.add(str(f))
            except PermissionError:
                continue

        if not found_paths:
            scanned = ", ".join(str(d) for d in dirs if d.exists())
            return self._err(
                f"Aucun fichier musical trouvé dans : {scanned or 'dossiers par défaut'}",
                {"searched_dirs": [str(d) for d in dirs]}
            )

        # Indexer les nouveaux fichiers
        existing_paths = {s["path"] for s in self._songs}
        new_count = 0

        with self._lock:
            for fpath in found_paths:
                if fpath in existing_paths:
                    continue
                p = Path(fpath)
                meta = _extract_metadata(p)
                song = {
                    "id":          _song_id(fpath),
                    "title":       meta["title"],
                    "artist":      meta["artist"],
                    "album":       meta["album"],
                    "genre":       meta["genre"],
                    "duration":    meta["duration"],
                    "path":        fpath,
                    "play_count":  0,
                    "last_played": 0,
                    "added_at":    int(time.time()),
                }
                self._songs.append(song)
                new_count += 1

            # Supprimer les fichiers qui n'existent plus
            before = len(self._songs)
            self._songs = [s for s in self._songs if Path(s["path"]).exists()]
            removed = before - len(self._songs)

            self._save()

        total = len(self._songs)
        msg_parts = [f"{total} chanson(s) dans la bibliothèque."]
        if new_count:
            msg_parts.append(f"{new_count} nouvelle(s) ajoutée(s).")
        if removed:
            msg_parts.append(f"{removed} supprimée(s) (fichiers manquants).")

        return self._ok(
            " ".join(msg_parts),
            {
                "total":   total,
                "new":     new_count,
                "removed": removed,
                "dirs":    [str(d) for d in dirs],
            }
        )

    def count_songs(self) -> int:
        """Retourne le nombre de chansons indexées."""
        return len(self._songs)

    # ══════════════════════════════════════════════════════════════════════════
    #  RECHERCHE
    # ══════════════════════════════════════════════════════════════════════════

    def search_song(self, keyword: str) -> list[dict]:
        """
        Recherche des chansons par titre, artiste ou album.
        Retourne une liste de dicts song.
        """
        kw = keyword.strip().lower()
        if not kw:
            return []
        results = []
        for song in self._songs:
            score = 0
            title  = song.get("title", "").lower()
            artist = song.get("artist", "").lower()
            album  = song.get("album", "").lower()
            fname  = Path(song.get("path", "")).stem.lower()

            if title == kw or artist == kw:
                score += 100
            elif kw in title or kw in artist:
                score += 60
            elif kw in album or kw in fname:
                score += 30

            if score > 0:
                results.append({**song, "_score": score})

        results.sort(key=lambda x: (-x["_score"], -x.get("play_count", 0)))
        # Nettoyer le score interne
        for r in results:
            r.pop("_score", None)
        return results

    def find_by_artist(self, artist: str) -> list[dict]:
        """Retourne toutes les chansons d'un artiste."""
        a = artist.strip().lower()
        return [s for s in self._songs if a in s.get("artist", "").lower()]

    def find_by_album(self, album: str) -> list[dict]:
        """Retourne toutes les chansons d'un album."""
        a = album.strip().lower()
        return [s for s in self._songs if a in s.get("album", "").lower()]

    # ══════════════════════════════════════════════════════════════════════════
    #  LECTURE
    # ══════════════════════════════════════════════════════════════════════════

    def play(self, query: str) -> dict:
        """
        Joue une musique par titre, artiste, ou chemin direct.

        Exemple :
          jarvis: "joue hallelujah"
          jarvis: "joue Michael Jackson"
          jarvis: "joue C:/Music/song.mp3"
        """
        if not query:
            return self._err("Précise le nom d'une chanson ou d'un artiste.")

        # Cas 1 : chemin direct
        p = Path(query)
        if p.is_absolute() and p.exists() and p.suffix.lower() in MUSIC_EXTENSIONS:
            return self._play_path(str(p))

        # Cas 2 : recherche dans la bibliothèque
        if self._songs:
            results = self.search_song(query)
            if results:
                song = results[0]
                return self._play_path(song["path"], song=song)

        # Cas 3 : bibliothèque vide → scan auto puis retry
        if not self._songs:
            logger.info("Bibliothèque vide — scan automatique...")
            scan = self.scan_library()
            if self._songs:
                results = self.search_song(query)
                if results:
                    return self._play_path(results[0]["path"], results[0])

        # Cas 4 : recherche directe dans le dossier musique
        for base_dir in self._music_dirs:
            if not base_dir.exists():
                continue
            for ext in MUSIC_EXTENSIONS:
                for f in base_dir.rglob(f"*{query}*{ext}"):
                    return self._play_path(str(f))

        return self._err(
            f"Aucune musique trouvée pour '{query}'. "
            f"Dis 'analyse mon dossier musique' pour scanner ta bibliothèque.",
            {"query": query, "library_size": len(self._songs)}
        )

    def play_random(self) -> dict:
        """Joue une musique aléatoire de la bibliothèque."""
        if not self._songs:
            return self._err("Bibliothèque vide. Dis 'analyse mon dossier musique'.")
        song = random.choice(self._songs)
        return self._play_path(song["path"], song)

    def play_by_artist(self, artist: str) -> dict:
        """Joue toutes les chansons d'un artiste."""
        songs = self.find_by_artist(artist)
        if not songs:
            return self._err(f"Aucune chanson de '{artist}' dans la bibliothèque.")
        paths = [s["path"] for s in songs if Path(s["path"]).exists()]
        result = self._vlc.play_playlist(paths)
        if result["success"]:
            self._record_history(songs[0]["id"])
        return self._ok(
            f"{len(paths)} chanson(s) de '{artist}'.",
            {"artist": artist, "count": len(paths), **result.get("data", {})}
        )

    def play_by_album(self, album: str) -> dict:
        """Joue toutes les chansons d'un album."""
        songs = self.find_by_album(album)
        if not songs:
            return self._err(f"Album '{album}' non trouvé dans la bibliothèque.")
        paths = [s["path"] for s in songs if Path(s["path"]).exists()]
        result = self._vlc.play_playlist(paths)
        return self._ok(
            f"Album '{album}' — {len(paths)} piste(s).",
            {"album": album, "count": len(paths), **result.get("data", {})}
        )

    def play_playlist(self, playlist_name: str) -> dict:
        """Joue une playlist par nom."""
        paths = self._playlists.get_songs(playlist_name)
        if not paths:
            pl = self._playlists.get_playlist(playlist_name)
            if pl is None:
                return self._err(f"Playlist '{playlist_name}' introuvable.")
            return self._err(f"La playlist '{playlist_name}' est vide.")

        result = self._vlc.play_playlist(paths)
        if result["success"]:
            self._playlists.increment_play_count(playlist_name)
        return self._ok(
            f"Playlist '{playlist_name}' — {len(paths)} morceau(x).",
            {"playlist": playlist_name, "count": len(paths), **result.get("data", {})}
        )

    def _play_path(self, path: str, song: dict = None) -> dict:
        """Joue un chemin et met à jour l'historique."""
        result = self._vlc.play_file(path)
        if result["success"] and song:
            self._increment_play_count(song["id"])
            self._record_history(song["id"])
        return result

    # ══════════════════════════════════════════════════════════════════════════
    #  CONTRÔLE DE LECTURE
    # ══════════════════════════════════════════════════════════════════════════

    def pause(self) -> dict:
        return self._vlc.pause()

    def resume(self) -> dict:
        return self._vlc.resume()

    def stop(self) -> dict:
        return self._vlc.stop()

    def next_track(self) -> dict:
        return self._vlc.next_track()

    def prev_track(self) -> dict:
        return self._vlc.prev_track()

    def set_volume(self, level: int) -> dict:
        return self._vlc.set_volume(level)

    def toggle_shuffle(self) -> dict:
        return self._vlc.toggle_shuffle()

    def toggle_repeat(self) -> dict:
        return self._vlc.toggle_repeat()

    def current_song(self) -> dict:
        return self._vlc.current_song()

    def get_status(self) -> dict:
        return self._vlc.get_status()

    # ══════════════════════════════════════════════════════════════════════════
    #  PLAYLISTS — délégation à PlaylistManager
    # ══════════════════════════════════════════════════════════════════════════

    def create_playlist(self, name: str) -> dict:
        return self._playlists.create_playlist(name)

    def delete_playlist(self, name: str) -> dict:
        return self._playlists.delete_playlist(name)

    def list_playlists(self) -> dict:
        return self._playlists.list_playlists()

    def add_song_to_playlist(self, song_query: str, playlist_name: str) -> dict:
        """Cherche une chanson et l'ajoute à une playlist."""
        results = self.search_song(song_query)
        if not results:
            return self._err(f"Chanson '{song_query}' non trouvée dans la bibliothèque.")
        song = results[0]
        return self._playlists.add_song(playlist_name, song)

    def add_folder_to_playlist(self, folder_path: str, playlist_name: str) -> dict:
        return self._playlists.add_folder_to_playlist(playlist_name, folder_path)

    def auto_create_playlists_by_genre(self) -> dict:
        """Crée automatiquement des playlists par genre."""
        return self._playlists.auto_create_by_genre(self._songs)

    # ══════════════════════════════════════════════════════════════════════════
    #  RECOMMANDATION
    # ══════════════════════════════════════════════════════════════════════════

    def recommend_song(self) -> dict:
        """
        Recommande une chanson basée sur :
        1. Le même artiste que les dernières écoutes
        2. Sinon : une chanson peu jouée
        3. Sinon : aléatoire
        """
        if not self._songs:
            return self._err("Bibliothèque vide.")

        # Artiste le plus récemment écouté
        recent_ids = [h["song_id"] for h in self._history[-5:]]
        recent_songs = [s for s in self._songs if s["id"] in recent_ids]
        if recent_songs:
            artist = recent_songs[-1].get("artist", "")
            if artist:
                same_artist = [
                    s for s in self._songs
                    if s.get("artist") == artist and s["id"] not in recent_ids
                ]
                if same_artist:
                    choice = min(same_artist, key=lambda x: x.get("play_count", 0))
                    return self._ok(
                        f"Je te recommande '{choice['title']}' de {artist}.",
                        {"song": choice, "reason": "same_artist"}
                    )

        # Chanson peu jouée
        unplayed = [s for s in self._songs if s.get("play_count", 0) == 0]
        if unplayed:
            choice = random.choice(unplayed)
            return self._ok(
                f"Tu n'as jamais écouté '{choice['title']}' — essaie-le !",
                {"song": choice, "reason": "unplayed"}
            )

        # Aléatoire
        choice = random.choice(self._songs)
        return self._ok(
            f"Que penses-tu de '{choice['title']}' ?",
            {"song": choice, "reason": "random"}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  HISTORIQUE & STATS
    # ══════════════════════════════════════════════════════════════════════════

    def most_played(self, n: int = 5) -> dict:
        """Retourne les n chansons les plus écoutées."""
        sorted_songs = sorted(
            [s for s in self._songs if s.get("play_count", 0) > 0],
            key=lambda x: x.get("play_count", 0),
            reverse=True,
        )[:n]

        if not sorted_songs:
            return self._ok(
                "Aucune chanson écoutée pour le moment.",
                {"songs": [], "count": 0}
            )

        lines = [f"{'TITRE':<35} {'ARTISTE':<20} ÉCOUTES"]
        lines.append("─" * 65)
        for s in sorted_songs:
            lines.append(
                f"{s['title'][:34]:<35} {s.get('artist', '')[:19]:<20} {s.get('play_count', 0)}"
            )

        return self._ok(
            f"Top {len(sorted_songs)} chansons les plus écoutées.",
            {"songs": sorted_songs, "count": len(sorted_songs), "display": "\n".join(lines)}
        )

    def last_played(self, n: int = 5) -> dict:
        """Retourne les n dernières chansons écoutées."""
        recent_ids = [h["song_id"] for h in reversed(self._history[-n:])]
        id_to_song = {s["id"]: s for s in self._songs}
        recent = [id_to_song[sid] for sid in recent_ids if sid in id_to_song]

        if not recent:
            return self._ok("Aucun historique d'écoute.", {"songs": [], "count": 0})

        return self._ok(
            f"{len(recent)} dernière(s) chanson(s) écoutée(s).",
            {"songs": recent, "count": len(recent)}
        )

    def _record_history(self, song_id: str):
        """Ajoute une écoute dans l'historique."""
        with self._lock:
            self._history.append({
                "song_id":   song_id,
                "timestamp": int(time.time()),
            })
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[-MAX_HISTORY:]
            self._save()

    def _increment_play_count(self, song_id: str):
        """Incrémente le compteur de lectures d'une chanson."""
        with self._lock:
            for song in self._songs:
                if song["id"] == song_id:
                    song["play_count"] = song.get("play_count", 0) + 1
                    song["last_played"] = int(time.time())
                    break
            self._save()

    # ══════════════════════════════════════════════════════════════════════════
    #  PERSISTANCE
    # ══════════════════════════════════════════════════════════════════════════

    def _load(self):
        if not LIBRARY_FILE.exists():
            return
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
            self._songs   = data.get("songs", [])
            self._history = data.get("history", [])
        except Exception as e:
            logger.error(f"Chargement bibliothèque musicale échoué : {e}")

    def _save(self):
        """Sauvegarde la bibliothèque et l'historique."""
        try:
            LIBRARY_FILE.write_text(
                json.dumps(
                    {
                        "songs":     self._songs,
                        "history":   self._history[-MAX_HISTORY:],
                        "last_save": int(time.time()),
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Sauvegarde bibliothèque échouée : {e}")

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}
