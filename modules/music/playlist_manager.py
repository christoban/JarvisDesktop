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

import copy
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

    def rename_playlist(self, old_name: str, new_name: str) -> dict:
        """Renomme une playlist existante."""
        old_key = old_name.strip().lower()
        new_key = new_name.strip().lower()
        if not old_key or not new_key:
            return self._err("Précise l'ancien et le nouveau nom de playlist.")
        if old_key == new_key:
            return self._ok("Le nom est identique, aucun changement.", {"name": old_key, "unchanged": True})

        with self._lock:
            if old_key not in self._playlists:
                return self._err(f"Playlist '{old_key}' introuvable.")
            if new_key in self._playlists:
                return self._err(f"Playlist '{new_key}' existe déjà.")

            pl = dict(self._playlists[old_key])
            pl["id"] = new_key
            pl["name"] = new_key
            self._playlists[new_key] = pl
            del self._playlists[old_key]
            self._save()

        return self._ok(
            f"Playlist renommée : '{old_key}' -> '{new_key}'.",
            {"old_name": old_key, "new_name": new_key}
        )

    def duplicate_playlist(self, source_name: str, target_name: str) -> dict:
        """Duplique une playlist vers un nouveau nom."""
        src = source_name.strip().lower()
        dst = target_name.strip().lower()
        if not src or not dst:
            return self._err("Précise la playlist source et le nom cible.")
        if src == dst:
            return self._err("Le nom cible doit être différent du nom source.")

        with self._lock:
            if src not in self._playlists:
                return self._err(f"Playlist source '{src}' introuvable.")
            if dst in self._playlists:
                return self._err(f"Playlist cible '{dst}' existe déjà.")

            src_pl = self._playlists[src]
            clone = {
                "id": dst,
                "name": dst,
                "songs": copy.deepcopy(src_pl.get("songs", [])),
                "created_at": int(time.time()),
                "play_count": 0,
            }
            self._playlists[dst] = clone
            self._save()

        return self._ok(
            f"Playlist '{src}' dupliquée vers '{dst}'.",
            {"source": src, "target": dst, "count": len(clone.get("songs", []))}
        )

    def merge_playlists(self, source_name: str, target_name: str, output_name: str = "") -> dict:
        """Fusionne deux playlists en évitant les doublons de chemins."""
        src = source_name.strip().lower()
        tgt = target_name.strip().lower()
        out = (output_name or f"{src}_{tgt}").strip().lower()
        if not src or not tgt:
            return self._err("Précise les deux playlists à fusionner.")
        if src == tgt:
            return self._err("Les playlists source et cible doivent être différentes.")

        with self._lock:
            if src not in self._playlists:
                return self._err(f"Playlist source '{src}' introuvable.")
            if tgt not in self._playlists:
                return self._err(f"Playlist cible '{tgt}' introuvable.")

            src_songs = list(self._playlists[src].get("songs", []))
            tgt_songs = list(self._playlists[tgt].get("songs", []))

            merged = []
            seen = set()
            for s in src_songs + tgt_songs:
                p = s.get("path")
                if not p or p in seen:
                    continue
                seen.add(p)
                merged.append(dict(s))

            if out in self._playlists:
                self._playlists[out]["songs"] = merged
                self._playlists[out]["updated_at"] = int(time.time())
            else:
                self._playlists[out] = {
                    "id": out,
                    "name": out,
                    "songs": merged,
                    "created_at": int(time.time()),
                    "play_count": 0,
                }
            self._save()

        return self._ok(
            f"Playlists '{src}' et '{tgt}' fusionnées dans '{out}'.",
            {
                "source": src,
                "target": tgt,
                "output": out,
                "count": len(merged),
            }
        )

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

    def remove_song_by_title(self, playlist_name: str, song_title: str, remove_all: bool = False) -> dict:
        """Supprime une chanson d'une playlist en cherchant par titre.

        Stratégie :
        - priorité à la correspondance exacte (insensible à la casse)
        - sinon correspondance partielle
        - par défaut, supprime une seule chanson (la meilleure candidate)
        - si remove_all=True, supprime toutes les correspondances
        """
        name = playlist_name.strip().lower()
        title_lower = song_title.strip().lower()
        
        with self._lock:
            if name not in self._playlists:
                return self._err(f"Playlist '{name}' introuvable.")
            
            songs = self._playlists[name]["songs"]

            exact_matches = []
            partial_matches = []
            for idx, s in enumerate(songs):
                song_name = str(s.get("title", "")).strip().lower()
                if not song_name:
                    continue
                if song_name == title_lower:
                    exact_matches.append(idx)
                elif title_lower in song_name or song_name in title_lower:
                    partial_matches.append(idx)

            candidates = exact_matches if exact_matches else partial_matches
            if not candidates:
                return self._err(f"Chanson '{song_title}' non trouvée dans '{name}'.")

            to_remove = set(candidates if remove_all else [candidates[0]])
            removed_songs = []
            remaining = []
            for idx, s in enumerate(songs):
                if idx in to_remove:
                    removed_songs.append(s)
                else:
                    remaining.append(s)

            self._playlists[name]["songs"] = remaining
            self._save()
        
        removed_titles = ", ".join([s.get("title", "?") for s in removed_songs])
        return self._ok(
            f"{len(removed_songs)} chanson(s) supprimée(s) de '{name}' : {removed_titles}",
            {
                "name": name,
                "removed": len(removed_songs),
                "count": len(remaining),
                "multiple_matches": len(candidates) > 1,
                "removed_all": bool(remove_all),
            }
        )

    def move_song(self, playlist_name: str, query: str = "", from_index: int = None, to_index: int = None) -> dict:
        """Déplace une chanson dans une playlist par index ou par titre partiel."""
        name = playlist_name.strip().lower()
        if to_index is None:
            return self._err("Précise la position de destination (to_index).")

        with self._lock:
            if name not in self._playlists:
                return self._err(f"Playlist '{name}' introuvable.")

            songs = list(self._playlists[name].get("songs", []))
            if not songs:
                return self._err(f"Playlist '{name}' vide.")

            src_idx = None
            if from_index is not None:
                if 1 <= int(from_index) <= len(songs):
                    src_idx = int(from_index) - 1
                else:
                    return self._err(f"Index source invalide: {from_index}.")
            else:
                q = (query or "").strip().lower()
                if not q:
                    return self._err("Précise un titre (query) ou un index source.")
                exact = [i for i, s in enumerate(songs) if str(s.get("title", "")).strip().lower() == q]
                partial = [i for i, s in enumerate(songs) if q in str(s.get("title", "")).strip().lower()]
                candidates = exact or partial
                if not candidates:
                    return self._err(f"Chanson '{query}' non trouvée dans '{name}'.")
                src_idx = candidates[0]

            dst_idx = int(to_index) - 1
            dst_idx = max(0, min(dst_idx, len(songs) - 1))

            song = songs.pop(src_idx)
            songs.insert(dst_idx, song)
            self._playlists[name]["songs"] = songs
            self._save()

        return self._ok(
            f"Chanson déplacée en position {dst_idx + 1} dans '{name}'.",
            {
                "name": name,
                "from_index": src_idx + 1,
                "to_index": dst_idx + 1,
                "title": song.get("title", ""),
            }
        )

    def clear_playlist(self, playlist_name: str) -> dict:
        """Vide complètement une playlist (garde la playlist, enlève toutes les chansons)."""
        name = playlist_name.strip().lower()
        with self._lock:
            if name not in self._playlists:
                return self._err(f"Playlist '{name}' introuvable.")
            
            count_before = len(self._playlists[name]["songs"])
            self._playlists[name]["songs"] = []
            self._save()
        
        return self._ok(
            f"Playlist '{name}' vidée ({count_before} chanson(s) supprimée(s)).",
            {"name": name, "cleared": True, "count_removed": count_before}
        )

    def export_playlist(self, playlist_name: str, fmt: str = "m3u", output_path: str = "") -> dict:
        """Exporte une playlist en .m3u ou .json."""
        name = playlist_name.strip().lower()
        fmt = (fmt or "m3u").strip().lower()
        if fmt not in {"m3u", "json"}:
            return self._err("Format d'export non supporté. Utilise 'm3u' ou 'json'.")

        with self._lock:
            pl = self._playlists.get(name)
            if not pl:
                return self._err(f"Playlist '{name}' introuvable.")
            songs = list(pl.get("songs", []))

        export_dir = PLAYLISTS_FILE.parent / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        target = Path(output_path) if output_path else (export_dir / f"{name}.{fmt}")
        if target.suffix.lower() != f".{fmt}":
            target = target.with_suffix(f".{fmt}")

        try:
            if fmt == "json":
                payload = {
                    "playlist": {
                        "name": name,
                        "songs": songs,
                        "exported_at": int(time.time()),
                    }
                }
                target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                lines = ["#EXTM3U"]
                for s in songs:
                    title = s.get("title") or Path(str(s.get("path", ""))).stem
                    artist = s.get("artist") or ""
                    label = f"{artist} - {title}".strip(" -")
                    lines.append(f"#EXTINF:-1,{label}")
                    lines.append(str(s.get("path", "")))
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")

            return self._ok(
                f"Playlist '{name}' exportée en {fmt.upper()}.",
                {"name": name, "format": fmt, "path": str(target), "count": len(songs)}
            )
        except Exception as e:
            return self._err(f"Export playlist échoué : {e}")

    def import_playlist(self, input_path: str, playlist_name: str = "", mode: str = "replace") -> dict:
        """Importe une playlist depuis un fichier .m3u ou .json."""
        src = Path(str(input_path or "").strip())
        if not src.exists() or not src.is_file():
            return self._err(f"Fichier d'import introuvable : '{input_path}'")

        mode = (mode or "replace").strip().lower()
        if mode not in {"replace", "append"}:
            return self._err("Mode d'import invalide. Utilise 'replace' ou 'append'.")

        target_name = (playlist_name or src.stem).strip().lower()
        if not target_name:
            return self._err("Nom de playlist cible vide.")

        imported_songs = []
        try:
            ext = src.suffix.lower()
            if ext == ".json":
                data = json.loads(src.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("playlist"), dict):
                    data = data["playlist"].get("songs", [])
                if not isinstance(data, list):
                    return self._err("JSON d'import invalide : liste de chansons attendue.")
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    imported_songs.append({
                        "id": item.get("id", Path(path).stem),
                        "title": item.get("title", Path(path).stem),
                        "artist": item.get("artist", ""),
                        "album": item.get("album", ""),
                        "path": path,
                    })
            else:
                # m3u/m3u8
                for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
                    item = line.strip()
                    if not item or item.startswith("#"):
                        continue
                    p = Path(item)
                    imported_songs.append({
                        "id": p.stem,
                        "title": p.stem,
                        "artist": "",
                        "album": "",
                        "path": str(p),
                    })
        except Exception as e:
            return self._err(f"Lecture du fichier d'import échouée : {e}")

        if not imported_songs:
            return self._err("Aucune chanson valide trouvée dans le fichier d'import.")

        with self._lock:
            if target_name not in self._playlists:
                self._playlists[target_name] = {
                    "id": target_name,
                    "name": target_name,
                    "songs": [],
                    "created_at": int(time.time()),
                    "play_count": 0,
                }
            if mode == "replace":
                self._playlists[target_name]["songs"] = []

            existing_paths = {s.get("path") for s in self._playlists[target_name]["songs"]}
            added = 0
            for s in imported_songs:
                if s["path"] in existing_paths:
                    continue
                self._playlists[target_name]["songs"].append({
                    "id": s.get("id", Path(s["path"]).stem),
                    "title": s.get("title", Path(s["path"]).stem),
                    "artist": s.get("artist", ""),
                    "album": s.get("album", ""),
                    "path": s["path"],
                    "added_at": int(time.time()),
                })
                existing_paths.add(s["path"])
                added += 1

            self._save()

        return self._ok(
            f"Import playlist terminé : {added} chanson(s) ajoutée(s) dans '{target_name}'.",
            {
                "name": target_name,
                "added": added,
                "source": str(src),
                "mode": mode,
                "total": len(self._playlists[target_name]["songs"]),
            }
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
