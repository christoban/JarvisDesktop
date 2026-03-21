"""
browser/cdp_session.py — Session CDP et gestion des onglets
============================================================

Responsabilités :
  - Connexion / lancement de Chrome en mode debug (--remote-debugging-port=9222)
  - Résolution d'onglet (par index, par titre/URL, ambiguïté)
  - Opérations sur les onglets : list, new, switch, close, navigate, back, forward, reload
  - Primitive cdp_call / cdp_eval pour envoyer des commandes DevTools

Chrome DOIT être lancé avec :
  --remote-debugging-port=9222
  (BrowserAutomation.ensure_session() le fait automatiquement)

CORRECTIONS :
  [Bug 5] Ajout de execute_js() — alias de cdp_eval() utilisé par page_actions.py.
          Avant : page_actions.py appelait self._session.execute_js(tab, js)
                  → AttributeError : 'CDPSession' object has no attribute 'execute_js'
          Après : execute_js() délègue à cdp_eval() avec gestion du résultat JS.

  [Bug 4] new_tab() robuste : si Chrome est déjà en debug mais n'a pas d'onglet
          accessible (ex: chrome://newtab uniquement), on navigue dans l'onglet
          existant plutôt que d'ouvrir une nouvelle fenêtre système.
          Ajout de focus_tab() utilisé par browser_control.switch_to_tab().
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
        # Stockage partagé des résultats de recherche (correction B14)
        self._shared_search_results: list = []

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

        if not tabs and not include_internal:
            return all_tabs
        return tabs

    def list_tabs(self) -> dict:
        """Retourne un résultat structuré listant tous les onglets ouverts."""
        ready = self.ensure_session(launch_if_missing=False)
        if not ready["success"]:
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
        """
        Ouvre un nouvel onglet CDP.

        [Bug 4] Si Chrome vient d'être lancé et n'a qu'un onglet interne
        (chrome://newtab), on navigue dans cet onglet plutôt que d'ouvrir
        une nouvelle fenêtre système via /json/new.
        """
        ready = self.ensure_session(launch_if_missing=True)
        if not ready["success"]:
            return ready

        target_url = normalize_url(url) if url and url != "about:blank" else ""

        # [Bug 4] Vérifier si on a des onglets utilisables (non-internes)
        all_tabs = self.get_tabs(include_internal=True)
        user_tabs = [t for t in all_tabs if not t.url.startswith("chrome://")]

        # Si Chrome vient d'être lancé et n'a que des onglets internes,
        # naviguer dans le premier onglet interne plutôt qu'ouvrir une fenêtre
        if not user_tabs and all_tabs and target_url:
            nav = self.navigate_tab(all_tabs[0], target_url)
            if nav["success"]:
                return self._ok(
                    f"Onglet ouvert : {target_url}",
                    {"url": target_url, "title": all_tabs[0].title, "reused": True}
                )

        # Cas normal : ouvrir un vrai nouvel onglet via CDP
        endpoint = f"{self._base}/json/new"
        if target_url:
            endpoint += f"?{urllib.parse.quote(target_url, safe=':/?&=%')}"

        try:
            # Chrome récent préfère PUT sur /json/new
            resp = requests.put(endpoint, timeout=3)
            if resp.status_code >= 400:
                resp = requests.get(endpoint, timeout=3)
            if resp.status_code >= 400:
                return self._err(f"Impossible d'ouvrir un nouvel onglet (HTTP {resp.status_code}).")
            data = resp.json() if resp.text else {}
            new_tab_id = data.get("id", "")

            # Si une URL est demandée et que le tab a été créé sur about:blank,
            # naviguer explicitement vers l'URL cible
            if target_url and new_tab_id:
                tabs = self.get_tabs()
                target_tab = next((t for t in tabs if t.id == new_tab_id), None)
                if target_tab:
                    time.sleep(0.3)
                    self.navigate_tab(target_tab, target_url)

            return self._ok(
                "Nouvel onglet ouvert.",
                {"url": target_url or "about:blank", "title": data.get("title", "")}
            )
        except Exception as e:
            return self._err(f"Erreur nouvel onglet: {e}")

    def focus_tab(self, tab: CDPTab) -> dict:
        """
        Met un onglet au premier plan (focus).
        Utilisé par browser_control.switch_to_tab().
        Alias de activate_tab() pour une API plus claire.
        """
        return self.activate_tab(tab)

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

        if index is not None:
            idx = int(index) - 1
            if 0 <= idx < len(tabs):
                return tabs[idx]
            return self._err(f"Onglet {index} introuvable.", {"available": len(tabs)})

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

        if fallback_first or len(tabs) == 1:
            return tabs[0]

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
        if r.get("type") == "object" and r.get("subtype") == "error":
            return self._err(f"Erreur JS: {r.get('description', 'inconnue')}")
        return self._ok("ok", r.get("value"))

    def execute_js(self, tab: CDPTab, expression: str, await_promise: bool = False):
        """
        [Bug 5] Alias de cdp_eval() utilisé par page_actions.py.

        Avant cette correction, page_actions.py appelait :
            self._session.execute_js(tab, js)
        ce qui provoquait :
            AttributeError: 'CDPSession' object has no attribute 'execute_js'

        Cette méthode retourne directement la valeur JS (pas le dict complet)
        pour rester compatible avec l'usage dans page_actions.py qui attend
        le résultat brut (liste, dict, str, None...).

        Exemples d'appels dans page_actions.py :
            raw = self._session.execute_js(tab, js)
            if isinstance(raw, list):  # résultats de recherche
            ...
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):  # fill_field
        """
        result = self.cdp_eval(tab, expression, await_promise=await_promise)
        if not result["success"]:
            # Lever une exception pour que page_actions.py puisse la capturer
            # avec son try/except habituel
            raise RuntimeError(f"execute_js échoué: {result.get('message', 'erreur inconnue')}")
        # Retourner directement la valeur, pas le dict complet
        return result.get("data")

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
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(u)