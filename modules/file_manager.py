"""
file_manager.py — Gestion des fichiers et dossiers Windows
Rechercher, ouvrir, copier, déplacer, renommer, supprimer, créer dossier, lister.

SEMAINE 3 — IMPLÉMENTATION COMPLÈTE
  Mercredi : search_file, search_by_type, open_file, list_folder
  Jeudi    : copy_file, move_file, rename_file, delete_file, create_folder
             + search_by_content, get_file_info, find_duplicates
"""

import json
import os
import re
import shutil
import hashlib
import datetime
import subprocess
from pathlib import Path
from config.logger import get_logger

logger = get_logger(__name__)

# ── Dossiers de recherche par défaut ────────────────────────────────────────
DEFAULT_SEARCH_DIRS = [
    Path.home(),
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Pictures",
    Path.home() / "Music",
    Path.home() / "Videos",
]

KNOWN_FOLDER_ALIASES = {
    "desktop": Path.home() / "Desktop",
    "bureau": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "document": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
    "telechargements": Path.home() / "Downloads",
    "téléchargements": Path.home() / "Downloads",
    "pictures": Path.home() / "Pictures",
    "images": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "musique": Path.home() / "Music",
    "videos": Path.home() / "Videos",
    "vidéos": Path.home() / "Videos",
}

# Dossiers à toujours exclure (trop profonds ou non pertinents)
EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "Windows", "System32", "SysWOW64", "$Recycle.Bin", "AppData",
}

