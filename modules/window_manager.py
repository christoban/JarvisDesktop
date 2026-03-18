"""
window_manager.py — Détection et fermeture de fenêtres Windows.
Permet à Jarvis de raisonner sur les vraies fenêtres ouvertes, même hors contexte.
"""

import time
import unicodedata
from pathlib import Path

import psutil

from config.logger import get_logger

logger = get_logger(__name__)

try:
    import pygetwindow as gw
    import win32api
    import win32con
    import win32com.client
    import win32gui
    import win32process
    WINDOWS_API_AVAILABLE = True
except Exception as exc:
    WINDOWS_API_AVAILABLE = False
    WINDOWS_IMPORT_ERROR = exc


BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe",
}

MEDIA_PROCESSES = {
    "vlc.exe", "wmplayer.exe", "music.ui.exe", "potplayer64.exe", "mpc-hc64.exe",
    "mpc-hc.exe", "moviesandtv.exe",
}

DOCUMENT_PROCESSES = {
    "winword.exe", "excel.exe", "powerpnt.exe", "acrord32.exe", "acrobat.exe",
    "sumatrapdf.exe", "notepad.exe", "notepad++.exe",
}

DOCUMENT_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".doc", ".docx", ".rtf", ".odt",
    ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
}

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
}

KIND_ALIASES = {
    "browser": {"navigateur", "browser", "chrome", "firefox", "edge", "opera", "brave", "onglet", "site"},
    "document": {"document", "pdf", "word", "excel", "powerpoint", "texte", "fichier texte", "presentation", "présentation", "tableur"},
    "media": {"video", "vidéo", "film", "musique", "audio", "media", "média", "vlc", "lecteur"},
    "folder": {"dossier", "explorateur", "explorer", "répertoire", "repertoire"},
}

GENERIC_WINDOW_TERMS = {
    "", "la", "le", "les", "ca", "ça", "cela", "celle", "celui", "celle-la", "celle là",
    "fenetre", "fenêtre", "window", "app", "application", "programme",
}


