"""
browser/cdp_session.py — Session CDP et gestion des onglets
============================================================

Responsabilités :
  - Connexion / lancement de Chrome en mode debug (--remote-debugging-port=9222)
  - Résolution d'onglet (par index, par titre/URL, ambiguïté)
  - Opérations sur les onglets : list, new, switch, close, navigate, back, forward, reload
  - Primitive _cdp_call / _cdp_eval pour envoyer des commandes DevTools

Chrome DOIT être lancé avec :
  --remote-debugging-port=9222
  (BrowserAutomation.ensure_session() le fait automatiquement)
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from config.logger import get_logger

logger = get_logger(__name__)

try:
    from websocket import create_connection
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning("cdp_session: package 'websocket-client' manquant. pip install websocket-client")

try:
    import pygetwindow as gw
    import win32process
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CDPTab:
    id: str
    title: str
    url: str
    websocket_url: str


class CDPSession:
    """
    Bas niveau : connexion CDP, résolution d'onglet, primitives d'éval.
    Utilisé par BrowserActions et BrowserPage.
    """

    def __init__(self, debug_port: int = 9222):
        self.debug_port = debug_port
        self._base = f"http://127.0.0.1:{debug_port}"

    # ── Santé de la session ───────────────────────────────────────────────────

    def is_ready(self) -> bool:
        try:
            r = requests.get(f"{self._base}/json/version", timeout=1)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_session(self, launch_if_missing: bool = True) -> dict:
        """S'assure que Chrome est lancé en mode debug. Le lance si nécessaire."""
        if self.is_ready():
            return self._ok("Session CDP prête.", {"port": self.debug_port})

        if not launch_if_missing:
            return self._err(
                "Chrome n'est pas en mode pilotable.",
                {
                    "tip": "Lance Chrome avec --remote-debugging-port=9222, "
                           "ou dis 'ouvre chrome' pour que Jarvis le fasse.",
                    "action_required": True,
                },
            )

        result = self._launch_debug_chrome()
        if not result["success"]:
            return result

        for _ in range(24):   # ~6 secondes max
            if self.is_ready():
                return self._ok("Chrome lancé en mode pilotable.", {"port": self.debug_port, "launched": True})
            time.sleep(0.25)

        return self._err(
            "Chrome lancé mais l'interface DevTools ne répond pas.",
            {"tip": "Réessaie dans quelques secondes."},
        )

    # ── Onglets ───────────────────────────────────────────────────────────────

    def get_tabs(self, include_internal: bool = False) -> list[CDPTab]:
        """Retourne la liste des onglets CDP actifs."""
        try:
            resp = requests.get(f"{self._base}/json", timeout=2)
            if resp.status_code != 200:
                return []
            payload = resp.json()
        except Exception:
            return []

        all_tabs = []
        tabs = []
        for item in payload:
            if item.get("type") != "page":
                continue
            url = item.get("url") or ""
            tab = CDPTab(
                id=item.get("id", ""),
                title=item.get("title") or "",
                url=url,
                websocket_url=item.get("webSocketDebuggerUrl") or "",
            )
            all_tabs.append(tab)
            if not include_internal and url.startswith("chrome://"):
                continue
            tabs.append(tab)

        # Si seuls des onglets internes existent (ex: chrome://newtab),
        # on les retourne quand même pour pouvoir piloter la session.
        if not tabs and not include_internal:
            return all_tabs
        return tabs

    def list_tabs(self) -> dict:
        """Retourne un résultat structuré listant tous les onglets ouverts."""
        ready = self.ensure_session(launch_if_missing=False)
        if not ready["success"]:
            # Fallback: lire les titres de fenêtres Windows
            fallback = self._tabs_from_windows()
            if fallback:
                display = self._format_windows_tabs(fallback)
                return self._ok(
                    f"{len(fallback)} onglet(s) détecté(s) (mode fenêtres).",
                    {"tabs": fallback, "count": len(fallback), "display": display, "source": "windows"},
                )
            return ready

        tabs = self.get_tabs()
        if not tabs:
            return self._ok("Aucun onglet détecté.", {"tabs": [], "count": 0})

        lines = ["Onglets ouverts :", "─" * 70]
        for i, tab in enumerate(tabs, 1):
            short = tab.url[:60] + "..." if len(tab.url) > 60 else tab.url
            lines.append(f"  {i}. {tab.title or '(sans titre)'}  —  {short}")

        return self._ok(
            f"{len(tabs)} onglet(s) ouvert(s).",
            {
                "tabs": [self._tab_dict(t, i + 1) for i, t in enumerate(tabs)],
                "count": len(tabs),
                "display": "\n".join(lines),
            },
        )

    def new_tab(self, url: str = "about:blank") -> dict:
        """Ouvre un nouvel onglet CDP."""
        ready = self.ensure_session(launch_if_missing=True)
        if not ready["success"]:
            return ready

        url = normalize_url(url) if url and url != "about:blank" else "about:blank"
        endpoint = f"{self._base}/json/new?{urllib.parse.quote(url, safe=':/?&=%')}"
        try:
            # Chrome récent préfère souvent PUT sur /json/new.
            resp = requests.put(endpoint, timeout=3)
            if resp.status_code >= 400:
                # Fallback anciennes versions/outils : GET
                resp = requests.get(endpoint, timeout=3)
            if resp.status_code >= 400:
                return self._err(f"Impossible d'ouvrir un nouvel onglet (HTTP {resp.status_code}).")
            data = resp.json() if resp.text else {}
            return self._ok("Nouvel onglet ouvert.", {"url": url, "title": data.get("title", "")})
        except Exception as e:
            return self._err(f"Erreur nouvel onglet: {e}")

    def activate_tab(self, tab: CDPTab) -> dict:
        """Bascule sur un onglet (le met au premier plan)."""
        try:
            resp = requests.get(f"{self._base}/json/activate/{tab.id}", timeout=2)
            if resp.status_code >= 400:
                return self._err("Impossible d'activer cet onglet.")
            return self._ok(f"Onglet activé : '{tab.title or tab.url}'.", {"tab": self._tab_dict(tab)})
        except Exception as e:
            return self._err(f"Activation onglet échouée: {e}")

    def close_tab_by_id(self, tab_id: str, label: str = "Onglet fermé.") -> dict:
        try:
            resp = requests.get(f"{self._base}/json/close/{tab_id}", timeout=2)
            if resp.status_code >= 400:
                return self._err("Impossible de fermer cet onglet.")
            return self._ok(label, {"tab_id": tab_id})
        except Exception as e:
            return self._err(f"Fermeture onglet échouée: {e}")

    # ── Résolution d'onglet ───────────────────────────────────────────────────

    def resolve_tab(
        self,
        index: int | None = None,
        query: str = "",
        fallback_first: bool = False,
        launch_if_missing: bool = False,
    ) -> CDPTab | dict:
        """
        Résout un onglet cible. Retourne :
          - CDPTab si trouvé
          - dict avec "ambiguous": True si plusieurs correspondances
          - dict d'erreur sinon
        """
        ready = self.ensure_session(launch_if_missing=launch_if_missing)
        if not ready["success"]:
            return ready

        tabs = self.get_tabs()
        if not tabs:
            return self._err("Aucun onglet pilotable détecté. Ouvre Chrome d'abord.")

        # Par index
        if index is not None:
            idx = int(index) - 1
            if 0 <= idx < len(tabs):
                return tabs[idx]
            return self._err(f"Onglet {index} introuvable.", {"available": len(tabs)})

        # Par recherche textuelle
        q = (query or "").strip().lower()
        if q:
            matches = [
                t for t in tabs
                if q in (t.title or "").lower() or q in (t.url or "").lower()
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                lines = [f"J'ai trouvé {len(matches)} onglets pour '{query}'. Lequel ?", "─" * 60]
                for i, t in enumerate(matches, 1):
                    lines.append(f"  {i}. {t.title or '(sans titre)'}  —  {t.url[:50]}")
                return {
                    "ambiguous": True,
                    "message": lines[0],
                    "choices": [self._tab_dict(t, i + 1) for i, t in enumerate(matches)],
                    "display": "\n".join(lines),
                }
            return self._err(f"Aucun onglet ne correspond à '{query}'.")

        # Fallback : premier onglet ou seul onglet
        if fallback_first or len(tabs) == 1:
            return tabs[0]

        # Ambiguïté : plusieurs onglets, pas de précision
        lines = ["Plusieurs onglets sont ouverts. Lequel vises-tu ?", "─" * 60]
        for i, t in enumerate(tabs, 1):
            lines.append(f"  {i}. {t.title or '(sans titre)'}  —  {t.url[:50]}")
        return {
            "ambiguous": True,
            "message": "Plusieurs onglets sont ouverts. Lequel vises-tu ?",
            "choices": [self._tab_dict(t, i + 1) for i, t in enumerate(tabs)],
            "display": "\n".join(lines),
        }

    # ── Primitives CDP ────────────────────────────────────────────────────────

    def cdp_call(self, tab: CDPTab, method: str, params: dict | None = None) -> dict:
        """Envoie une commande CDP à un onglet via WebSocket."""
        if not _WS_AVAILABLE:
            return self._err("websocket-client non installé. pip install websocket-client")
        if not tab.websocket_url:
            return self._err("Cet onglet n'expose pas de socket DevTools.")

        try:
            ws = create_connection(tab.websocket_url, timeout=5, suppress_origin=True)
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            raw = ws.recv()
            ws.close()
            payload = json.loads(raw)
            if "error" in payload:
                return self._err(f"CDP {method} échoué: {payload['error'].get('message', '')}")
            return self._ok("ok", payload.get("result") or {})
        except Exception as e:
            return self._err(f"Connexion CDP impossible: {e}")

    def cdp_eval(self, tab: CDPTab, expression: str, await_promise: bool = False) -> dict:
        """Évalue du JavaScript dans un onglet et retourne la valeur."""
        result = self.cdp_call(
            tab,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        )
        if not result["success"]:
            return result

        r = (result.get("data") or {}).get("result") or {}
        # Propager les erreurs JS
        if r.get("type") == "object" and r.get("subtype") == "error":
            return self._err(f"Erreur JS: {r.get('description', 'inconnue')}")
        return self._ok("ok", r.get("value"))

    def navigate_tab(self, tab: CDPTab, url: str) -> dict:
        """Navigue vers une URL dans un onglet."""
        url = normalize_url(url)
        call = self.cdp_call(tab, "Page.navigate", {"url": url})
        if not call["success"]:
            return call
        return self._ok(f"Navigation vers {url}.", {"url": url})

    def history_nav(self, tab: CDPTab, direction: str) -> dict:
        """Navigue dans l'historique (back/forward)."""
        script = "history.back(); true;" if direction == "back" else "history.forward(); true;"
        result = self.cdp_eval(tab, script)
        if not result["success"]:
            return result
        label = "Page précédente." if direction == "back" else "Page suivante."
        return self._ok(label, {"direction": direction})

    def reload_tab(self, tab: CDPTab, hard: bool = False) -> dict:
        """Recharge un onglet."""
        call = self.cdp_call(tab, "Page.reload", {"ignoreCache": hard})
        if not call["success"]:
            return call
        return self._ok("Page rechargée.", {})

    # ── Fallback Windows (sans CDP) ───────────────────────────────────────────

    def _tabs_from_windows(self) -> list[dict]:
        if not _WIN32_AVAILABLE:
            return []
        procs = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe"}
        result = []
        seen = set()
        for win in gw.getAllWindows():
            hwnd = getattr(win, "_hWnd", None)
            if not hwnd or hwnd in seen:
                continue
            seen.add(hwnd)
            try:
                title = (getattr(win, "title", "") or "").strip()
                if not title:
                    continue
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                import psutil
                pname = psutil.Process(pid).name().lower()
                if pname not in procs:
                    continue
                result.append({"index": len(result) + 1, "id": str(hwnd), "title": title, "url": "", "process": pname})
            except Exception:
                continue
        return result

    @staticmethod
    def _format_windows_tabs(tabs: list[dict]) -> str:
        lines = ["Fenêtres navigateur détectées :", "─" * 60]
        for t in tabs:
            lines.append(f"  {t['index']}. {t['title']}  ({t['process']})")
        return "\n".join(lines)

    # ── Lancement Chrome debug ────────────────────────────────────────────────

    def _launch_debug_chrome(self) -> dict:
        profile_dir = Path.home() / "AppData" / "Local" / "JarvisChrome"
        profile_dir.mkdir(parents=True, exist_ok=True)

        exes = [
            "chrome",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "msedge",
            "brave",
        ]
        flags = [
            f"--remote-debugging-port={self.debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        for exe in exes:
            try:
                subprocess.Popen([exe, *flags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info(f"Chrome debug lancé: {exe}")
                return self._ok("Chrome lancé en mode pilotable.", {"exe": exe})
            except Exception:
                continue

        return self._err(
            "Impossible de lancer Chrome en mode pilotable.",
            {"tip": "Vérifie que Google Chrome est installé."},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tab_dict(tab: CDPTab, index: int | None = None) -> dict:
        d = {"id": tab.id, "title": tab.title, "url": tab.url}
        if index is not None:
            d["index"] = index
        return d

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}


# ── Utilitaire partagé ────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Ajoute https:// si le schéma est absent, convertit une recherche en URL Google."""
    u = (url or "").strip()
    if not u or u == "about:blank":
        return u
    if re.match(r"^[a-z]+://", u, re.IGNORECASE):
        return u
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", u, re.IGNORECASE):
        return "https://" + u
    # Ressemble à une recherche → Google
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(u)