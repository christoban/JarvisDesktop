"""
modules/pdf_manager.py — Contrôle PDF avancé
=============================================
Semaine 10 — Extraction précise, fusion, découpe, compression PDF.

Dépendances :
  pip install pypdf             # Manipulation PDF (déjà utilisé dans doc_reader)
  pip install pikepdf           # Opérations avancées : compression, repair (optionnel)
  pip install reportlab         # Création PDF depuis zéro (optionnel)

Fonctionnalités :
  - Extraire texte d'une page ou plage de pages spécifique
  - Extraire les métadonnées (auteur, titre, pages, taille)
  - Fusionner plusieurs PDF en un seul
  - Découper un PDF (extraire pages X à Y)
  - Compresser un PDF (réduire taille)
  - Recherche de texte avec numéro de page
  - Rotation de pages
  - Créer un PDF simple depuis texte
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from config.logger import get_logger

logger = get_logger(__name__)

# ── Imports optionnels ────────────────────────────────────────────────────────
try:
    import pypdf
    from pypdf import PdfReader, PdfWriter
    PYPDF_AVAILABLE = True
except ImportError:
    try:
        import PyPDF2 as pypdf
        from PyPDF2 import PdfReader, PdfWriter
        PYPDF_AVAILABLE = True
    except ImportError:
        PYPDF_AVAILABLE = False
        logger.warning("pypdf non installé. pip install pypdf")

try:
    import pikepdf
    PIKEPDF_AVAILABLE = True
except ImportError:
    PIKEPDF_AVAILABLE = False
    logger.debug("pikepdf non installé (compression désactivée). pip install pikepdf")

try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.debug("reportlab non installé (création PDF désactivée). pip install reportlab")

DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "Jarvis"


class PDFManager:
    """
    Gestionnaire PDF complet.
    Toutes les méthodes retournent { success, message, data }.
    """

    def __init__(self, output_dir: str = None):
        self._output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  EXTRACTION
    # ══════════════════════════════════════════════════════════════════════════

    def extract_pages(
        self,
        path: str,
        pages: str | list = None,
        output_path: str = None,
        open_after: bool = False,
    ) -> dict:
        """
        Extrait une ou plusieurs pages d'un PDF et crée un nouveau PDF.

        Args:
            path        : Chemin du PDF source
            pages       : "1", "1-5", "1,3,5", ou liste [1, 3, 5] (1-indexé)
            output_path : Chemin du PDF résultant (défaut : auto-généré)
            open_after  : Ouvrir après création

        Returns:
            data : path, pages_extracted, total_pages
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé. pip install pypdf")

        path_obj = Path(path)
        if not path_obj.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        try:
            reader     = PdfReader(str(path_obj))
            total_pages = len(reader.pages)

            # Parser les numéros de pages
            page_indices = self._parse_page_spec(pages, total_pages)
            if not page_indices:
                return self._err(f"Spécification de pages invalide : '{pages}'")

            # Vérifier que les indices sont valides
            invalid = [i + 1 for i in page_indices if i >= total_pages]
            if invalid:
                return self._err(
                    f"Pages inexistantes : {invalid}. Le PDF a {total_pages} pages."
                )

            # Créer le PDF extrait
            writer = PdfWriter()
            for i in page_indices:
                writer.add_page(reader.pages[i])

            if not output_path:
                stem = path_obj.stem
                pages_str = str(pages).replace(",", "-").replace(" ", "") if pages else "all"
                output_path = self._output_dir / f"{stem}_pages_{pages_str}.pdf"
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with open(str(out_path), "wb") as f:
                writer.write(f)

            logger.info(f"PDF extrait : {out_path.name} ({len(page_indices)} pages)")

            if open_after:
                self._open_file(str(out_path))

            return self._ok(
                f"{len(page_indices)} page(s) extraite(s) → '{out_path.name}'.",
                {
                    "path":             str(out_path),
                    "name":             out_path.name,
                    "pages_extracted":  len(page_indices),
                    "page_indices":     [i + 1 for i in page_indices],
                    "source_pages":     total_pages,
                }
            )
        except Exception as e:
            logger.error(f"Erreur extraction pages PDF : {e}")
            return self._err(f"Impossible d'extraire les pages : {e}")

    def extract_text_by_page(
        self,
        path: str,
        pages: str | list = None,
    ) -> dict:
        """
        Extrait le texte de pages spécifiques (sans créer de nouveau PDF).

        Args:
            path  : Chemin du PDF
            pages : Même format que extract_pages (défaut : toutes)

        Returns:
            data : text_by_page (dict page→texte), full_text, count
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé.")

        path_obj = Path(path)
        if not path_obj.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        try:
            reader      = PdfReader(str(path_obj))
            total_pages = len(reader.pages)
            page_indices = self._parse_page_spec(pages, total_pages)

            text_by_page = {}
            for i in page_indices:
                if i < total_pages:
                    text = reader.pages[i].extract_text() or ""
                    text_by_page[i + 1] = text.strip()

            full_text = "\n\n".join(
                f"[Page {p}]\n{t}" for p, t in sorted(text_by_page.items()) if t
            )

            return self._ok(
                f"Texte extrait de {len(text_by_page)} page(s) sur {total_pages}.",
                {
                    "text_by_page": text_by_page,
                    "full_text":    full_text,
                    "pages_count":  len(text_by_page),
                    "total_pages":  total_pages,
                    "display":      full_text[:3000],
                }
            )
        except Exception as e:
            return self._err(f"Impossible d'extraire le texte : {e}")

    def get_info(self, path: str) -> dict:
        """
        Retourne les métadonnées complètes d'un PDF.

        Returns:
            data : pages, title, author, creator, size, encrypted
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé.")

        path_obj = Path(path)
        if not path_obj.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        try:
            reader = PdfReader(str(path_obj))
            meta   = reader.metadata or {}
            stat   = path_obj.stat()

            info = {
                "name":      path_obj.name,
                "path":      str(path_obj),
                "pages":     len(reader.pages),
                "title":     meta.get("/Title", "") or "",
                "author":    meta.get("/Author", "") or "",
                "creator":   meta.get("/Creator", "") or "",
                "producer":  meta.get("/Producer", "") or "",
                "encrypted": reader.is_encrypted,
                "size":      stat.st_size,
                "size_str":  self._format_size(stat.st_size),
                "modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }

            lines = [
                f"  Fichier  : {info['name']}",
                f"  Pages    : {info['pages']}",
                f"  Taille   : {info['size_str']}",
                f"  Modifié  : {info['modified']}",
            ]
            if info["title"]:   lines.append(f"  Titre    : {info['title']}")
            if info["author"]:  lines.append(f"  Auteur   : {info['author']}")
            if info["encrypted"]: lines.append("  🔒 Chiffré")
            info["display"] = "\n".join(lines)

            return self._ok(f"Infos PDF : '{path_obj.name}'", info)
        except Exception as e:
            return self._err(f"Impossible de lire les métadonnées : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  FUSION / DÉCOUPE
    # ══════════════════════════════════════════════════════════════════════════

    def merge(
        self,
        paths: list[str],
        output_path: str = None,
        open_after: bool = False,
    ) -> dict:
        """
        Fusionne plusieurs PDF en un seul.

        Args:
            paths       : Liste de chemins PDF à fusionner (dans l'ordre)
            output_path : Chemin du PDF résultant
            open_after  : Ouvrir après création

        Returns:
            data : path, pages_total, files_merged
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé.")
        if not paths or len(paths) < 2:
            return self._err("Fournis au moins 2 fichiers PDF à fusionner.")

        # Vérifier que tous les fichiers existent
        missing = [p for p in paths if not Path(p).exists()]
        if missing:
            return self._err(f"Fichiers introuvables : {missing}")

        if not output_path:
            output_path = self._output_dir / f"fusion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            writer      = PdfWriter()
            total_pages = 0

            for pdf_path in paths:
                reader = PdfReader(str(pdf_path))
                for page in reader.pages:
                    writer.add_page(page)
                total_pages += len(reader.pages)
                logger.info(f"  + {Path(pdf_path).name} ({len(reader.pages)} pages)")

            with open(str(out_path), "wb") as f:
                writer.write(f)

            logger.info(f"PDF fusionné : {out_path.name} ({total_pages} pages)")

            if open_after:
                self._open_file(str(out_path))

            return self._ok(
                f"{len(paths)} PDF fusionnés → '{out_path.name}' ({total_pages} pages).",
                {
                    "path":         str(out_path),
                    "name":         out_path.name,
                    "pages_total":  total_pages,
                    "files_merged": len(paths),
                }
            )
        except Exception as e:
            logger.error(f"Erreur fusion PDF : {e}")
            return self._err(f"Impossible de fusionner les PDF : {e}")

    def split(
        self,
        path: str,
        split_at: int | list[int] = None,
        output_dir: str = None,
        open_after: bool = False,
    ) -> dict:
        """
        Découpe un PDF en plusieurs fichiers.

        Args:
            path     : Chemin du PDF source
            split_at : int → page à partir de laquelle découper (en 2 parties)
                       list → [5, 10] → découpe en 3 parties (1-4, 5-9, 10-fin)
                       None → découpe en pages individuelles
            output_dir : Dossier de sortie (défaut : même que source)
            open_after : Ouvrir le dossier après création

        Returns:
            data : files (list), pages_total
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé.")

        path_obj = Path(path)
        if not path_obj.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        out_dir = Path(output_dir) if output_dir else (self._output_dir / path_obj.stem)
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            reader      = PdfReader(str(path_obj))
            total_pages = len(reader.pages)

            # Construire les plages
            if split_at is None:
                # Une page par fichier
                ranges = [(i, i + 1) for i in range(total_pages)]
            elif isinstance(split_at, int):
                idx = split_at - 1
                ranges = [(0, idx), (idx, total_pages)]
            else:
                cuts = sorted([c - 1 for c in split_at])
                ranges = []
                prev = 0
                for cut in cuts:
                    if 0 < cut < total_pages:
                        ranges.append((prev, cut))
                        prev = cut
                ranges.append((prev, total_pages))

            created_files = []
            for i, (start, end) in enumerate(ranges):
                if start >= end:
                    continue
                writer = PdfWriter()
                for page_idx in range(start, end):
                    writer.add_page(reader.pages[page_idx])

                part_name = (
                    f"{path_obj.stem}_page_{start + 1}.pdf"
                    if split_at is None else
                    f"{path_obj.stem}_part{i + 1}_p{start + 1}-{end}.pdf"
                )
                part_path = out_dir / part_name
                with open(str(part_path), "wb") as f:
                    writer.write(f)
                created_files.append(str(part_path))

            logger.info(f"PDF découpé : {len(created_files)} fichiers créés")

            if open_after:
                self._open_file(str(out_dir))

            return self._ok(
                f"PDF découpé en {len(created_files)} fichier(s) dans '{out_dir.name}'.",
                {
                    "files":       created_files,
                    "count":       len(created_files),
                    "pages_total": total_pages,
                    "output_dir":  str(out_dir),
                }
            )
        except Exception as e:
            logger.error(f"Erreur découpe PDF : {e}")
            return self._err(f"Impossible de découper le PDF : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  RECHERCHE
    # ══════════════════════════════════════════════════════════════════════════

    def search(self, path: str, keyword: str, case_sensitive: bool = False) -> dict:
        """
        Recherche un mot ou une phrase dans toutes les pages du PDF.

        Returns:
            data : matches [{page, line, context}], count, total_pages
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé.")

        path_obj = Path(path)
        if not path_obj.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        keyword_search = keyword if case_sensitive else keyword.lower()

        try:
            reader  = PdfReader(str(path_obj))
            matches = []

            for page_num, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                text_check = text if case_sensitive else text.lower()

                if keyword_search in text_check:
                    # Trouver le contexte autour de chaque occurrence
                    lines = text.splitlines()
                    for line_num, line in enumerate(lines):
                        line_check = line if case_sensitive else line.lower()
                        if keyword_search in line_check:
                            ctx_start = max(0, line_num - 1)
                            ctx_end   = min(len(lines), line_num + 2)
                            matches.append({
                                "page":    page_num,
                                "line":    line.strip(),
                                "context": "\n".join(lines[ctx_start:ctx_end]).strip(),
                            })

            if not matches:
                return self._ok(
                    f"'{keyword}' non trouvé dans '{path_obj.name}'.",
                    {"found": False, "count": 0, "matches": [], "total_pages": len(reader.pages)}
                )

            # Affichage
            lines_display = [
                f"'{keyword}' trouvé {len(matches)} fois dans '{path_obj.name}' :",
                "─" * 60,
            ]
            for m in matches[:10]:
                lines_display.append(f"  Page {m['page']:>3} : {m['line'][:80]}")
            if len(matches) > 10:
                lines_display.append(f"  ... et {len(matches) - 10} occurrence(s) de plus")

            return self._ok(
                f"'{keyword}' trouvé {len(matches)} fois dans '{path_obj.name}'.",
                {
                    "found":       True,
                    "count":       len(matches),
                    "matches":     matches[:20],
                    "total_pages": len(reader.pages),
                    "display":     "\n".join(lines_display),
                }
            )
        except Exception as e:
            return self._err(f"Erreur recherche PDF : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  ROTATION
    # ══════════════════════════════════════════════════════════════════════════

    def rotate_pages(
        self,
        path: str,
        angle: int,
        pages: str | list = None,
        output_path: str = None,
    ) -> dict:
        """
        Fait pivoter des pages d'un PDF.

        Args:
            path        : Chemin du PDF
            angle       : 90, 180, ou 270 degrés
            pages       : Pages à pivoter (défaut : toutes)
            output_path : PDF résultant
        """
        if not PYPDF_AVAILABLE:
            return self._err("pypdf non installé.")
        if angle not in (90, 180, 270):
            return self._err("L'angle doit être 90, 180 ou 270 degrés.")

        path_obj = Path(path)
        if not path_obj.exists():
            return self._err(f"Fichier introuvable : '{path}'")

        if not output_path:
            output_path = self._output_dir / f"{path_obj.stem}_rotated{angle}.pdf"
        out_path = Path(output_path)

        try:
            reader      = PdfReader(str(path_obj))
            writer      = PdfWriter()
            total_pages = len(reader.pages)
            page_indices = self._parse_page_spec(pages, total_pages)

            for i, page in enumerate(reader.pages):
                if i in page_indices:
                    page.rotate(angle)
                writer.add_page(page)

            with open(str(out_path), "wb") as f:
                writer.write(f)

            return self._ok(
                f"{len(page_indices)} page(s) pivotée(s) de {angle}° → '{out_path.name}'.",
                {"path": str(out_path), "name": out_path.name, "rotated": len(page_indices)}
            )
        except Exception as e:
            return self._err(f"Erreur rotation PDF : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_page_spec(pages, total_pages: int) -> list[int]:
        """
        Convertit une spécification de pages en liste d'indices 0-basés.

        Exemples :
          None      → [0, 1, 2, ..., total-1]
          "3"       → [2]
          "1-5"     → [0, 1, 2, 3, 4]
          "1,3,5"   → [0, 2, 4]
          [1, 3]    → [0, 2]
        """
        if pages is None:
            return list(range(total_pages))

        if isinstance(pages, list):
            return [int(p) - 1 for p in pages if str(p).isdigit()]

        pages = str(pages).strip()

        # Plage "X-Y"
        if "-" in pages:
            parts = pages.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start = int(parts[0]) - 1
                end   = int(parts[1])
                return list(range(start, end))

        # Liste "X,Y,Z"
        if "," in pages:
            return [int(p.strip()) - 1 for p in pages.split(",") if p.strip().isdigit()]

        # Page unique
        if pages.isdigit():
            return [int(pages) - 1]

        return []

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / 1024 ** 2:.1f} MB"
        return f"{size_bytes / 1024 ** 3:.2f} GB"

    @staticmethod
    def _open_file(path: str):
        try:
            os.startfile(path)
        except Exception:
            import subprocess, sys
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}