class WindowManager:
    def list_open_windows(self) -> list[dict]:
        if not WINDOWS_API_AVAILABLE:
            logger.warning(f"WindowManager indisponible: {WINDOWS_IMPORT_ERROR}")
            return []

        windows = []
        seen = set()
        for win in gw.getAllWindows():
            hwnd = getattr(win, "_hWnd", None)
            if not hwnd or hwnd in seen:
                continue
            seen.add(hwnd)

            try:
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    continue
                title = (win32gui.GetWindowText(hwnd) or "").strip()
                if not title:
                    continue
            except Exception:
                continue

            pid = None
            process_name = ""
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                process_name = psutil.Process(pid).name()
            except Exception:
                pass

            entry = {
                "hwnd": hwnd,
                "pid": pid,
                "title": title,
                "process_name": process_name,
                "kind": self._classify_window(title, process_name),
            }
            windows.append(entry)

        return windows

    def close_window(
        self,
        query: str = "",
        preferred_kind: str | None = None,
        close_scope: str | None = None,
        hwnd: int | None = None,
        title: str | None = None,
        pid: int | None = None,
        title_candidates: list[str] | None = None,
    ) -> dict:
        if hwnd:
            match = self._find_window_by_hwnd(hwnd)
            if match is None:
                match = {
                    "hwnd": hwnd,
                    "pid": pid,
                    "title": title or query or "fenêtre",
                    "process_name": "",
                    "kind": preferred_kind or self._infer_kind_from_query(query or title or "") or "app",
                }
            return self._close_match(match, close_scope=close_scope)

        matches = self.find_windows(query=query, preferred_kind=preferred_kind, title_candidates=title_candidates)
        if not matches:
            target = query or title or preferred_kind or "la fenêtre demandée"
            return self._err(f"Aucune fenêtre ouverte ne correspond à '{target}'.")

        if len(matches) > 1:
            label = query or preferred_kind or "cette demande"
            return self._ok(
                f"J'ai trouvé {len(matches)} fenêtres pour '{label}'. Laquelle veux-tu fermer ?",
                {
                    "awaiting_choice": True,
                    "ambiguous": True,
                    "choices": matches[:8],
                    "count": len(matches),
                    "display": self._format_choices(matches[:8]),
                },
            )

        return self._close_match(matches[0], close_scope=close_scope)

    def _close_match(self, entry: dict, close_scope: str | None = None) -> dict:
        scope = (close_scope or "").strip().lower()
        if scope == "tab" and entry.get("kind") == "browser":
            return self._close_browser_tab(entry)
        return self._close_single_window(entry)

    def _close_browser_tab(self, entry: dict) -> dict:
        hwnd = entry.get("hwnd")
        title = entry.get("title") or "onglet navigateur"
        if not hwnd:
            return self._err("Impossible de cibler l'onglet navigateur.")

        try:
            if not win32gui.IsWindow(hwnd):
                return self._ok(
                    f"L'onglet '{title}' n'est plus ouvert.",
                    {"closed": [entry], "closed_title": title, "closed_scope": "tab"},
                )

            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
            self._focus_window(hwnd)
            time.sleep(0.12)

            # Ctrl+W ferme l'onglet actif, pas toute la fenêtre.
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord('W'), 0, 0, 0)
            win32api.keybd_event(ord('W'), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.2)

            return self._ok(
                f"Onglet fermé : '{title}'.",
                {
                    "closed": [entry],
                    "closed_title": title,
                    "closed_hwnd": hwnd,
                    "closed_scope": "tab",
                },
            )
        except Exception as exc:
            logger.warning(f"Fermeture onglet échouée pour '{title}': {exc}")
            return self._err(f"Impossible de fermer l'onglet '{title}' : {exc}")

    def _focus_window(self, hwnd: int):
        try:
            win32gui.SetForegroundWindow(hwnd)
            return
        except Exception:
            pass

        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            shell.SendKeys('%')
            win32gui.SetForegroundWindow(hwnd)
            return
        except Exception:
            pass

        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetActiveWindow(hwnd)
        except Exception as exc:
            raise RuntimeError(f"Impossible d'activer la fenêtre cible: {exc}") from exc

    def find_windows(
        self,
        query: str = "",
        preferred_kind: str | None = None,
        title_candidates: list[str] | None = None,
    ) -> list[dict]:
        windows = self.list_open_windows()
        if not windows:
            return []

        normalized_query = self._normalize_text(query)
        inferred_kind = preferred_kind or self._infer_kind_from_query(normalized_query)
        candidate_terms = [self._normalize_text(t) for t in (title_candidates or []) if str(t or "").strip()]
        generic_query = normalized_query in GENERIC_WINDOW_TERMS or not normalized_query

        scored = []
        for entry in windows:
            score = self._score_window(entry, normalized_query, inferred_kind, candidate_terms, generic_query)
            if score <= 0:
                continue
            item = dict(entry)
            item["score"] = score
            scored.append(item)

        scored.sort(key=lambda item: (-item["score"], item.get("title", "").lower()))
        for item in scored:
            item.pop("score", None)
        return scored

    def _find_window_by_hwnd(self, hwnd: int) -> dict | None:
        for entry in self.list_open_windows():
            if entry.get("hwnd") == hwnd:
                return entry
        return None

    def _close_single_window(self, entry: dict) -> dict:
        hwnd = entry.get("hwnd")
        if not hwnd:
            return self._err("Fenêtre cible invalide.")

        title = entry.get("title") or "fenêtre"
        try:
            if not win32gui.IsWindow(hwnd):
                return self._ok(
                    f"La fenêtre '{title}' n'est plus ouverte.",
                    {"closed": [entry], "closed_title": title},
                )

            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            for _ in range(10):
                time.sleep(0.15)
                if not win32gui.IsWindow(hwnd):
                    return self._ok(
                        f"Fenêtre fermée : '{title}'.",
                        {"closed": [entry], "closed_title": title, "closed_hwnd": hwnd},
                    )
        except Exception as exc:
            logger.warning(f"Fermeture fenêtre échouée pour '{title}': {exc}")
            return self._err(f"Impossible de fermer '{title}' : {exc}")

        return self._err(
            f"La fenêtre '{title}' n'a pas pu être fermée proprement.",
            {"title": title, "hwnd": hwnd, "pid": entry.get("pid")},
        )

    def _score_window(
        self,
        entry: dict,
        normalized_query: str,
        inferred_kind: str | None,
        candidate_terms: list[str],
        generic_query: bool,
    ) -> int:
        title = self._normalize_text(entry.get("title", ""))
        process_name = self._normalize_text(entry.get("process_name", ""))
        kind = entry.get("kind", "app")
        score = 0

        if inferred_kind:
            if kind == inferred_kind:
                score += 35
            elif generic_query:
                return 0

        for term in candidate_terms:
            if not term:
                continue
            if title == term:
                score += 140
            elif term in title:
                score += 90
            if term and term in process_name:
                score += 55

        if normalized_query and normalized_query not in GENERIC_WINDOW_TERMS:
            if title == normalized_query:
                score += 160
            elif normalized_query in title:
                score += 100
            if normalized_query and normalized_query in process_name:
                score += 60

            for token in [tok for tok in normalized_query.split() if len(tok) >= 3]:
                if token in title:
                    score += 20
                if token in process_name:
                    score += 10

        if generic_query and inferred_kind and kind == inferred_kind:
            score += 25

        return score

    def _classify_window(self, title: str, process_name: str) -> str:
        title_lower = self._normalize_text(title)
        process_lower = self._normalize_text(process_name)

        if process_lower in {self._normalize_text(name) for name in BROWSER_PROCESSES}:
            return "browser"
        if process_lower in {self._normalize_text(name) for name in MEDIA_PROCESSES}:
            return "media"
        if process_lower == "explorer.exe":
            return "folder"
        if process_lower in {self._normalize_text(name) for name in DOCUMENT_PROCESSES}:
            return "document"

        suffix = Path(title).suffix.lower()
        if suffix in DOCUMENT_EXTENSIONS:
            return "document"
        if suffix in MEDIA_EXTENSIONS:
            return "media"
        if any(alias in title_lower for alias in KIND_ALIASES["browser"]):
            return "browser"
        if any(alias in title_lower for alias in KIND_ALIASES["media"]):
            return "media"
        if any(alias in title_lower for alias in KIND_ALIASES["document"]):
            return "document"
        return "app"

    def _infer_kind_from_query(self, query: str) -> str | None:
        normalized = self._normalize_text(query)
        for kind, aliases in KIND_ALIASES.items():
            if any(alias in normalized for alias in aliases):
                return kind
        return None

    @staticmethod
    def _format_choices(choices: list[dict]) -> str:
        lines = ["Fenêtres possibles :", "-" * 70]
        for index, choice in enumerate(choices, start=1):
            kind = str(choice.get("kind") or "app").upper()
            title = choice.get("title") or "(sans titre)"
            process_name = choice.get("process_name") or "?"
            lines.append(f"  {index}. [{kind}] {title}  —  {process_name}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(text or ""))
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return normalized.lower().strip()

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}