# Types de fichiers par catégorie
FILE_TYPE_CATEGORIES = {
    "documents": [".docx", ".doc", ".pdf", ".txt", ".odt", ".rtf", ".md"],
    "images":    [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico"],
    "videos":    [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"],
    "audio":     [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
    "archives":  [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
    "code":      [".py", ".js", ".html", ".css", ".json", ".xml", ".java",
                  ".cpp", ".c", ".h", ".cs", ".php", ".rb", ".go", ".ts"],
    "tableurs":  [".xlsx", ".xls", ".csv", ".ods"],
    "slides":    [".pptx", ".ppt", ".odp"],
}


class FileManager:
    """
    Gestionnaire complet de fichiers et dossiers.
    Toutes les méthodes retournent le format standard :
        { "success": bool, "message": str, "data": dict | None }
    """

    def __init__(self, search_dirs: list = None, max_depth: int = 5):
        self.search_dirs = search_dirs or DEFAULT_SEARCH_DIRS
        self.max_depth   = max_depth

    # ══════════════════════════════════════════════════════════════════════════
    #  MERCREDI — Recherche et consultation
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_all_drive_roots() -> list:
        """Retourne les racines de tous les disques disponibles sur Windows."""
        import string
        roots = []
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append(drive)
        return roots

    def search_file(self, name: str, search_dirs: list = None, max_results: int = 20) -> dict:
        """
        Recherche un fichier par nom (partiel ou complet, insensible à la casse).

        Args:
            name        : nom ou partie du nom (ex: "rapport", "budget.xlsx")
            search_dirs : dossiers où chercher (défaut: Documents, Desktop, etc.)
            max_results : nombre max de résultats retournés

        Exemples :
            search_file("rapport")        → tous les fichiers contenant "rapport"
            search_file("budget.xlsx")    → fichier exact
            search_file("*.pdf")          → tous les PDF (wildcard)
        """
        logger.info(f"Recherche fichier : '{name}'")
        dirs = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err(
                "Aucun dossier cible valide pour la recherche.",
                {"requested_search_dirs": [str(d) for d in (search_dirs or [])]}
            )
        name_lower = name.lower().strip()

        # Détecter si c'est une extension pure (*.pdf ou .pdf)
        if name_lower.startswith("*.") or name_lower.startswith("."):
            ext = name_lower.lstrip("*")
            return self.search_by_type(ext)

        results = self._search_entries(name_lower, dirs, target_type="file", max_results=max_results)

        if not results:
            return self._err(
                f"Aucun fichier trouvé contenant '{name}' dans les dossiers scannés.",
                {"query": name, "searched_dirs": [str(d) for d in dirs]}
            )

        # Trier par date de modification (plus récent en premier)
        results.sort(key=lambda x: x.get("modified", ""), reverse=True)

        lines = [f"{'NOM':<40} {'TAILLE':>10}  CHEMIN"]
        lines.append("-" * 90)
        for r in results[:10]:
            lines.append(
                f"{r['name'][:39]:<40} {r['size_str']:>10}  {r['path']}"
            )
        if len(results) > 10:
            lines.append(f"  ... et {len(results) - 10} autre(s) résultat(s)")

        return self._ok(
            f"{len(results)} fichier(s) trouvé(s) pour '{name}'.",
            {"files": results, "count": len(results), "display": "\n".join(lines)}
        )

    def search_by_type(self, extension: str, search_dirs: list = None,
                       max_results: int = 50) -> dict:
        """
        Recherche tous les fichiers d'un type donné.

        Args:
            extension : extension avec ou sans point (".pdf" ou "pdf")
                        ou catégorie : "documents", "images", "videos", etc.

        Exemples :
            search_by_type(".pdf")
            search_by_type("pdf")
            search_by_type("documents")   → tous .docx, .pdf, .txt, etc.
        """
        logger.info(f"Recherche par type : '{extension}'")
        dirs = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err(
                "Aucun dossier cible valide pour la recherche par type.",
                {"requested_search_dirs": [str(d) for d in (search_dirs or [])]}
            )

        # Résoudre les extensions cibles
        ext_lower = extension.lower().strip().lstrip("*")
        if not ext_lower.startswith("."):
            ext_lower = "." + ext_lower

        # Vérifier si c'est une catégorie
        cat_key = extension.lower().strip().rstrip("s")  # "document" → "document"
        target_exts = None
        for cat, exts in FILE_TYPE_CATEGORIES.items():
            if extension.lower().strip() == cat or cat_key in cat:
                target_exts = set(exts)
                break

        if target_exts is None:
            target_exts = {ext_lower}

        results = []
        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if item.is_file() and item.suffix.lower() in target_exts:
                        results.append(self._file_info_dict(item))
                        if len(results) >= max_results:
                            break
            except PermissionError:
                continue
            if len(results) >= max_results:
                break

        results.sort(key=lambda x: x.get("modified", ""), reverse=True)

        lines = [f"{'NOM':<40} {'TAILLE':>10}  CHEMIN"]
        lines.append("-" * 90)
        for r in results[:15]:
            lines.append(f"{r['name'][:39]:<40} {r['size_str']:>10}  {r['path']}")
        if len(results) > 15:
            lines.append(f"  ... et {len(results) - 15} autre(s)")

        ext_display = ", ".join(target_exts)
        return self._ok(
            f"{len(results)} fichier(s) {ext_display} trouvé(s).",
            {"files": results, "count": len(results),
             "extensions": list(target_exts), "display": "\n".join(lines)}
        )

    def open_file(
        self,
        path: str,
        search_dirs: list = None,
        target_type: str = "any",
        current_dir: str | None = None,
    ) -> dict:
        """
        Ouvre un fichier avec l'application par défaut du système.
        Si le chemin est partiel, cherche d'abord le fichier.

        Args:
            path : chemin complet ou nom de fichier à chercher
        """
        logger.info(f"Ouverture cible : '{path}'")
        requested_path = (path or "").strip().strip('"').strip("'")
        requested_path = self._sanitize_open_query(requested_path)
        requested_search_dirs = search_dirs
        search_dirs = self._normalize_search_dirs(search_dirs, current_dir=current_dir)
        if requested_search_dirs is not None and not search_dirs:
            return self._err(
                "Aucun dossier cible valide pour l'ouverture.",
                {"requested_search_dirs": [str(d) for d in (requested_search_dirs or [])]}
            )
        file_path = self._resolve_existing_path(requested_path, current_dir=current_dir)

        if file_path is None:
            search_query = Path(requested_path).name or requested_path
            matches = self._search_entries(
                search_query.lower(),
                search_dirs,
                target_type=target_type,
                max_results=12,
            )
            if not matches:
                return self._err(
                    f"Introuvable : '{path}'.",
                    {
                        "query": path,
                        "search_query": search_query,
                        "searched_dirs": [str(d) for d in search_dirs],
                    }
                )
            if len(matches) == 1:
                file_path = Path(matches[0]["path"])
            else:
                display = self._format_choice_display(matches)
                item_label = "éléments" if target_type == "any" else ("dossiers" if target_type == "directory" else "fichiers")
                return self._ok(
                    f"J'ai trouvé {len(matches)} {item_label} pour '{path}'. Lequel ouvrir ?",
                    {
                        "ambiguous": True,
                        "awaiting_choice": True,
                        "choices": matches,
                        "files": matches,
                        "count": len(matches),
                        "display": display,
                    }
                )

        if not file_path.exists():
            return self._err(f"Le chemin n'existe pas : '{file_path}'")

        resolved_target = self._safe_resolve_path(file_path)

        try:
            import platform
            system = platform.system()
            if system == "Windows":
                os.startfile(str(file_path))
            elif system == "Darwin":
                import subprocess
                subprocess.Popen(["open", str(file_path)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(file_path)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)

            logger.info(f"Cible ouverte : {file_path}")
            if file_path.is_dir():
                try:
                    entries = list(file_path.iterdir())
                    folders = [e for e in entries if e.is_dir()]
                    files = [e for e in entries if e.is_file()]
                    preview = [f.name for f in files[:8]]
                except PermissionError:
                    folders, files, preview = [], [], []

                message = (
                    f"Dossier ouvert : '{file_path.name}'. "
                    f"Il contient {len(folders)} dossier(s) et {len(files)} fichier(s). "
                    f"Voulez-vous que j'ouvre un fichier précis dedans ?"
                )
                return self._ok(
                    message,
                    {
                        **self._file_info_dict(file_path),
                        "opened_path": resolved_target,
                        "resolved_path": resolved_target,
                        "current_directory": resolved_target,
                        "top_files": preview,
                    }
                )

            return self._ok(
                f"Fichier ouvert : '{file_path.name}'",
                {
                    **self._file_info_dict(file_path),
                    "opened_path": resolved_target,
                    "resolved_path": resolved_target,
                }
            )
        except AttributeError:
            # os.startfile n'existe pas (Linux)
            import subprocess
            subprocess.Popen(["xdg-open", str(file_path)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            if file_path.is_dir():
                return self._ok(
                    f"Dossier ouvert : '{file_path.name}'. Voulez-vous que j'ouvre un fichier précis dedans ?",
                    {
                        **self._file_info_dict(file_path),
                        "opened_path": resolved_target,
                        "resolved_path": resolved_target,
                        "current_directory": resolved_target,
                    }
                )
            return self._ok(
                f"Fichier ouvert : '{file_path.name}'",
                {
                    **self._file_info_dict(file_path),
                    "opened_path": resolved_target,
                    "resolved_path": resolved_target,
                }
            )
        except Exception as e:
            return self._err(f"Impossible d'ouvrir '{file_path.name}' : {str(e)}")

    def list_folder(self, path: str = None, show_hidden: bool = False) -> dict:
        """
        Liste le contenu d'un dossier avec détails.

        Args:
            path        : chemin du dossier (défaut: répertoire courant)
            show_hidden : inclure les fichiers cachés (commençant par .)
        """
        folder = self._resolve_existing_path(path, current_dir=None) if path else Path.home()

        # Si pas trouvé directement → chercher sur tous les disques
        if (folder is None or not folder.exists()) and path:
            # Chercher d'abord à la racine des disques, puis dans les dossiers standards
            # Priorité aux disques non-système (D:, E:, F:...) puis C:, puis dossiers user
            drive_roots = self._get_all_drive_roots()
            non_system_drives = [d for d in drive_roots if d.drive.upper() != "C:"]
            system_drive = [d for d in drive_roots if d.drive.upper() == "C:"]
            search_order = non_system_drives + system_drive + list(self.search_dirs)

            matches = self._search_entries(
                Path(path.lstrip("/\\")).name.lower(),
                search_order,
                target_type="directory",
                max_results=5,
            )
            if matches:
                # Prendre le meilleur match
                folder = Path(matches[0]["resolved_path"] or matches[0]["path"])
            else:
                return self._err(
                    f"Dossier '{path}' introuvable sur cette machine.",
                    {"query": path}
                )

        if not folder:
            folder = Path.home()

        logger.info(f"Listage dossier : '{folder}'")

        if not folder.exists():
            return self._err(f"Dossier introuvable : '{folder}'")
        if not folder.is_dir():
            return self._err(f"'{folder}' n'est pas un dossier.")

        try:
            items = list(folder.iterdir())
        except PermissionError:
            return self._err(f"Accès refusé au dossier : '{folder}'")

        if not show_hidden:
            items = [i for i in items if not i.name.startswith(".")]

        # Trier : dossiers en premier, puis fichiers, alphabétique
        items.sort(key=lambda x: (x.is_file(), x.name.lower()))

        files   = [i for i in items if i.is_file()]
        folders = [i for i in items if i.is_dir()]

        lines = [
            f"📂 {folder}",
            f"   {len(folders)} dossier(s), {len(files)} fichier(s)",
            "-" * 70,
        ]

        for d in folders:
            try:
                n_children = len(list(d.iterdir()))
                lines.append(f"  [DIR]  {d.name:<45}  ({n_children} élément(s))")
            except PermissionError:
                lines.append(f"  [DIR]  {d.name:<45}  (accès refusé)")

        for f in files:
            info = self._file_info_dict(f)
            lines.append(f"  [FIL]  {f.name:<45}  {info['size_str']:>10}")

        # Stats globales
        total_size = sum(
            f.stat().st_size for f in files
            if f.exists()
        )
        lines.append("-" * 70)
        lines.append(f"  Total fichiers : {self._format_size(total_size)}")

        all_items = [
            {**self._file_info_dict(i), "is_dir": i.is_dir()}
            for i in items
        ]

        resolved_folder = self._safe_resolve_path(folder)

        return self._ok(
            f"{len(folders)} dossier(s) et {len(files)} fichier(s) dans '{folder.name}'.",
            {
                "path":    resolved_folder,
                "resolved_path": resolved_folder,
                "files":   [i for i in all_items if not i["is_dir"]],
                "folders": [i for i in all_items if i["is_dir"]],
                "total":   len(items),
                "display": "\n".join(lines)
            }
        )

    def close_file(self, path: str, current_dir: str | None = None, window_title: str | None = None) -> dict:
        logger.info(f"Fermeture cible : '{path}'")
        requested_path = self._sanitize_open_query((path or "").strip().strip('"').strip("'"))
        target_path = self._resolve_existing_path(requested_path, current_dir=current_dir)

        if target_path is None and requested_path:
            search_dirs = self._normalize_search_dirs(None, current_dir=current_dir)
            matches = self._search_entries(
                requested_path.lower(),
                search_dirs,
                target_type="any",
                max_results=5,
            )
            if len(matches) == 1:
                target_path = Path(matches[0]["path"])

        title_candidates = []
        for candidate in [window_title, requested_path]:
            if candidate:
                title_candidates.append(str(candidate))
        if target_path is not None:
            title_candidates.append(target_path.name)
            if target_path.is_file() and target_path.stem != target_path.name:
                title_candidates.append(target_path.stem)

        from modules.window_manager import WindowManager

        result = WindowManager().close_window(
            query=requested_path,
            preferred_kind="folder" if target_path is not None and target_path.is_dir() else None,
            title_candidates=title_candidates,
            title=window_title,
        )
        if not result.get("success"):
            data = dict(result.get("data") or {})
            data.setdefault("path", str(target_path) if target_path else requested_path)
            data.setdefault("title_candidates", [title for title in title_candidates if title])
            result["data"] = data
            return result

        data = dict(result.get("data") or {})
        data["closed_path"] = str(target_path) if target_path else requested_path
        result["data"] = data
        return result

    def get_file_info(self, path: str) -> dict:
        """Retourne les informations détaillées d'un fichier."""
        file_path = Path(path)
        if not file_path.exists():
            return self._err(f"Fichier introuvable : '{path}'")
        info = self._file_info_dict(file_path)
        lines = [
            f"  Nom       : {info['name']}",
            f"  Chemin    : {info['path']}",
            f"  Taille    : {info['size_str']}",
            f"  Type      : {info['extension']}",
            f"  Modifié   : {info['modified']}",
            f"  Créé      : {info['created']}",
        ]
        info["display"] = "\n".join(lines)
        return self._ok(f"Informations : '{file_path.name}'", info)

    # ══════════════════════════════════════════════════════════════════════════
    #  JEUDI — Opérations sur fichiers (copier, déplacer, renommer, supprimer)
    # ══════════════════════════════════════════════════════════════════════════

    def copy_file(self, src: str, dst: str, overwrite: bool = False) -> dict:
        """
        Copie un fichier ou un dossier vers une destination.

        Args:
            src       : chemin source (fichier ou dossier)
            dst       : chemin destination (fichier ou dossier cible)
            overwrite : écraser si la destination existe déjà

        Exemples :
            copy_file("C:/rapport.docx", "C:/Backup/")
            copy_file("C:/dossier_src", "C:/dossier_dst")
        """
        logger.info(f"Copie : '{src}' → '{dst}'")
        src_path = Path(src)
        dst_path = Path(dst)

        if not src_path.exists():
            return self._err(f"Source introuvable : '{src}'")

        # Si dst est un dossier existant → copier dedans
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name

        # Vérifier écrasement
        if dst_path.exists() and not overwrite:
            return self._err(
                f"La destination '{dst_path.name}' existe déjà. "
                f"Utilise overwrite=True pour écraser.",
                {"destination_exists": True, "dst": str(dst_path)}
            )

        try:
            # Créer les dossiers parents si nécessaires
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            if src_path.is_dir():
                shutil.copytree(str(src_path), str(dst_path),
                                dirs_exist_ok=overwrite)
                item_type = "dossier"
            else:
                shutil.copy2(str(src_path), str(dst_path))
                item_type = "fichier"

            size_str = self._format_size(dst_path.stat().st_size) \
                       if dst_path.is_file() else ""
            logger.info(f"Copie réussie : {src_path.name} → {dst_path}")
            return self._ok(
                f"'{src_path.name}' copié vers '{dst_path.parent}' ({size_str}).",
                {"src": str(src_path), "dst": str(dst_path),
                 "type": item_type, "size": size_str}
            )
        except PermissionError:
            return self._err(f"Permission refusée pour copier vers '{dst}'.")
        except shutil.Error as e:
            return self._err(f"Erreur de copie : {str(e)}")
        except Exception as e:
            return self._err(f"Erreur inattendue lors de la copie : {str(e)}")

    def move_file(self, src: str, dst: str, overwrite: bool = False) -> dict:
        """
        Déplace un fichier ou dossier vers une nouvelle destination.

        Args:
            src       : chemin source
            dst       : chemin destination
            overwrite : écraser si destination existe

        Exemples :
            move_file("C:/rapport.docx", "C:/Archives/")
            move_file("C:/ancien_nom.txt", "C:/Documents/nouveau_nom.txt")
        """
        logger.info(f"Déplacement : '{src}' → '{dst}'")
        src_path = Path(src)
        dst_path = Path(dst)

        if not src_path.exists():
            return self._err(f"Source introuvable : '{src}'")

        # Si dst est un dossier → déplacer dedans
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name

        if dst_path.exists() and not overwrite:
            return self._err(
                f"La destination '{dst_path.name}' existe déjà.",
                {"destination_exists": True, "dst": str(dst_path)}
            )

        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            if dst_path.exists() and overwrite:
                if dst_path.is_dir():
                    shutil.rmtree(str(dst_path))
                else:
                    dst_path.unlink()

            shutil.move(str(src_path), str(dst_path))
            logger.info(f"Déplacement réussi : {src_path.name} → {dst_path}")
            return self._ok(
                f"'{src_path.name}' déplacé vers '{dst_path.parent}'.",
                {"src": str(src_path), "dst": str(dst_path),
                 "old_location": str(src_path.parent),
                 "new_location": str(dst_path.parent)}
            )
        except PermissionError:
            return self._err(f"Permission refusée pour déplacer '{src}'.")
        except Exception as e:
            return self._err(f"Erreur déplacement : {str(e)}")

    def rename_file(self, path: str, new_name: str) -> dict:
        """
        Renomme un fichier ou dossier.
        Conserve l'extension originale si new_name n'en a pas.

        Args:
            path     : chemin du fichier à renommer
            new_name : nouveau nom (avec ou sans extension)

        Exemples :
            rename_file("C:/rapport.docx", "rapport_final")
            → C:/rapport_final.docx

            rename_file("C:/rapport.docx", "rapport_final.pdf")
            → C:/rapport_final.pdf
        """
        logger.info(f"Renommage : '{path}' → '{new_name}'")
        file_path = Path(path)

        if not file_path.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        # Conserver l'extension si new_name n'en a pas (et si c'est un fichier)
        new_name = new_name.strip()
        if file_path.is_file() and "." not in new_name:
            new_name = new_name + file_path.suffix

        new_path = file_path.parent / new_name

        if new_path.exists():
            return self._err(
                f"Un fichier '{new_name}' existe déjà dans '{file_path.parent}'."
            )

        try:
            file_path.rename(new_path)
            logger.info(f"Renommé : '{file_path.name}' → '{new_path.name}'")
            return self._ok(
                f"'{file_path.name}' renommé en '{new_path.name}'.",
                {"old_name": str(file_path), "new_name": str(new_path)}
            )
        except PermissionError:
            return self._err(f"Permission refusée pour renommer '{file_path.name}'.")
        except Exception as e:
            return self._err(f"Erreur renommage : {str(e)}")

    def delete_file(self, path: str, confirm: bool = True) -> dict:
        """
        Supprime un fichier ou un dossier.

        Args:
            path    : chemin à supprimer
            confirm : si True (défaut), demande confirmation pour les dossiers

        Sécurité : refuse de supprimer la racine C:/ ou les dossiers système.
        """
        logger.info(f"Suppression : '{path}'")
        file_path = Path(path)

        if not file_path.exists():
            return self._err(f"Fichier/dossier introuvable : '{path}'")

        # Garde-fous de sécurité
        dangerous_paths = [
            Path("C:/"), Path("C:/Windows"), Path("C:/Program Files"),
            Path.home(), Path("/"), Path("/home"), Path("/usr"),
        ]
        if any(file_path.resolve() == dp.resolve() for dp in dangerous_paths
               if dp.exists()):
            return self._err(
                f"SÉCURITÉ : Suppression de '{path}' refusée — dossier protégé.",
                {"blocked": True}
            )

        try:
            if file_path.is_file() or file_path.is_symlink():
                size = file_path.stat().st_size
                file_path.unlink()
                logger.info(f"Fichier supprimé : {file_path.name}")
                return self._ok(
                    f"'{file_path.name}' supprimé ({self._format_size(size)}).",
                    {"deleted": str(file_path), "type": "file",
                     "size": self._format_size(size)}
                )
            elif file_path.is_dir():
                # Compter les éléments avant suppression
                n_items = sum(1 for _ in file_path.rglob("*"))
                shutil.rmtree(str(file_path))
                logger.info(f"Dossier supprimé : {file_path.name} ({n_items} éléments)")
                return self._ok(
                    f"Dossier '{file_path.name}' supprimé ({n_items} élément(s)).",
                    {"deleted": str(file_path), "type": "directory",
                     "items_deleted": n_items}
                )
            else:
                return self._err(f"'{path}' n'est ni un fichier ni un dossier.")

        except PermissionError:
            return self._err(f"Permission refusée pour supprimer '{file_path.name}'.")
        except Exception as e:
            return self._err(f"Erreur suppression : {str(e)}")

    def create_folder(self, path: str) -> dict:
        """
        Crée un dossier et tous ses parents si nécessaire.

        Args:
            path : chemin du dossier à créer

        Exemples :
            create_folder("C:/Projets/JarvisWindows/logs")
        """
        logger.info(f"Création dossier : '{path}'")
        folder = Path(path)

        if folder.exists():
            if folder.is_dir():
                return self._ok(
                    f"Le dossier '{folder.name}' existe déjà.",
                    {"path": str(folder), "already_existed": True}
                )
            return self._err(f"'{path}' existe déjà en tant que fichier.")

        try:
            folder.mkdir(parents=True, exist_ok=True)
            logger.info(f"Dossier créé : {folder}")
            return self._ok(
                f"Dossier '{folder.name}' créé.",
                {"path": str(folder), "parent": str(folder.parent)}
            )
        except PermissionError:
            return self._err(f"Permission refusée pour créer '{path}'.")
        except Exception as e:
            return self._err(f"Erreur création dossier : {str(e)}")

    def search_by_content(self, keyword: str, search_dirs: list = None,
                          extensions: list = None, max_results: int = 20) -> dict:
        """
        Recherche un mot-clé dans le contenu des fichiers texte.

        Args:
            keyword    : mot ou phrase à chercher
            extensions : extensions à scanner (défaut: .txt, .py, .md, .csv, .json)
            max_results: nombre max de fichiers retournés
        """
        logger.info(f"Recherche contenu : '{keyword}'")
        dirs    = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err(
                "Aucun dossier cible valide pour la recherche de contenu.",
                {"requested_search_dirs": [str(d) for d in (search_dirs or [])]}
            )
        exts    = set(extensions or [".txt", ".py", ".md", ".csv", ".json",
                                     ".log", ".xml", ".html", ".cfg", ".ini"])
        results = []
        keyword_lower = keyword.lower()

        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if not item.is_file() or item.suffix.lower() not in exts:
                        continue
                    try:
                        text = item.read_text(encoding="utf-8", errors="ignore")
                        if keyword_lower in text.lower():
                            # Trouver les lignes correspondantes
                            matching_lines = [
                                (i + 1, line.strip())
                                for i, line in enumerate(text.splitlines())
                                if keyword_lower in line.lower()
                            ][:3]  # max 3 extraits par fichier
                            info = self._file_info_dict(item)
                            info["matches"] = matching_lines
                            results.append(info)
                            if len(results) >= max_results:
                                break
                    except (PermissionError, OSError):
                        continue
            except PermissionError:
                continue
            if len(results) >= max_results:
                break

        if not results:
            return self._err(f"Aucun fichier ne contient '{keyword}'.")

        lines = [f"Fichiers contenant '{keyword}' ({len(results)} résultat(s)) :"]
        lines.append("-" * 70)
        for r in results:
            lines.append(f"  {r['name']}  —  {r['path']}")
            for line_no, line_text in r.get("matches", []):
                lines.append(f"    L{line_no}: {line_text[:80]}")

        return self._ok(
            f"{len(results)} fichier(s) contiennent '{keyword}'.",
            {"files": results, "count": len(results),
             "keyword": keyword, "display": "\n".join(lines)}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  UTILITAIRES PRIVÉS
    # ══════════════════════════════════════════════════════════════════════════

    def _normalize_search_dirs(self, search_dirs: list | None, current_dir: str | None = None) -> list[Path]:
        if search_dirs is not None:
            raw_dirs = search_dirs
        elif current_dir:
            raw_dirs = [current_dir]
        else:
            raw_dirs = self.search_dirs
        normalized = []
        for raw_dir in raw_dirs:
            if raw_dir is None:
                continue
            resolved = self._resolve_alias_path(str(raw_dir), current_dir=current_dir)
            if resolved and resolved.exists():
                normalized.append(resolved)
        if normalized:
            return normalized
        if search_dirs is not None or current_dir is not None:
            return []
        return list(self.search_dirs)

    def _resolve_existing_path(self, raw_path: str | None, current_dir: str | None = None) -> Path | None:
        if not raw_path:
            return None

        cleaned = str(raw_path).strip().strip('"').strip("'")
        alias_path = self._resolve_alias_path(cleaned, current_dir=current_dir)
        candidates = []
        if alias_path is not None:
            candidates.append(alias_path)

        try:
            direct = Path(cleaned)
            candidates.append(direct)
            if current_dir and not direct.is_absolute():
                candidates.append(Path(current_dir) / cleaned)
        except OSError:
            return None

        seen = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                return candidate
        return None

    def _resolve_alias_path(self, raw_path: str, current_dir: str | None = None) -> Path | None:
        text = raw_path.strip().strip('"').strip("'")
        if not text:
            return None

        lower = text.lower().rstrip("/\\")
        if current_dir and lower in {"dedans", "dans ce dossier", "dans ce répertoire", "ici", "là", "la"}:
            return Path(current_dir)

        if lower in KNOWN_FOLDER_ALIASES:
            return KNOWN_FOLDER_ALIASES[lower]

        drive_match = re.fullmatch(r"([a-zA-Z]):(?:[\\/]+)?", text)
        if drive_match:
            return Path(f"{drive_match.group(1).upper()}:\\")

        # Accept explicit absolute paths passed by parser/context (e.g. E:\, C:\Users\...)
        try:
            candidate = Path(text)
            if candidate.is_absolute():
                return candidate
        except OSError:
            return None

        return None

    def _search_entries(self, query: str, search_dirs: list[Path], target_type: str, max_results: int = 20) -> list[dict]:
        query = (query or "").strip().strip('"').strip("'")
        if not query:
            return []

        results = []
        query_lower = query.lower()
        query_variants = {query_lower}
        if len(query_lower) >= 4:
            if query_lower.endswith("s"):
                query_variants.add(query_lower[:-1])
            else:
                query_variants.add(query_lower + "s")
        for base_path in search_dirs:
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if target_type == "file" and not item.is_file():
                        continue
                    if target_type == "directory" and not item.is_dir():
                        continue
                    name_lower = item.name.lower()
                    if not any(variant and variant in name_lower for variant in query_variants):
                        continue
                    info = self._file_info_dict(item)
                    info["match_score"] = self._score_match(item, query_lower, base_path)
                    results.append(info)
                    if len(results) >= max_results:
                        break
            except PermissionError:
                continue
            if len(results) >= max_results:
                break

        results.sort(
            key=lambda item: (
                -item.get("match_score", 0),
                len(item.get("path", "")),
                item.get("name", "").lower(),
            )
        )
        for item in results:
            item.pop("match_score", None)
        return results

    @staticmethod
    def _sanitize_open_query(query: str) -> str:
        text = (query or "").strip()
        if not text:
            return text
        text = re.sub(
            r"\s+(du|de|au|aux)\s+(?:le|la|les)?\s*(?:disque|disk|lecteur|drive)\s+[a-z]\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        return text

    def _close_windows_by_title(self, title_candidates: list[str]) -> list[dict]:
        patterns = []
        for title in title_candidates:
            normalized = (title or "").strip()
            if normalized and normalized not in patterns:
                patterns.append(normalized)
        if not patterns:
            return []

        patterns_json = json.dumps(patterns, ensure_ascii=False)
        script = f"""
$patterns = ConvertFrom-Json @'
{patterns_json}
'@
$matched = @()
foreach ($proc in Get-Process | Where-Object {{ $_.MainWindowTitle }}) {{
  $title = $proc.MainWindowTitle
  foreach ($pattern in $patterns) {{
    if ($pattern -and $title -like ('*' + $pattern + '*')) {{
      $matched += $proc
      break
    }}
  }}
}}
$matched = $matched | Sort-Object Id -Unique
$results = @()
foreach ($proc in $matched) {{
  $title = $proc.MainWindowTitle
  try {{ [void]$proc.CloseMainWindow() }} catch {{}}
  Start-Sleep -Milliseconds 600
  try {{ $proc.Refresh() }} catch {{}}
  if (-not $proc.HasExited) {{
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }}
  $results += [PSCustomObject]@{{ ProcessName = $proc.ProcessName; Id = $proc.Id; MainWindowTitle = $title }}
}}
$results | ConvertTo-Json -Compress
"""

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:
            logger.warning(f"Fermeture par fenêtre impossible : {exc}")
            return []

        stdout = (result.stdout or "").strip()
        if result.returncode != 0 or not stdout:
            return []

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return []

        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return payload
        return []

    def _score_match(self, item: Path, query_lower: str, base_dir: Path) -> int:
        score = 0
        name_lower = item.name.lower()
        parent_lower = str(item.parent).lower()
        if name_lower == query_lower:
            score += 100
        elif name_lower.startswith(query_lower):
            score += 60
        elif query_lower in name_lower:
            score += 30
        if str(item).lower().startswith(str(base_dir).lower()):
            score += 5
        if item.is_dir():
            score += 3
        if query_lower in parent_lower:
            score += 8
        return score

    def _format_choice_display(self, choices: list[dict]) -> str:
        lines = ["Choix possibles :", "-" * 70]
        for index, choice in enumerate(choices[:8], start=1):
            kind = "DIR" if choice.get("is_dir") else "FIL"
            lines.append(f"  {index}. [{kind}] {choice['name']}  —  {choice['path']}")
        return "\n".join(lines)

    def _walk_limited(self, base: Path, max_depth: int):
        """
        Générateur qui parcourt un dossier en limitant la profondeur.
        Exclut les dossiers dans EXCLUDED_DIRS.
        """
        for item in base.iterdir():
            if item.is_file():
                yield item
            elif item.is_dir() and item.name not in EXCLUDED_DIRS:
                # Yield directories too so directory-target searches can match.
                yield item
                if max_depth > 1:
                    try:
                        yield from self._walk_limited(item, max_depth - 1)
                    except PermissionError:
                        continue

    def _file_info_dict(self, path: Path) -> dict:
        """Retourne un dictionnaire d'informations sur un fichier/dossier."""
        try:
            stat     = path.stat()
            size     = stat.st_size if path.is_file() else 0
            modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            created  = datetime.datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size, modified, created = 0, "N/A", "N/A"

        resolved = self._safe_resolve_path(path)

        return {
            "name":      path.name,
            "path":      str(path),
            "resolved_path": resolved,
            "parent":    str(path.parent),
            "extension": path.suffix.lower(),
            "size":      size,
            "size_str":  self._format_size(size),
            "modified":  modified,
            "created":   created,
            "is_dir":    path.is_dir(),
        }

    @staticmethod
    def _safe_resolve_path(path: Path) -> str:
        try:
            return str(path.resolve(strict=False))
        except OSError:
            return str(path)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Convertit des octets en taille lisible (KB, MB, GB)."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024**2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024**3:
            return f"{size_bytes / 1024**2:.1f} MB"
        else:
            return f"{size_bytes / 1024**3:.2f} GB"

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}