"""
file_manager.py — Gestion des fichiers et dossiers Windows
===========================================================

SEMAINE 3 — IMPLÉMENTATION DE BASE
  search_file, search_by_type, open_file, list_folder,
  copy_file, move_file, rename_file, delete_file, create_folder,
  search_by_content, get_file_info, find_duplicates

SEMAINE 7 — NIVEAUX 1 À 4 (nouveautés marquées [S7])

  [S7-1] Recherche avancée :
    • search_by_date()     — "trouve tous les PDF de cette semaine"
                             période : today / yesterday / week / month /
                                       year / last_7 / last_30 / last_90
                             + plage arbitraire (date_from, date_to)
    • search_by_size()     — "fichiers > 100 Mo dans Documents"
                             opérateurs : gt / lt / eq / between
    • search_advanced()    — combinaison type + date + taille + dossier
                             "PDF modifiés cette semaine > 500 Ko"

  [S7-2] Organisation automatique :
    • organize_folder()    — trier un dossier par catégorie (images → Images/,
                             documents → Documents/, etc.)
                             "organise mon dossier téléchargements"
                             mode dry_run pour prévisualiser sans bouger
    • clean_empty_folders()— supprimer les sous-dossiers vides

  [S7-3] Renommage en masse :
    • bulk_rename()        — pattern find/replace, préfixe/suffixe,
                             numérotation, date, extension
                             "renomme tous les PNG en ajoutant la date"

  [S7-4] Doublons améliorés :
    • find_duplicates()    — amélioré : groupes de doublons avec actions
                             (garder le plus récent / le plus ancien / demander)
    • delete_duplicates()  — suppression automatique des doublons avec stratégie

CONVENTIONS :
  Toutes les méthodes retournent {"success": bool, "message": str, "data": dict | None}
  [S7] = nouveau en semaine 7
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Literal

from config.logger import get_logger

logger = get_logger(__name__)

# ── Dossiers de recherche par défaut ─────────────────────────────────────────
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
    "desktop":          Path.home() / "Desktop",
    "bureau":           Path.home() / "Desktop",
    "documents":        Path.home() / "Documents",
    "document":         Path.home() / "Documents",
    "downloads":        Path.home() / "Downloads",
    "telechargements":  Path.home() / "Downloads",
    "téléchargements":  Path.home() / "Downloads",
    "telecharger":      Path.home() / "Downloads",
    "pictures":         Path.home() / "Pictures",
    "images":           Path.home() / "Pictures",
    "photos":           Path.home() / "Pictures",
    "music":            Path.home() / "Music",
    "musique":          Path.home() / "Music",
    "videos":           Path.home() / "Videos",
    "vidéos":           Path.home() / "Videos",
    "home":             Path.home(),
    "accueil":          Path.home(),
}

# Dossiers à toujours exclure
EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "Windows", "System32", "SysWOW64", "$Recycle.Bin", "AppData",
}

# Types de fichiers par catégorie
FILE_TYPE_CATEGORIES = {
    "documents": [".docx", ".doc", ".pdf", ".txt", ".odt", ".rtf", ".md"],
    "images":    [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".heic"],
    "videos":    [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"],
    "audio":     [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
    "archives":  [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"],
    "code":      [".py", ".js", ".html", ".css", ".json", ".xml", ".java",
                  ".cpp", ".c", ".h", ".cs", ".php", ".rb", ".go", ".ts",
                  ".jsx", ".tsx", ".vue", ".rs", ".kt", ".swift"],
    "tableurs":  [".xlsx", ".xls", ".csv", ".ods"],
    "slides":    [".pptx", ".ppt", ".odp"],
    "executables": [".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh"],
}

# [S7] Mapping catégorie → nom de dossier cible pour organize_folder
ORGANIZE_TARGET_FOLDERS = {
    "documents": "Documents",
    "images":    "Images",
    "videos":    "Vidéos",
    "audio":     "Musique",
    "archives":  "Archives",
    "code":      "Code",
    "tableurs":  "Tableurs",
    "slides":    "Présentations",
    "executables": "Programmes",
}

# [S7] Périodes de date en langage naturel
DATE_PERIODS = {
    "today":        0,
    "aujourd'hui":  0,
    "hier":         1,
    "yesterday":    1,
    "week":         7,
    "semaine":      7,
    "cette semaine": 7,
    "this week":    7,
    "last_7":       7,
    "7 jours":      7,
    "month":        30,
    "mois":         30,
    "ce mois":      30,
    "this month":   30,
    "last_30":      30,
    "30 jours":     30,
    "last_90":      90,
    "3 mois":       90,
    "year":         365,
    "an":           365,
    "année":        365,
    "this year":    365,
}

# [S8] Catégories métier pour la classification intelligente de documents
DOC_CLASS_LABELS = {
    "cv": [
        "cv", "curriculum", "curriculum vitae", "resume", "résumé", "profil",
    ],
    "lettre_motivation": [
        "lettre motivation", "motivation", "cover letter", "lettre de motivation",
    ],
    "diplome": [
        "diplome", "diplôme", "certificat", "certificate", "attestation", "releve de notes", "relevé de notes",
    ],
    "identite": [
        "carte identite", "carte d identite", "cni", "passport", "passeport", "id card",
    ],
    "portfolio": [
        "portfolio", "projets", "projects", "github", "behance",
    ],
    "administratif": [
        "justificatif", "facture", "quittance", "attestation", "recommandation", "reference",
    ],
}


class FileManager:
    """
    Gestionnaire complet de fichiers et dossiers.
    Toutes les méthodes retournent {success, message, data}.
    """

    def __init__(self, search_dirs: list = None, max_depth: int = 5):
        self.search_dirs = search_dirs or DEFAULT_SEARCH_DIRS
        self.max_depth   = max_depth

    # ══════════════════════════════════════════════════════════════════════════
    #  RECHERCHE DE BASE (semaine 3)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_all_drive_roots() -> list:
        import string
        roots = []
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append(drive)
        return roots

    def search_file(self, name: str, search_dirs: list = None, max_results: int = 20) -> dict:
        """Recherche un fichier par nom (partiel ou complet, insensible à la casse)."""
        logger.info(f"Recherche fichier : '{name}'")
        dirs = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err("Aucun dossier cible valide.", {"requested_search_dirs": [str(d) for d in (search_dirs or [])]})

        name_lower = name.lower().strip()
        if name_lower.startswith("*.") or name_lower.startswith("."):
            return self.search_by_type(name_lower.lstrip("*"))

        results = self._search_entries(name_lower, dirs, target_type="file", max_results=max_results)
        if not results:
            return self._err(f"Aucun fichier trouvé pour '{name}'.", {"query": name, "searched_dirs": [str(d) for d in dirs]})

        results.sort(key=lambda x: x.get("modified", ""), reverse=True)
        display = self._format_results_table(results[:10], f"Fichiers contenant '{name}'")
        return self._ok(f"{len(results)} fichier(s) trouvé(s) pour '{name}'.", {"files": results, "count": len(results), "display": display})

    def search_by_type(self, extension: str, search_dirs: list = None, max_results: int = 50) -> dict:
        """Recherche tous les fichiers d'un type ou d'une catégorie."""
        logger.info(f"Recherche par type : '{extension}'")
        dirs = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err("Aucun dossier cible valide.", {"requested_search_dirs": [str(d) for d in (search_dirs or [])]})

        ext_lower = extension.lower().strip().lstrip("*")
        if not ext_lower.startswith("."):
            ext_lower = "." + ext_lower

        target_exts = None
        cat_key = extension.lower().strip()
        for cat, exts in FILE_TYPE_CATEGORIES.items():
            if cat_key == cat or cat_key.rstrip("s") in cat:
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
        ext_display = ", ".join(target_exts)
        display = self._format_results_table(results[:15], f"Fichiers {ext_display}")
        return self._ok(
            f"{len(results)} fichier(s) {ext_display} trouvé(s).",
            {"files": results, "count": len(results), "extensions": list(target_exts), "display": display}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S7-1] RECHERCHE PAR DATE
    # ══════════════════════════════════════════════════════════════════════════

    def search_by_date(
        self,
        period: str = "week",
        search_dirs: list = None,
        extension: str = None,
        date_from: str = None,
        date_to: str = None,
        max_results: int = 50,
    ) -> dict:
        """
        [S7-1] Recherche des fichiers par date de modification.

        Args:
            period      : "today", "yesterday", "week", "month", "year", "last_7",
                          "last_30", "last_90" — ou laisser vide si date_from/date_to
            extension   : filtrer par extension ou catégorie (".pdf", "images"...)
            date_from   : date de début "YYYY-MM-DD" (optionnel, remplace period)
            date_to     : date de fin   "YYYY-MM-DD" (optionnel, défaut : aujourd'hui)
            max_results : nombre max de résultats

        Exemples :
            search_by_date("week")                         → fichiers de cette semaine
            search_by_date("month", extension=".pdf")      → PDF de ce mois
            search_by_date(date_from="2025-01-01")         → depuis le 1er janvier
            search_by_date("today", extension="documents") → docs d'aujourd'hui
        """
        logger.info(f"Recherche par date : period='{period}' ext='{extension}'")
        dirs = self._normalize_search_dirs(search_dirs)

        # Calculer la plage de dates
        now = datetime.datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if date_from:
            try:
                dt_from = datetime.datetime.fromisoformat(date_from)
            except ValueError:
                return self._err(f"Format date_from invalide : '{date_from}'. Utilise YYYY-MM-DD.")
        else:
            period_key = period.lower().strip() if period else "week"
            days = DATE_PERIODS.get(period_key)
            if days is None:
                # Tenter de parser comme nombre de jours
                try:
                    days = int(re.search(r"\d+", period_key).group())
                except Exception:
                    days = 7
            dt_from = today_start - datetime.timedelta(days=days)

        if date_to:
            try:
                dt_to = datetime.datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59)
            except ValueError:
                return self._err(f"Format date_to invalide : '{date_to}'.")
        else:
            dt_to = now

        # Résoudre les extensions cibles
        target_exts = self._resolve_extensions(extension) if extension else None

        # Scan
        results = []
        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if not item.is_file():
                        continue
                    try:
                        mtime = datetime.datetime.fromtimestamp(item.stat().st_mtime)
                    except OSError:
                        continue
                    if not (dt_from <= mtime <= dt_to):
                        continue
                    if target_exts and item.suffix.lower() not in target_exts:
                        continue
                    results.append(self._file_info_dict(item))
                    if len(results) >= max_results:
                        break
            except PermissionError:
                continue
            if len(results) >= max_results:
                break

        # Trier par date décroissante
        results.sort(key=lambda x: x.get("modified", ""), reverse=True)

        # Construire l'affichage
        period_label = period or f"{date_from} → {date_to}"
        ext_label    = f" ({extension})" if extension else ""
        title = f"Fichiers{ext_label} modifiés : {period_label}"

        if not results:
            return self._err(
                f"Aucun fichier{ext_label} trouvé pour la période '{period_label}'.",
                {"period": period, "date_from": str(dt_from.date()), "date_to": str(dt_to.date())}
            )

        display = self._format_results_table(results[:15], title, show_date=True)
        return self._ok(
            f"{len(results)} fichier(s){ext_label} trouvé(s) pour '{period_label}'.",
            {
                "files":     results,
                "count":     len(results),
                "period":    period,
                "date_from": str(dt_from.date()),
                "date_to":   str(dt_to.date()),
                "extension": extension,
                "display":   display,
            }
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S7-1] RECHERCHE PAR TAILLE
    # ══════════════════════════════════════════════════════════════════════════

    def search_by_size(
        self,
        min_size: int = None,
        max_size: int = None,
        operator: Literal["gt", "lt", "eq", "between"] = "gt",
        unit: Literal["B", "KB", "MB", "GB"] = "MB",
        search_dirs: list = None,
        extension: str = None,
        max_results: int = 50,
    ) -> dict:
        """
        [S7-1] Recherche des fichiers par taille.

        Args:
            min_size  : taille minimale (ou unique selon operator)
            max_size  : taille maximale (pour operator="between")
            operator  : "gt" (>), "lt" (<), "eq" (=), "between" (entre)
            unit      : "B", "KB", "MB", "GB"
            extension : filtrer par type
            max_results

        Exemples :
            search_by_size(100, operator="gt", unit="MB")    → fichiers > 100 MB
            search_by_size(0, operator="eq", unit="B")       → fichiers vides (0 octet)
            search_by_size(1, 10, operator="between", unit="MB") → entre 1 et 10 MB
            search_by_size(500, operator="lt", unit="KB")    → fichiers < 500 KB
        """
        logger.info(f"Recherche par taille : {operator} {min_size} {unit}")
        dirs = self._normalize_search_dirs(search_dirs)

        # Convertir en octets
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        mult = multipliers.get(unit.upper(), 1024**2)
        min_bytes = int(min_size * mult) if min_size is not None else 0
        max_bytes = int(max_size * mult) if max_size is not None else float("inf")

        target_exts = self._resolve_extensions(extension) if extension else None

        def matches_size(size: int) -> bool:
            if operator == "gt":
                return size > min_bytes
            elif operator == "lt":
                return size < min_bytes
            elif operator == "eq":
                return abs(size - min_bytes) < max(1, min_bytes * 0.01)  # ±1%
            elif operator == "between":
                return min_bytes <= size <= max_bytes
            return False

        results = []
        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if not item.is_file():
                        continue
                    try:
                        size = item.stat().st_size
                    except OSError:
                        continue
                    if target_exts and item.suffix.lower() not in target_exts:
                        continue
                    if matches_size(size):
                        results.append(self._file_info_dict(item))
                        if len(results) >= max_results:
                            break
            except PermissionError:
                continue
            if len(results) >= max_results:
                break

        # Trier par taille décroissante
        results.sort(key=lambda x: x.get("size", 0), reverse=True)

        op_labels = {"gt": ">", "lt": "<", "eq": "=", "between": "entre"}
        if operator == "between":
            size_label = f"entre {min_size} et {max_size} {unit}"
        else:
            size_label = f"{op_labels.get(operator, operator)} {min_size} {unit}"

        if not results:
            return self._err(
                f"Aucun fichier de taille {size_label} trouvé.",
                {"operator": operator, "min_size": min_size, "unit": unit}
            )

        display = self._format_results_table(
            results[:15],
            f"Fichiers taille {size_label}",
            show_date=False,
            show_size=True,
        )
        total_size = sum(r.get("size", 0) for r in results)
        return self._ok(
            f"{len(results)} fichier(s) de taille {size_label} ({self._format_size(total_size)} au total).",
            {
                "files":      results,
                "count":      len(results),
                "total_size": total_size,
                "total_str":  self._format_size(total_size),
                "operator":   operator,
                "min_size":   min_size,
                "max_size":   max_size,
                "unit":       unit,
                "display":    display,
            }
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S7-1] RECHERCHE AVANCÉE COMBINÉE
    # ══════════════════════════════════════════════════════════════════════════

    def search_advanced(
        self,
        name: str = None,
        extension: str = None,
        period: str = None,
        date_from: str = None,
        date_to: str = None,
        min_size: int = None,
        max_size: int = None,
        size_unit: str = "MB",
        search_dirs: list = None,
        max_results: int = 50,
    ) -> dict:
        """
        [S7-1] Recherche combinant nom + type + date + taille.

        Exemples :
            search_advanced(extension="pdf", period="week", min_size=500, size_unit="KB")
            → PDF modifiés cette semaine et de plus de 500 Ko

            search_advanced(name="rapport", extension="docx", period="month")
            → fichiers .docx contenant "rapport" modifiés ce mois
        """
        logger.info(f"Recherche avancée : name={name}, ext={extension}, period={period}, size>={min_size}{size_unit}")
        dirs = self._normalize_search_dirs(search_dirs)

        # Plage de dates
        now = datetime.datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        dt_from, dt_to = None, None

        if date_from:
            try:
                dt_from = datetime.datetime.fromisoformat(date_from)
            except ValueError:
                return self._err(f"date_from invalide : '{date_from}'")
        elif period:
            period_key = period.lower().strip()
            days = DATE_PERIODS.get(period_key)
            if days is None:
                try:
                    days = int(re.search(r"\d+", period_key).group())
                except Exception:
                    days = 7
            dt_from = today_start - datetime.timedelta(days=days)

        if date_to:
            try:
                dt_to = datetime.datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            except ValueError:
                return self._err(f"date_to invalide : '{date_to}'")
        elif dt_from:
            dt_to = now

        # Extensions
        target_exts = self._resolve_extensions(extension) if extension else None

        # Taille
        mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(size_unit.upper(), 1024**2)
        min_bytes = int(min_size * mult) if min_size is not None else None
        max_bytes = int(max_size * mult) if max_size is not None else None

        # Nom (insensible à la casse)
        name_lower = name.lower().strip() if name else None

        results = []
        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if not item.is_file():
                        continue

                    # Filtre nom
                    if name_lower and name_lower not in item.name.lower():
                        continue

                    # Filtre extension
                    if target_exts and item.suffix.lower() not in target_exts:
                        continue

                    try:
                        stat = item.stat()
                    except OSError:
                        continue

                    # Filtre date
                    if dt_from:
                        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
                        if not (dt_from <= mtime <= dt_to):
                            continue

                    # Filtre taille
                    if min_bytes is not None and stat.st_size < min_bytes:
                        continue
                    if max_bytes is not None and stat.st_size > max_bytes:
                        continue

                    results.append(self._file_info_dict(item))
                    if len(results) >= max_results:
                        break
            except PermissionError:
                continue
            if len(results) >= max_results:
                break

        results.sort(key=lambda x: x.get("modified", ""), reverse=True)

        # Construire le résumé des filtres appliqués
        filters = []
        if name:        filters.append(f"nom contient '{name}'")
        if extension:   filters.append(f"type={extension}")
        if period:      filters.append(f"période={period}")
        if date_from:   filters.append(f"depuis {date_from}")
        if min_size:    filters.append(f"≥{min_size}{size_unit}")
        if max_size:    filters.append(f"≤{max_size}{size_unit}")
        filters_str = " | ".join(filters) if filters else "aucun filtre"

        if not results:
            return self._err(
                f"Aucun fichier trouvé avec : {filters_str}.",
                {"filters": filters}
            )

        display = self._format_results_table(results[:15], f"Recherche avancée : {filters_str}", show_date=True)
        return self._ok(
            f"{len(results)} fichier(s) trouvé(s) [{filters_str}].",
            {
                "files":      results,
                "count":      len(results),
                "filters":    filters,
                "display":    display,
            }
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S7-2] ORGANISATION AUTOMATIQUE DE DOSSIER
    # ══════════════════════════════════════════════════════════════════════════

    def organize_folder(
        self,
        folder_path: str = None,
        dry_run: bool = False,
        create_other: bool = True,
        skip_subdirs: bool = True,
    ) -> dict:
        """
        [S7-2] Organise un dossier en rangeant les fichiers par catégorie.

        Chaque catégorie de fichiers est déplacée dans un sous-dossier :
            Images/     → .jpg, .png, .gif...
            Documents/  → .pdf, .docx, .txt...
            Musique/    → .mp3, .flac...
            Vidéos/     → .mp4, .mkv...
            Archives/   → .zip, .rar...
            Code/       → .py, .js, .html...
            Tableurs/   → .xlsx, .csv...
            Présentations/ → .pptx...
            Autres/     → tout le reste

        Args:
            folder_path : dossier à organiser (défaut: Downloads)
            dry_run     : si True, simule sans déplacer (aperçu)
            create_other: créer un dossier "Autres" pour les fichiers inclassables
            skip_subdirs: ignorer les fichiers déjà dans un sous-dossier

        Exemples :
            organize_folder()                         → organise Downloads
            organize_folder("C:/Bureau", dry_run=True) → aperçu sans déplacer
        """
        # Résoudre le dossier cible
        if folder_path:
            target = self._resolve_existing_path(folder_path)
            if target is None:
                target = KNOWN_FOLDER_ALIASES.get(folder_path.lower().strip())
        else:
            target = KNOWN_FOLDER_ALIASES["downloads"]

        if target is None or not target.exists():
            return self._err(f"Dossier '{folder_path}' introuvable.")
        if not target.is_dir():
            return self._err(f"'{folder_path}' n'est pas un dossier.")

        logger.info(f"Organisation dossier : '{target}' (dry_run={dry_run})")

        # Construire l'index : extension → catégorie
        ext_to_category = {}
        for cat, exts in FILE_TYPE_CATEGORIES.items():
            for ext in exts:
                ext_to_category[ext] = cat

        # Lister les fichiers directs du dossier (pas les sous-dossiers)
        try:
            all_items = list(target.iterdir())
        except PermissionError:
            return self._err(f"Accès refusé à '{target}'.")

        files_to_move = [
            i for i in all_items
            if i.is_file() and not i.name.startswith(".")
        ]

        if not files_to_move:
            return self._ok(
                f"Dossier '{target.name}' déjà vide ou sans fichiers à organiser.",
                {"folder": str(target), "moved": 0}
            )

        # Plan de déplacement
        plan: list[dict] = []
        skipped: list[str] = []

        for file in files_to_move:
            ext = file.suffix.lower()
            category = ext_to_category.get(ext)
            if category:
                dest_subdir = ORGANIZE_TARGET_FOLDERS[category]
            elif create_other:
                dest_subdir = "Autres"
            else:
                skipped.append(file.name)
                continue

            dest_dir  = target / dest_subdir
            dest_path = dest_dir / file.name

            # Gérer les conflits de nom
            if dest_path.exists():
                stem = file.stem
                suffix = file.suffix
                counter = 1
                while dest_path.exists():
                    dest_path = dest_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            plan.append({
                "src":      str(file),
                "dst":      str(dest_path),
                "name":     file.name,
                "category": dest_subdir,
                "size_str": self._format_size(file.stat().st_size if file.exists() else 0),
            })

        if not plan:
            return self._ok(
                "Aucun fichier à déplacer.",
                {"folder": str(target), "moved": 0, "skipped": skipped}
            )

        # Résumé par catégorie
        by_cat: dict[str, list] = {}
        for item in plan:
            by_cat.setdefault(item["category"], []).append(item["name"])

        # Aperçu
        lines = [f"📂 Organisation de '{target.name}'" + (" [APERÇU — rien n'est déplacé]" if dry_run else ""), "─" * 60]
        for cat, names in sorted(by_cat.items()):
            lines.append(f"  📁 {cat}/ ({len(names)} fichier(s))")
            for n in names[:5]:
                lines.append(f"      • {n}")
            if len(names) > 5:
                lines.append(f"      ... et {len(names) - 5} autre(s)")
        if skipped:
            lines.append(f"  ⏭  Ignorés : {len(skipped)} fichier(s)")

        if dry_run:
            return self._ok(
                f"Aperçu : {len(plan)} fichier(s) seraient déplacés en {len(by_cat)} catégorie(s). "
                "Dis 'organise mon dossier téléchargements' pour confirmer.",
                {
                    "folder":    str(target),
                    "plan":      plan,
                    "by_cat":    {k: len(v) for k, v in by_cat.items()},
                    "total":     len(plan),
                    "skipped":   skipped,
                    "dry_run":   True,
                    "display":   "\n".join(lines),
                }
            )

        # Exécuter les déplacements
        moved = 0
        errors = []
        for item in plan:
            src  = Path(item["src"])
            dst  = Path(item["dst"])
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved += 1
                logger.info(f"Organisé : {src.name} → {dst.parent.name}/")
            except PermissionError:
                errors.append(f"Permission refusée : {src.name}")
            except Exception as e:
                errors.append(f"{src.name} : {e}")

        lines.append("")
        lines.append(f"✅ {moved}/{len(plan)} fichier(s) déplacé(s).")
        if errors:
            lines.append(f"⚠️  {len(errors)} erreur(s) :")
            for err in errors[:5]:
                lines.append(f"   • {err}")

        return self._ok(
            f"Organisation terminée : {moved} fichier(s) déplacé(s) en {len(by_cat)} catégorie(s).",
            {
                "folder":  str(target),
                "moved":   moved,
                "errors":  errors,
                "by_cat":  {k: len(v) for k, v in by_cat.items()},
                "total":   len(plan),
                "display": "\n".join(lines),
            }
        )

    def clean_empty_folders(self, folder_path: str = None, dry_run: bool = False) -> dict:
        """
        [S7-2] Supprime les sous-dossiers vides dans un dossier.

        Args:
            folder_path : dossier racine (défaut: Downloads)
            dry_run     : si True, liste sans supprimer

        Exemple :
            clean_empty_folders("C:/Bureau")
        """
        if folder_path:
            target = self._resolve_existing_path(folder_path)
        else:
            target = KNOWN_FOLDER_ALIASES["downloads"]

        if not target or not target.exists():
            return self._err(f"Dossier '{folder_path}' introuvable.")

        logger.info(f"Nettoyage dossiers vides : '{target}' (dry_run={dry_run})")

        empty_dirs = []
        # Parcourir de bas en haut pour supprimer les chaînes vides
        for dirpath, dirnames, filenames in os.walk(str(target), topdown=False):
            d = Path(dirpath)
            if d == target:
                continue  # Ne jamais supprimer le dossier racine
            if d.name in EXCLUDED_DIRS:
                continue
            try:
                if not any(d.iterdir()):
                    empty_dirs.append(d)
            except PermissionError:
                continue

        if not empty_dirs:
            return self._ok(
                f"Aucun dossier vide trouvé dans '{target.name}'.",
                {"folder": str(target), "deleted": 0}
            )

        lines = [f"{'📋 Aperçu' if dry_run else '🗑️  Suppression'} des dossiers vides dans '{target.name}':", "─" * 50]
        for d in empty_dirs:
            lines.append(f"  • {d.relative_to(target)}")

        if dry_run:
            return self._ok(
                f"{len(empty_dirs)} dossier(s) vide(s) trouvé(s). Dis 'nettoie les dossiers vides' pour supprimer.",
                {"folder": str(target), "empty_dirs": [str(d) for d in empty_dirs], "count": len(empty_dirs), "dry_run": True, "display": "\n".join(lines)}
            )

        deleted = 0
        errors = []
        for d in empty_dirs:
            try:
                d.rmdir()
                deleted += 1
                logger.info(f"Dossier vide supprimé : {d}")
            except Exception as e:
                errors.append(f"{d.name} : {e}")

        lines.append(f"\n✅ {deleted} dossier(s) supprimé(s).")
        return self._ok(
            f"{deleted} dossier(s) vide(s) supprimé(s) dans '{target.name}'.",
            {"folder": str(target), "deleted": deleted, "errors": errors, "display": "\n".join(lines)}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S7-3] RENOMMAGE EN MASSE
    # ══════════════════════════════════════════════════════════════════════════

    def bulk_rename(
        self,
        folder_path: str,
        pattern: str = None,
        replacement: str = "",
        prefix: str = "",
        suffix: str = "",
        add_date: bool = False,
        add_number: bool = False,
        number_start: int = 1,
        number_padding: int = 2,
        new_extension: str = None,
        extension_filter: str = None,
        dry_run: bool = True,
    ) -> dict:
        """
        [S7-3] Renomme en masse les fichiers d'un dossier selon un pattern.

        Args:
            folder_path      : dossier contenant les fichiers à renommer
            pattern          : texte (ou regex) à remplacer dans le nom
            replacement      : remplacement pour `pattern`
            prefix           : texte à ajouter au début du nom
            suffix           : texte à ajouter à la fin (avant extension)
            add_date         : ajouter la date de modification (YYYY-MM-DD) au nom
            add_number       : ajouter un numéro séquentiel au nom
            number_start     : numéro de départ (défaut: 1)
            number_padding   : zéros pour le numéro (2 → 01, 02...)
            new_extension    : changer l'extension de tous les fichiers
            extension_filter : ne renommer que les fichiers de ce type
            dry_run          : si True (défaut), prévisualise sans renommer

        Exemples :
            bulk_rename("C:/Photos", add_date=True, extension_filter=".jpg")
            → photo.jpg → 2025-01-15_photo.jpg

            bulk_rename("C:/Docs", pattern="rapport", replacement="report")
            → rapport_Q1.pdf → report_Q1.pdf

            bulk_rename("C:/Serie", add_number=True, prefix="Episode_", dry_run=False)
            → fichier.mp4 → Episode_01_fichier.mp4
        """
        target = self._resolve_existing_path(folder_path)
        if target is None:
            target = KNOWN_FOLDER_ALIASES.get(folder_path.lower().strip())
        if not target or not target.exists():
            return self._err(f"Dossier '{folder_path}' introuvable.")

        logger.info(f"Renommage en masse : '{target}' (dry_run={dry_run})")

        # Lister les fichiers à renommer
        try:
            all_items = list(target.iterdir())
        except PermissionError:
            return self._err(f"Accès refusé à '{target}'.")

        files = sorted(
            [i for i in all_items if i.is_file() and not i.name.startswith(".")],
            key=lambda x: x.name.lower()
        )

        # Filtre par extension
        if extension_filter:
            target_exts = self._resolve_extensions(extension_filter)
            files = [f for f in files if f.suffix.lower() in target_exts]

        if not files:
            return self._err(f"Aucun fichier à renommer dans '{target.name}'.")

        plan = []
        counter = number_start
        fmt = f"{{:0{number_padding}d}}"

        for file in files:
            stem  = file.stem
            ext   = file.suffix

            # 1. Remplacement par pattern
            if pattern:
                try:
                    new_stem = re.sub(pattern, replacement, stem, flags=re.IGNORECASE)
                except re.error:
                    new_stem = stem.replace(pattern, replacement)
            else:
                new_stem = stem

            # 2. Numérotation
            num_str = fmt.format(counter) if add_number else ""
            counter += 1

            # 3. Date de modification
            try:
                mtime = datetime.datetime.fromtimestamp(file.stat().st_mtime)
                date_str = mtime.strftime("%Y-%m-%d") if add_date else ""
            except OSError:
                date_str = ""

            # 4. Assemblage : prefix + [num_] + [date_] + stem + suffix + ext
            parts = []
            if prefix:      parts.append(prefix)
            if num_str:     parts.append(num_str)
            if date_str:    parts.append(date_str)
            parts.append(new_stem)
            if suffix:      parts.append(suffix)

            # Joindre avec "_" si plusieurs segments
            new_stem_final = "_".join(p.strip("_") for p in parts if p)

            # 5. Extension
            new_ext = ("." + new_extension.lstrip(".")) if new_extension else ext
            new_name = new_stem_final + new_ext

            # Sanitiser le nom (supprimer caractères interdits Windows)
            new_name = re.sub(r'[<>:"/\\|?*]', "_", new_name)

            new_path = target / new_name

            if new_name == file.name:
                continue  # Pas de changement

            plan.append({
                "src":      str(file),
                "dst":      str(new_path),
                "old_name": file.name,
                "new_name": new_name,
                "conflict": new_path.exists() and str(new_path) != str(file),
            })

        if not plan:
            return self._ok(
                "Aucun fichier ne sera renommé (noms identiques).",
                {"folder": str(target), "renamed": 0}
            )

        conflicts = [p for p in plan if p["conflict"]]
        lines = [
            f"{'📋 Aperçu' if dry_run else '✏️  Renommage'} dans '{target.name}' ({len(plan)} fichier(s))",
            "─" * 65,
        ]
        for item in plan[:20]:
            marker = "⚠️ " if item["conflict"] else "  "
            lines.append(f"{marker}{item['old_name'][:35]:<35} → {item['new_name']}")
        if len(plan) > 20:
            lines.append(f"  ... et {len(plan) - 20} autre(s)")
        if conflicts:
            lines.append(f"\n⚠️  {len(conflicts)} conflit(s) de noms détecté(s) — ces fichiers ne seront PAS renommés.")

        if dry_run:
            return self._ok(
                f"Aperçu : {len(plan)} fichier(s) seraient renommés. "
                "Ajoute dry_run=False pour confirmer.",
                {
                    "folder":    str(target),
                    "plan":      plan,
                    "total":     len(plan),
                    "conflicts": len(conflicts),
                    "dry_run":   True,
                    "display":   "\n".join(lines),
                }
            )

        # Exécuter les renommages (en ignorant les conflits)
        renamed = 0
        errors  = []
        for item in plan:
            if item["conflict"]:
                errors.append(f"Conflit : '{item['old_name']}'")
                continue
            src = Path(item["src"])
            dst = Path(item["dst"])
            try:
                src.rename(dst)
                renamed += 1
                logger.info(f"Renommé : {item['old_name']} → {item['new_name']}")
            except PermissionError:
                errors.append(f"Permission refusée : {item['old_name']}")
            except Exception as e:
                errors.append(f"{item['old_name']} : {e}")

        lines.append(f"\n✅ {renamed} fichier(s) renommé(s).")
        if errors:
            lines.append(f"⚠️  {len(errors)} erreur(s).")

        return self._ok(
            f"{renamed}/{len(plan)} fichier(s) renommé(s) dans '{target.name}'.",
            {
                "folder":  str(target),
                "renamed": renamed,
                "errors":  errors,
                "display": "\n".join(lines),
            }
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S7-4] DOUBLONS — AMÉLIORÉ
    # ══════════════════════════════════════════════════════════════════════════

    def find_duplicates(
        self,
        search_dirs: list = None,
        extension: str = None,
        min_size: int = 1,
        max_results: int = 100,
    ) -> dict:
        """
        [S7-4] Trouve les fichiers en double par hash MD5.

        Améliorations semaine 7 :
          - Groupes de doublons avec aperçu (taille, date, chemin)
          - Recommandation automatique (garder le + récent)
          - Préparation pour delete_duplicates()

        Args:
            search_dirs : dossiers à scanner
            extension   : filtrer par type
            min_size    : taille minimale en octets (évite les fichiers vides)
            max_results : nombre max de groupes de doublons

        Exemple :
            find_duplicates(extension="images")
            → groupes de photos en double avec recommandation
        """
        logger.info("Recherche de doublons...")
        dirs = self._normalize_search_dirs(search_dirs)
        target_exts = self._resolve_extensions(extension) if extension else None

        # Phase 1 : grouper par taille (rapide)
        size_groups: dict[int, list[Path]] = {}
        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if not item.is_file():
                        continue
                    if target_exts and item.suffix.lower() not in target_exts:
                        continue
                    try:
                        size = item.stat().st_size
                        if size < min_size:
                            continue
                        size_groups.setdefault(size, []).append(item)
                    except OSError:
                        continue
            except PermissionError:
                continue

        # Phase 2 : parmi les fichiers de même taille, calculer le hash MD5
        hash_groups: dict[str, list[Path]] = {}
        for size, paths in size_groups.items():
            if len(paths) < 2:
                continue
            for path in paths:
                try:
                    md5 = self._md5(path)
                    hash_groups.setdefault(md5, []).append(path)
                except (OSError, PermissionError):
                    continue

        # Filtrer les vrais doublons (≥2 fichiers avec même hash)
        duplicate_groups = [
            paths for paths in hash_groups.values() if len(paths) >= 2
        ]
        duplicate_groups.sort(key=lambda g: -g[0].stat().st_size)
        duplicate_groups = duplicate_groups[:max_results]

        if not duplicate_groups:
            return self._ok(
                "Aucun doublon trouvé.",
                {"groups": [], "count": 0, "wasted_space": 0}
            )

        # Construire le rapport avec recommandations
        groups_data = []
        total_wasted = 0

        for group in duplicate_groups:
            files_info = []
            for path in group:
                try:
                    stat = path.stat()
                    files_info.append({
                        "path":     str(path),
                        "name":     path.name,
                        "size":     stat.st_size,
                        "size_str": self._format_size(stat.st_size),
                        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "mtime_ts": stat.st_mtime,
                    })
                except OSError:
                    continue

            if len(files_info) < 2:
                continue

            # Recommandation : garder le + récent
            files_info.sort(key=lambda f: f["mtime_ts"], reverse=True)
            keep = files_info[0]
            to_delete = files_info[1:]
            wasted = sum(f["size"] for f in to_delete)
            total_wasted += wasted

            groups_data.append({
                "hash":      "",  # Ne pas exposer le hash
                "size":      files_info[0]["size"],
                "size_str":  files_info[0]["size_str"],
                "count":     len(files_info),
                "files":     files_info,
                "keep":      keep,
                "to_delete": to_delete,
                "wasted":    wasted,
                "wasted_str": self._format_size(wasted),
            })

        # Affichage
        lines = [f"🔍 Doublons trouvés : {len(groups_data)} groupe(s)", "─" * 65]
        for i, g in enumerate(groups_data[:10], 1):
            lines.append(f"  Groupe {i} — {g['size_str']} × {g['count']} copies ({g['wasted_str']} gaspillés)")
            lines.append(f"    ✅ Garder : {g['keep']['path']}")
            for f in g["to_delete"][:3]:
                lines.append(f"    🗑️  Suppr.  : {f['path']}")
            if len(g["to_delete"]) > 3:
                lines.append(f"    ... et {len(g['to_delete'])-3} autre(s)")

        lines.append(f"\n💾 Espace récupérable : {self._format_size(total_wasted)}")
        lines.append("Dis 'supprime les doublons' pour libérer cet espace.")

        return self._ok(
            f"{len(groups_data)} groupe(s) de doublons — {self._format_size(total_wasted)} récupérables.",
            {
                "groups":        groups_data,
                "group_count":   len(groups_data),
                "total_files":   sum(g["count"] for g in groups_data),
                "wasted_space":  total_wasted,
                "wasted_str":    self._format_size(total_wasted),
                "display":       "\n".join(lines),
            }
        )

    def delete_duplicates(
        self,
        search_dirs: list = None,
        extension: str = None,
        strategy: Literal["keep_newest", "keep_oldest", "keep_shortest_path"] = "keep_newest",
        dry_run: bool = True,
    ) -> dict:
        """
        [S7-4] Supprime les doublons automatiquement selon une stratégie.

        Args:
            strategy  : "keep_newest" (garder le + récent, défaut)
                        "keep_oldest" (garder le + ancien)
                        "keep_shortest_path" (garder celui avec le chemin le + court)
            dry_run   : si True (défaut), liste sans supprimer

        Exemple :
            delete_duplicates(strategy="keep_newest", dry_run=False)
        """
        # D'abord trouver les doublons
        found = self.find_duplicates(search_dirs=search_dirs, extension=extension)
        if not found["success"] or not found.get("data", {}).get("groups"):
            return found

        groups = found["data"]["groups"]
        to_delete_all = []

        for group in groups:
            files = group["files"]
            if strategy == "keep_newest":
                files_sorted = sorted(files, key=lambda f: f["mtime_ts"], reverse=True)
            elif strategy == "keep_oldest":
                files_sorted = sorted(files, key=lambda f: f["mtime_ts"])
            elif strategy == "keep_shortest_path":
                files_sorted = sorted(files, key=lambda f: len(f["path"]))
            else:
                files_sorted = files

            to_delete_all.extend(files_sorted[1:])  # Garder le premier, supprimer les autres

        if not to_delete_all:
            return self._ok("Aucun doublon à supprimer.", {"deleted": 0})

        lines = [
            f"{'📋 Aperçu' if dry_run else '🗑️  Suppression'} des doublons (stratégie: {strategy})",
            f"Fichiers à supprimer : {len(to_delete_all)}",
            "─" * 60,
        ]
        for f in to_delete_all[:20]:
            lines.append(f"  • {f['path']}")
        if len(to_delete_all) > 20:
            lines.append(f"  ... et {len(to_delete_all)-20} autre(s)")
        total_size = sum(f["size"] for f in to_delete_all)
        lines.append(f"\n💾 Espace libéré : {self._format_size(total_size)}")

        if dry_run:
            return self._ok(
                f"Aperçu : {len(to_delete_all)} doublon(s) seraient supprimés "
                f"({self._format_size(total_size)} libérés). "
                "Ajoute dry_run=False pour confirmer.",
                {
                    "to_delete": to_delete_all,
                    "count":     len(to_delete_all),
                    "size":      total_size,
                    "size_str":  self._format_size(total_size),
                    "dry_run":   True,
                    "display":   "\n".join(lines),
                }
            )

        # Suppression effective
        deleted = 0
        freed   = 0
        errors  = []
        for f in to_delete_all:
            try:
                p = Path(f["path"])
                size = f.get("size", 0)
                p.unlink()
                deleted += 1
                freed   += size
                logger.info(f"Doublon supprimé : {p.name}")
            except PermissionError:
                errors.append(f"Permission refusée : {f['path']}")
            except FileNotFoundError:
                pass  # Déjà supprimé
            except Exception as e:
                errors.append(f"{f['path']} : {e}")

        lines.append(f"\n✅ {deleted} fichier(s) supprimé(s) — {self._format_size(freed)} libérés.")

        return self._ok(
            f"{deleted} doublon(s) supprimé(s) — {self._format_size(freed)} libérés.",
            {
                "deleted": deleted,
                "freed":   freed,
                "freed_str": self._format_size(freed),
                "errors":  errors,
                "display": "\n".join(lines),
            }
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S8-1] CLASSIFICATION INTELLIGENTE DE DOCUMENTS
    # ══════════════════════════════════════════════════════════════════════════

    def classify_documents(
        self,
        search_dirs: list = None,
        extension: str = None,
        max_results: int = 100,
        move_files: bool = False,
        target_root: str = None,
    ) -> dict:
        """
        [S8-1] Classifie intelligemment les documents par contenu (et nom).

        Stratégie légère, robuste et locale :
          1) score par nom de fichier
          2) score par texte extrait (txt/md/csv/json)
          3) fallback par type pour les fichiers non lisibles
        """
        dirs = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err("Aucun dossier cible valide.")

        target_exts = self._resolve_extensions(extension) if extension else None
        if not target_exts:
            target_exts = set(FILE_TYPE_CATEGORIES["documents"]) | set(FILE_TYPE_CATEGORIES["tableurs"]) | set(FILE_TYPE_CATEGORIES["slides"])

        candidates: list[Path] = []
        for base_dir in dirs:
            base_path = Path(base_dir)
            if not base_path.exists():
                continue
            try:
                for item in self._walk_limited(base_path, self.max_depth):
                    if item.is_file() and item.suffix.lower() in target_exts:
                        candidates.append(item)
                        if len(candidates) >= max_results:
                            break
            except PermissionError:
                continue
            if len(candidates) >= max_results:
                break

        if not candidates:
            return self._err("Aucun document à classifier trouvé.")

        rows = []
        grouped: dict[str, list[dict]] = {}

        for file_path in candidates:
            label, score, reasons = self._classify_single_document(file_path)
            info = self._file_info_dict(file_path)
            row = {
                "path": info["path"],
                "name": info["name"],
                "extension": info["extension"],
                "size": info["size"],
                "size_str": info["size_str"],
                "modified": info["modified"],
                "category": label,
                "confidence": round(score, 2),
                "reasons": reasons,
            }
            rows.append(row)
            grouped.setdefault(label, []).append(row)

        moved = 0
        move_errors = []
        if move_files:
            root = Path(target_root) if target_root else Path.home() / "Documents" / "Jarvis_Classified"
            root.mkdir(parents=True, exist_ok=True)
            for row in rows:
                src = Path(row["path"])
                dst_dir = root / row["category"]
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / src.name
                if dst.exists():
                    stem, suf = src.stem, src.suffix
                    i = 1
                    while dst.exists():
                        dst = dst_dir / f"{stem}_{i}{suf}"
                        i += 1
                try:
                    shutil.move(str(src), str(dst))
                    moved += 1
                    row["moved_to"] = str(dst)
                except Exception as e:
                    move_errors.append(f"{src.name}: {e}")

        lines = [f"Classification intelligente ({len(rows)} document(s))", "-" * 62]
        for cat in sorted(grouped.keys()):
            lines.append(f"  {cat}: {len(grouped[cat])}")
        if move_files:
            lines.append(f"\nDéplacés: {moved}/{len(rows)}")
            if move_errors:
                lines.append(f"Erreurs: {len(move_errors)}")

        return self._ok(
            f"Classification terminée: {len(rows)} document(s) analysé(s).",
            {
                "documents": rows,
                "count": len(rows),
                "by_category": {k: len(v) for k, v in grouped.items()},
                "moved": moved,
                "move_errors": move_errors,
                "display": "\n".join(lines),
            },
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S8-2] WORKFLOW CANDIDATURE (CV + LETTRE + ZIP)
    # ══════════════════════════════════════════════════════════════════════════

    def prepare_application_package(
        self,
        search_dirs: list = None,
        output_dir: str = None,
        package_name: str = "dossier_candidature",
        include_categories: list = None,
        dry_run: bool = True,
    ) -> dict:
        """
        [S8-2] Workflow complet : trouve CV + lettre + docs clés, puis crée un ZIP.
        """
        include_categories = include_categories or ["cv", "lettre_motivation", "diplome", "portfolio", "administratif"]

        classified = self.classify_documents(
            search_dirs=search_dirs,
            extension="documents",
            max_results=200,
            move_files=False,
        )
        if not classified.get("success"):
            return classified

        docs = (classified.get("data") or {}).get("documents", [])
        selected = [d for d in docs if d.get("category") in include_categories and d.get("confidence", 0) >= 0.25]

        # Prioriser CV et lettre en premier
        selected.sort(key=lambda d: (
            0 if d.get("category") == "cv" else 1 if d.get("category") == "lettre_motivation" else 2,
            -float(d.get("confidence", 0)),
            d.get("name", "").lower(),
        ))

        cv_count = sum(1 for d in selected if d.get("category") == "cv")
        letter_count = sum(1 for d in selected if d.get("category") == "lettre_motivation")
        if cv_count == 0 or letter_count == 0:
            return self._err(
                "Dossier incomplet: CV ou lettre de motivation introuvable.",
                {
                    "selected": selected,
                    "cv_found": cv_count,
                    "letter_found": letter_count,
                    "hint": "Renomme les fichiers avec 'cv' et 'lettre motivation' ou place-les dans Documents.",
                },
            )

        out_root = Path(output_dir) if output_dir else (Path.home() / "Documents" / "JarvisPackages")
        out_root.mkdir(parents=True, exist_ok=True)
        package_dir = out_root / package_name
        zip_path = out_root / f"{package_name}.zip"

        lines = [f"Préparation candidature: {len(selected)} fichier(s) sélectionné(s)", "-" * 62]
        for d in selected[:20]:
            lines.append(f"  - [{d['category']}] {d['name']}")
        if len(selected) > 20:
            lines.append(f"  ... et {len(selected)-20} autre(s)")

        if dry_run:
            lines.append("\nMode aperçu: aucun fichier copié, aucun zip créé.")
            return self._ok(
                "Aperçu candidature prêt. Passe dry_run=False pour créer le ZIP.",
                {
                    "selected": selected,
                    "count": len(selected),
                    "output_dir": str(out_root),
                    "zip_path": str(zip_path),
                    "dry_run": True,
                    "display": "\n".join(lines),
                },
            )

        # Build package directory
        if package_dir.exists():
            shutil.rmtree(str(package_dir), ignore_errors=True)
        package_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        copy_errors = []
        for d in selected:
            src = Path(d["path"])
            if not src.exists():
                continue
            cat_dir = package_dir / d["category"]
            cat_dir.mkdir(parents=True, exist_ok=True)
            dst = cat_dir / src.name
            if dst.exists():
                stem, suf = src.stem, src.suffix
                i = 1
                while dst.exists():
                    dst = cat_dir / f"{stem}_{i}{suf}"
                    i += 1
            try:
                shutil.copy2(str(src), str(dst))
                copied += 1
            except Exception as e:
                copy_errors.append(f"{src.name}: {e}")

        try:
            if zip_path.exists():
                zip_path.unlink()
            with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for item in package_dir.rglob("*"):
                    if item.is_file():
                        zf.write(str(item), arcname=str(item.relative_to(package_dir)))
            zip_size = self._format_size(zip_path.stat().st_size)
        except Exception as e:
            return self._err(f"Création ZIP échouée: {e}", {"package_dir": str(package_dir)})

        lines.append(f"\nPackage créé: {package_dir}")
        lines.append(f"ZIP: {zip_path} ({zip_size})")
        if copy_errors:
            lines.append(f"Erreurs de copie: {len(copy_errors)}")

        return self._ok(
            f"Dossier candidature prêt: {copied} fichier(s), ZIP créé ({zip_size}).",
            {
                "selected": selected,
                "copied": copied,
                "copy_errors": copy_errors,
                "package_dir": str(package_dir),
                "zip_path": str(zip_path),
                "zip_size": zip_size,
                "display": "\n".join(lines),
            },
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S8-3] SYNC BASIQUE GOOGLE DRIVE
    # ══════════════════════════════════════════════════════════════════════════

    def sync_to_google_drive(
        self,
        source: str,
        drive_folder: str = None,
        mode: Literal["copy", "mirror"] = "copy",
        dry_run: bool = True,
    ) -> dict:
        """
        [S8-3] Synchronisation locale basique vers dossier Google Drive desktop.
        """
        src = self._resolve_existing_path(source)
        if src is None or not src.exists() or not src.is_dir():
            return self._err(f"Dossier source invalide: '{source}'.")

        drive_root = None
        if drive_folder:
            cand = self._resolve_existing_path(drive_folder)
            if cand and cand.exists() and cand.is_dir():
                drive_root = cand
        if drive_root is None:
            drive_candidates = [
                Path.home() / "Google Drive",
                Path.home() / "My Drive",
                Path.home() / "Mon Drive",
                Path.home() / "Documents" / "Google Drive",
            ]
            for cand in drive_candidates:
                if cand.exists() and cand.is_dir():
                    drive_root = cand
                    break

        if drive_root is None:
            return self._err(
                "Google Drive local introuvable. Spécifie drive_folder explicitement.",
                {"hint": "Ex: drive_folder='C:/Users/USER/Google Drive'"},
            )

        target = drive_root / src.name
        plan = []
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(src)
            dst = target / rel
            action = "copy"
            if dst.exists():
                try:
                    src_m = item.stat().st_mtime
                    dst_m = dst.stat().st_mtime
                    src_s = item.stat().st_size
                    dst_s = dst.stat().st_size
                    if abs(src_m - dst_m) < 1 and src_s == dst_s:
                        action = "skip"
                    else:
                        action = "update"
                except OSError:
                    action = "update"
            plan.append({"src": str(item), "dst": str(dst), "action": action})

        to_copy = [p for p in plan if p["action"] in {"copy", "update"}]
        lines = [
            f"Sync Google Drive ({mode})",
            f"Source: {src}",
            f"Cible : {target}",
            f"A copier/mettre à jour: {len(to_copy)} fichier(s)",
            f"Ignorés: {len(plan) - len(to_copy)}",
        ]

        if dry_run:
            lines.append("\nMode aperçu: aucune copie effectuée.")
            return self._ok(
                "Aperçu sync Google Drive prêt. Passe dry_run=False pour exécuter.",
                {
                    "source": str(src),
                    "target": str(target),
                    "plan": plan,
                    "copy_count": len(to_copy),
                    "dry_run": True,
                    "display": "\n".join(lines),
                },
            )

        copied = 0
        errors = []
        for entry in to_copy:
            src_f = Path(entry["src"])
            dst_f = Path(entry["dst"])
            try:
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_f), str(dst_f))
                copied += 1
            except Exception as e:
                errors.append(f"{src_f.name}: {e}")

        # mode mirror: supprimer fichiers absents de la source dans la cible
        deleted = 0
        if mode == "mirror" and target.exists():
            src_files_rel = {str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()}
            for dst_item in target.rglob("*"):
                if not dst_item.is_file():
                    continue
                rel = str(dst_item.relative_to(target))
                if rel not in src_files_rel:
                    try:
                        dst_item.unlink()
                        deleted += 1
                    except Exception:
                        pass

        lines.append(f"\nCopiés: {copied}")
        if mode == "mirror":
            lines.append(f"Supprimés (mirror): {deleted}")
        if errors:
            lines.append(f"Erreurs: {len(errors)}")

        return self._ok(
            f"Sync Google Drive terminée: {copied} fichier(s) copiés.",
            {
                "source": str(src),
                "target": str(target),
                "copied": copied,
                "deleted": deleted,
                "errors": errors,
                "display": "\n".join(lines),
            },
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  OPÉRATIONS EXISTANTES (semaine 3 — inchangées)
    # ══════════════════════════════════════════════════════════════════════════

    def open_file(self, path: str, search_dirs: list = None, target_type: str = "any", current_dir: str = None) -> dict:
        """Ouvre un fichier avec l'application par défaut du système."""
        logger.info(f"Ouverture cible : '{path}'")
        requested_path = (path or "").strip().strip('"').strip("'")
        requested_path = self._sanitize_open_query(requested_path)
        requested_search_dirs = search_dirs
        search_dirs = self._normalize_search_dirs(search_dirs, current_dir=current_dir)
        if requested_search_dirs is not None and not search_dirs:
            return self._err("Aucun dossier cible valide.", {"requested_search_dirs": [str(d) for d in (requested_search_dirs or [])]})

        file_path = self._resolve_existing_path(requested_path, current_dir=current_dir)

        if file_path is None:
            search_query = Path(requested_path).name or requested_path
            matches = self._search_entries(search_query.lower(), search_dirs, target_type=target_type, max_results=12)
            if not matches:
                return self._err(f"Introuvable : '{path}'.", {"query": path, "searched_dirs": [str(d) for d in search_dirs]})
            if len(matches) == 1:
                file_path = Path(matches[0]["path"])
            else:
                display = self._format_choice_display(matches)
                return self._ok(f"J'ai trouvé {len(matches)} résultats pour '{path}'. Lequel ouvrir ?", {"ambiguous": True, "awaiting_choice": True, "choices": matches, "files": matches, "count": len(matches), "display": display})

        if not file_path.exists():
            return self._err(f"Le chemin n'existe pas : '{file_path}'")

        resolved_target = self._safe_resolve_path(file_path)
        try:
            import platform
            system = platform.system()
            if system == "Windows":
                os.startfile(str(file_path))
            elif system == "Darwin":
                subprocess.Popen(["open", str(file_path)])
            else:
                subprocess.Popen(["xdg-open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if file_path.is_dir():
                try:
                    entries = list(file_path.iterdir())
                    folders = [e for e in entries if e.is_dir()]
                    files_in = [e for e in entries if e.is_file()]
                    preview = [f.name for f in files_in[:8]]
                except PermissionError:
                    folders, files_in, preview = [], [], []
                return self._ok(
                    f"Dossier ouvert : '{file_path.name}'. {len(folders)} dossier(s) et {len(files_in)} fichier(s).",
                    {**self._file_info_dict(file_path), "opened_path": resolved_target, "resolved_path": resolved_target, "current_directory": resolved_target, "top_files": preview}
                )
            return self._ok(f"Fichier ouvert : '{file_path.name}'", {**self._file_info_dict(file_path), "opened_path": resolved_target, "resolved_path": resolved_target})
        except AttributeError:
            subprocess.Popen(["xdg-open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._ok(f"Fichier ouvert : '{file_path.name}'", {**self._file_info_dict(file_path), "opened_path": resolved_target, "resolved_path": resolved_target})
        except Exception as e:
            return self._err(f"Impossible d'ouvrir '{file_path.name}' : {e}")

    def list_folder(self, path: str = None, show_hidden: bool = False) -> dict:
        """Liste le contenu d'un dossier avec détails."""
        folder = self._resolve_existing_path(path, current_dir=None) if path else Path.home()
        if (folder is None or not folder.exists()) and path:
            drive_roots = self._get_all_drive_roots()
            non_system_drives = [d for d in drive_roots if d.drive.upper() != "C:"]
            system_drive = [d for d in drive_roots if d.drive.upper() == "C:"]
            search_order = non_system_drives + system_drive + list(self.search_dirs)
            matches = self._search_entries(Path(path.lstrip("/\\")).name.lower(), search_order, target_type="directory", max_results=5)
            if matches:
                folder = Path(matches[0]["resolved_path"] or matches[0]["path"])
            else:
                return self._err(f"Dossier '{path}' introuvable.", {"query": path})

        if not folder:
            folder = Path.home()
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
        items.sort(key=lambda x: (x.is_file(), x.name.lower()))

        files   = [i for i in items if i.is_file()]
        folders_list = [i for i in items if i.is_dir()]

        lines = [f"📂 {folder}", f"   {len(folders_list)} dossier(s), {len(files)} fichier(s)", "-" * 70]
        for d in folders_list:
            try:
                n = len(list(d.iterdir()))
                lines.append(f"  [DIR]  {d.name:<45}  ({n} élément(s))")
            except PermissionError:
                lines.append(f"  [DIR]  {d.name:<45}  (accès refusé)")
        for f in files:
            info = self._file_info_dict(f)
            lines.append(f"  [FIL]  {f.name:<45}  {info['size_str']:>10}")
        total_size = sum(f.stat().st_size for f in files if f.exists())
        lines.extend(["─" * 70, f"  Total fichiers : {self._format_size(total_size)}"])

        all_items = [{**self._file_info_dict(i), "is_dir": i.is_dir()} for i in items]
        resolved_folder = self._safe_resolve_path(folder)
        return self._ok(
            f"{len(folders_list)} dossier(s) et {len(files)} fichier(s) dans '{folder.name}'.",
            {"path": resolved_folder, "resolved_path": resolved_folder, "files": [i for i in all_items if not i["is_dir"]], "folders": [i for i in all_items if i["is_dir"]], "total": len(items), "display": "\n".join(lines)}
        )

    def close_file(self, path: str, current_dir: str = None, window_title: str = None) -> dict:
        logger.info(f"Fermeture cible : '{path}'")
        requested_path = self._sanitize_open_query((path or "").strip().strip('"').strip("'"))
        target_path = self._resolve_existing_path(requested_path, current_dir=current_dir)
        if target_path is None and requested_path:
            search_dirs = self._normalize_search_dirs(None, current_dir=current_dir)
            matches = self._search_entries(requested_path.lower(), search_dirs, target_type="any", max_results=5)
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
        result = WindowManager().close_window(query=requested_path, preferred_kind="folder" if target_path is not None and target_path.is_dir() else None, title_candidates=title_candidates, title=window_title)
        if not result.get("success"):
            data = dict(result.get("data") or {})
            data.setdefault("path", str(target_path) if target_path else requested_path)
            data.setdefault("title_candidates", [t for t in title_candidates if t])
            result["data"] = data
        else:
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

    def copy_file(self, src: str, dst: str, overwrite: bool = False) -> dict:
        src_path = Path(src)
        dst_path = Path(dst)
        if not src_path.exists():
            return self._err(f"Source introuvable : '{src}'")
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name
        if dst_path.exists() and not overwrite:
            return self._err(f"La destination '{dst_path.name}' existe déjà.", {"destination_exists": True})
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                shutil.copytree(str(src_path), str(dst_path), dirs_exist_ok=overwrite)
                item_type = "dossier"
            else:
                shutil.copy2(str(src_path), str(dst_path))
                item_type = "fichier"
            size_str = self._format_size(dst_path.stat().st_size) if dst_path.is_file() else ""
            return self._ok(f"'{src_path.name}' copié vers '{dst_path.parent}' ({size_str}).", {"src": str(src_path), "dst": str(dst_path), "type": item_type})
        except PermissionError:
            return self._err(f"Permission refusée pour copier vers '{dst}'.")
        except Exception as e:
            return self._err(f"Erreur de copie : {e}")

    def move_file(self, src: str, dst: str, overwrite: bool = False) -> dict:
        src_path = Path(src)
        dst_path = Path(dst)
        if not src_path.exists():
            return self._err(f"Source introuvable : '{src}'")
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name
        if dst_path.exists() and not overwrite:
            return self._err(f"La destination '{dst_path.name}' existe déjà.", {"destination_exists": True})
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if dst_path.exists() and overwrite:
                shutil.rmtree(str(dst_path)) if dst_path.is_dir() else dst_path.unlink()
            shutil.move(str(src_path), str(dst_path))
            return self._ok(f"'{src_path.name}' déplacé vers '{dst_path.parent}'.", {"src": str(src_path), "dst": str(dst_path)})
        except PermissionError:
            return self._err(f"Permission refusée pour déplacer '{src}'.")
        except Exception as e:
            return self._err(f"Erreur déplacement : {e}")

    def rename_file(self, path: str, new_name: str) -> dict:
        file_path = Path(path)
        if not file_path.exists():
            return self._err(f"Fichier introuvable : '{path}'")
        new_name = new_name.strip()
        if file_path.is_file() and "." not in new_name:
            new_name = new_name + file_path.suffix
        new_path = file_path.parent / new_name
        if new_path.exists():
            return self._err(f"Un fichier '{new_name}' existe déjà dans '{file_path.parent}'.")
        try:
            file_path.rename(new_path)
            return self._ok(f"'{file_path.name}' renommé en '{new_path.name}'.", {"old_name": str(file_path), "new_name": str(new_path)})
        except PermissionError:
            return self._err(f"Permission refusée pour renommer '{file_path.name}'.")
        except Exception as e:
            return self._err(f"Erreur renommage : {e}")

    def delete_file(self, path: str, confirm: bool = True) -> dict:
        file_path = Path(path)
        if not file_path.exists():
            return self._err(f"Fichier/dossier introuvable : '{path}'")
        dangerous_paths = [Path("C:/"), Path("C:/Windows"), Path("C:/Program Files"), Path.home(), Path("/"), Path("/home"), Path("/usr")]
        if any(file_path.resolve() == dp.resolve() for dp in dangerous_paths if dp.exists()):
            return self._err(f"SÉCURITÉ : Suppression de '{path}' refusée — dossier protégé.", {"blocked": True})
        try:
            if file_path.is_file() or file_path.is_symlink():
                size = file_path.stat().st_size
                file_path.unlink()
                return self._ok(f"'{file_path.name}' supprimé ({self._format_size(size)}).", {"deleted": str(file_path), "type": "file"})
            elif file_path.is_dir():
                n_items = sum(1 for _ in file_path.rglob("*"))
                shutil.rmtree(str(file_path))
                return self._ok(f"Dossier '{file_path.name}' supprimé ({n_items} élément(s)).", {"deleted": str(file_path), "type": "directory", "items_deleted": n_items})
            else:
                return self._err(f"'{path}' n'est ni un fichier ni un dossier.")
        except PermissionError:
            return self._err(f"Permission refusée pour supprimer '{file_path.name}'.")
        except Exception as e:
            return self._err(f"Erreur suppression : {e}")

    def create_folder(self, path: str) -> dict:
        folder = Path(path)
        if folder.exists():
            return self._ok(f"Le dossier '{folder.name}' existe déjà.", {"path": str(folder), "already_existed": True}) if folder.is_dir() else self._err(f"'{path}' existe déjà en tant que fichier.")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            return self._ok(f"Dossier '{folder.name}' créé.", {"path": str(folder), "parent": str(folder.parent)})
        except PermissionError:
            return self._err(f"Permission refusée pour créer '{path}'.")
        except Exception as e:
            return self._err(f"Erreur création dossier : {e}")

    def search_by_content(self, keyword: str, search_dirs: list = None, extensions: list = None, max_results: int = 20) -> dict:
        logger.info(f"Recherche contenu : '{keyword}'")
        dirs = self._normalize_search_dirs(search_dirs)
        if search_dirs is not None and not dirs:
            return self._err("Aucun dossier cible valide.", {"requested_search_dirs": [str(d) for d in (search_dirs or [])]})
        exts = set(extensions or [".txt", ".py", ".md", ".csv", ".json", ".log", ".xml", ".html", ".cfg", ".ini"])
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
                            matching_lines = [(i+1, line.strip()) for i, line in enumerate(text.splitlines()) if keyword_lower in line.lower()][:3]
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
        lines = [f"Fichiers contenant '{keyword}' ({len(results)}) :", "-" * 70]
        for r in results:
            lines.append(f"  {r['name']}  —  {r['path']}")
            for line_no, line_text in r.get("matches", []):
                lines.append(f"    L{line_no}: {line_text[:80]}")
        return self._ok(f"{len(results)} fichier(s) contiennent '{keyword}'.", {"files": results, "count": len(results), "keyword": keyword, "display": "\n".join(lines)})

    # ══════════════════════════════════════════════════════════════════════════
    #  UTILITAIRES PRIVÉS
    # ══════════════════════════════════════════════════════════════════════════

    def _resolve_extensions(self, extension: str) -> set[str]:
        """[S7] Résout une extension ou catégorie en un set d'extensions."""
        if not extension:
            return set()
        ext_lower = extension.lower().strip()
        # Catégorie ?
        for cat, exts in FILE_TYPE_CATEGORIES.items():
            if ext_lower == cat or ext_lower.rstrip("s") == cat.rstrip("s"):
                return set(exts)
        # Extension directe
        if not ext_lower.startswith("."):
            ext_lower = "." + ext_lower
        return {ext_lower}

    def _format_results_table(self, results: list, title: str = "", show_date: bool = False, show_size: bool = True) -> str:
        """[S7] Formate une liste de résultats en tableau lisible."""
        lines = []
        if title:
            lines.append(title)
        header = f"{'NOM':<38} {'TAILLE':>10}"
        if show_date:
            header += f"  {'MODIFIÉ':<17}"
        header += "  CHEMIN"
        lines.append(header)
        lines.append("─" * (len(header) + 20))
        for r in results:
            name = r.get("name", "")[:37]
            size = r.get("size_str", "")
            row  = f"{name:<38} {size:>10}"
            if show_date:
                row += f"  {r.get('modified', ''):<17}"
            path = r.get("path", "")
            # Tronquer le chemin pour l'affichage
            if len(path) > 50:
                path = "..." + path[-47:]
            row += f"  {path}"
            lines.append(row)
        if len(results) == 0:
            lines.append("  (aucun résultat)")
        return "\n".join(lines)

    def _normalize_search_dirs(self, search_dirs: list = None, current_dir: str = None) -> list[Path]:
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

    def _resolve_existing_path(self, raw_path: str = None, current_dir: str = None) -> Path | None:
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

    def _resolve_alias_path(self, raw_path: str, current_dir: str = None) -> Path | None:
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
        results.sort(key=lambda item: (-item.get("match_score", 0), len(item.get("path", "")), item.get("name", "").lower()))
        for item in results:
            item.pop("match_score", None)
        return results

    @staticmethod
    def _sanitize_open_query(query: str) -> str:
        text = (query or "").strip()
        if not text:
            return text
        text = re.sub(r"\s+(du|de|au|aux)\s+(?:le|la|les)?\s*(?:disque|disk|lecteur|drive)\s+[a-z]\s*$", "", text, flags=re.IGNORECASE).strip()
        return text

    def _classify_single_document(self, path: Path) -> tuple[str, float, list[str]]:
        """[S8] Retourne (catégorie, confiance, raisons) pour un document."""
        name = self._normalize_text_for_match(path.name)
        text = self._normalize_text_for_match(self._extract_text_snippet(path, max_chars=4000))

        scores: dict[str, float] = {k: 0.0 for k in DOC_CLASS_LABELS.keys()}
        reasons: dict[str, list[str]] = {k: [] for k in DOC_CLASS_LABELS.keys()}

        for label, keywords in DOC_CLASS_LABELS.items():
            for kw in keywords:
                kw_norm = self._normalize_text_for_match(kw)
                if kw_norm in name:
                    scores[label] += 0.7
                    reasons[label].append(f"nom:{kw}")
                if kw_norm and kw_norm in text:
                    scores[label] += 0.35
                    reasons[label].append(f"contenu:{kw}")

        if path.suffix.lower() in {".pdf", ".doc", ".docx"} and max(scores.values() or [0.0]) < 0.4:
            scores["administratif"] += 0.2
            reasons["administratif"].append("fallback:document")

        best = max(scores.items(), key=lambda x: x[1])
        label, score = best[0], float(best[1])
        if score < 0.3:
            return "autre", 0.2, ["signal faible"]
        return label, min(score, 0.99), reasons[label][:4]

    @staticmethod
    def _normalize_text_for_match(text: str) -> str:
        txt = (text or "").lower()
        txt = txt.replace("'", " ").replace("-", " ")
        txt = re.sub(r"\s+", " ", txt)
        return txt.strip()

    def _extract_text_snippet(self, path: Path, max_chars: int = 4000) -> str:
        """[S8] Extraction locale légère pour classification; silencieuse en cas d'échec."""
        suffix = path.suffix.lower()
        try:
            if suffix in {".txt", ".md", ".csv", ".json", ".log", ".ini", ".cfg"}:
                return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
            if suffix in {".py", ".js", ".ts", ".html", ".xml"}:
                return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
            # Pour PDF/docx sans dépendances externes, on évite les erreurs: fallback nom uniquement.
            return ""
        except Exception:
            return ""

    def _close_windows_by_title(self, title_candidates: list[str]) -> list[dict]:
        patterns = [t for t in title_candidates if t]
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
  if (-not $proc.HasExited) {{ Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }}
  $results += [PSCustomObject]@{{ ProcessName = $proc.ProcessName; Id = $proc.Id; MainWindowTitle = $title }}
}}
$results | ConvertTo-Json -Compress
"""
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=15, check=False)
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
        return [payload] if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])

    def _score_match(self, item: Path, query_lower: str, base_dir: Path) -> int:
        score = 0
        name_lower = item.name.lower()
        parent_lower = str(item.parent).lower()
        if name_lower == query_lower:           score += 100
        elif name_lower.startswith(query_lower): score += 60
        elif query_lower in name_lower:          score += 30
        if str(item).lower().startswith(str(base_dir).lower()): score += 5
        if item.is_dir():                        score += 3
        if query_lower in parent_lower:          score += 8
        return score

    def _format_choice_display(self, choices: list[dict]) -> str:
        lines = ["Choix possibles :", "-" * 70]
        for i, choice in enumerate(choices[:8], 1):
            kind = "DIR" if choice.get("is_dir") else "FIL"
            lines.append(f"  {i}. [{kind}] {choice['name']}  —  {choice['path']}")
        return "\n".join(lines)

    def _walk_limited(self, base: Path, max_depth: int):
        for item in base.iterdir():
            if item.is_file():
                yield item
            elif item.is_dir() and item.name not in EXCLUDED_DIRS:
                yield item
                if max_depth > 1:
                    try:
                        yield from self._walk_limited(item, max_depth - 1)
                    except PermissionError:
                        continue

    def _file_info_dict(self, path: Path) -> dict:
        try:
            stat     = path.stat()
            size     = stat.st_size if path.is_file() else 0
            modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            created  = datetime.datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size, modified, created = 0, "N/A", "N/A"
        resolved = self._safe_resolve_path(path)
        return {
            "name":          path.name,
            "path":          str(path),
            "resolved_path": resolved,
            "parent":        str(path.parent),
            "extension":     path.suffix.lower(),
            "size":          size,
            "size_str":      self._format_size(size),
            "modified":      modified,
            "created":       created,
            "is_dir":        path.is_dir(),
        }

    @staticmethod
    def _md5(path: Path, chunk_size: int = 65536) -> str:
        """Calcule le hash MD5 d'un fichier par chunks."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_resolve_path(path: Path) -> str:
        try:
            return str(path.resolve(strict=False))
        except OSError:
            return str(path)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
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