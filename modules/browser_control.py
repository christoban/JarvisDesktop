"""
browser_control.py — Contrôle du navigateur web
Ouvrir un navigateur, recherche Google, ouvrir URL, onglets.

SEMAINE 5 — LUNDI — IMPLÉMENTATION COMPLÈTE
"""

import re
import subprocess
import webbrowser
import time
from urllib.parse import quote_plus
from config.logger import get_logger
from JarvisDesktop.modules.browser.autonomous import BrowserAutomation

logger = get_logger(__name__)

# ── Mapping navigateur → commandes système à essayer dans l'ordre ────────────
BROWSER_COMMANDS = {
    "chrome":         ["chrome", "google-chrome", "chromium-browser", "chromium"],
    "google chrome":  ["chrome", "google-chrome", "chromium-browser"],
    "firefox":        ["firefox", "firefox-esr"],
    "edge":           ["msedge", "microsoft-edge"],
    "microsoft edge": ["msedge", "microsoft-edge"],
    "opera":          ["opera"],
    "brave":          ["brave", "brave-browser"],
}

# Moteurs de recherche
SEARCH_ENGINES = {
    "google":        "https://www.google.com/search?q={}",
    "bing":          "https://www.bing.com/search?q={}",
    "duckduckgo":    "https://duckduckgo.com/?q={}",
    "youtube":       "https://www.youtube.com/results?search_query={}",
    "github":        "https://github.com/search?q={}",
    "stackoverflow": "https://stackoverflow.com/search?q={}",
}


class BrowserControl:
    """
    Contrôle du navigateur web.
    Toutes les méthodes retournent { "success": bool, "message": str, "data": dict | None }
    """

    def __init__(self, default_browser: str = "chrome"):
        self.default_browser = default_browser
        self.automation = BrowserAutomation()

    # ══════════════════════════════════════════════════════════════════════════
    #  Ouvrir navigateur
    # ══════════════════════════════════════════════════════════════════════════

    def open_browser(self, browser: str = None, url: str = "") -> dict:
        """
        Ouvre un navigateur, optionnellement sur une URL.
        Args:
            browser : "chrome", "firefox", "edge", "opera", "brave"
            url     : URL de démarrage (optionnel)
        """
        browser = (browser or self.default_browser).lower().strip()
        logger.info(f"Ouverture navigateur : '{browser}' url='{url}'")

        if url:
            url = self._normalize_url(url)

        # Essayer les commandes système
        commands = BROWSER_COMMANDS.get(browser, [browser])
        for cmd in commands:
            try:
                args = [cmd] + ([url] if url else [])
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info(f"Navigateur lancé : {cmd} (PID {proc.pid})")
                msg = f"'{browser.title()}' ouvert"
                if url:
                    msg += f" sur {url}"
                return self._ok(msg + ".", {"browser": browser, "url": url, "pid": proc.pid})
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug(f"Commande '{cmd}' échouée : {e}")

        # Fallback webbrowser Python
        try:
            target = url or "about:blank"
            webbrowser.open(target)
            return self._ok(
                f"Navigateur ouvert{' sur ' + url if url else ''} (mode webbrowser).",
                {"browser": "default", "url": target, "method": "webbrowser"}
            )
        except Exception as e:
            return self._err(f"Impossible d'ouvrir '{browser}' : {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    #  Recherche web
    # ══════════════════════════════════════════════════════════════════════════

    def google_search(self, query: str, engine: str = "google",
                      browser: str = None) -> dict:
        """
        Lance une recherche sur le moteur choisi.

        Args:
            query  : termes de recherche
            engine : "google" (défaut), "bing", "duckduckgo", "youtube", "github"
            browser: navigateur spécifique (optionnel)
        """
        query = query.strip()
        if not query:
            return self._err("La recherche ne peut pas être vide.")

        logger.info(f"Recherche web : '{query}' via {engine}")

        template = SEARCH_ENGINES.get(engine.lower(), SEARCH_ENGINES["google"])
        url       = template.format(quote_plus(query))

        result = self.open_browser(browser, url) if browser else self._open_url_system(url)

        if result["success"]:
            result["message"] = f"Recherche '{query}' lancée sur {engine.title()}."
            result["data"]    = {
                **(result.get("data") or {}),
                "query": query, "engine": engine, "url": url,
            }
        return result

    # ══════════════════════════════════════════════════════════════════════════
    #  Ouvrir URL / Onglet
    # ══════════════════════════════════════════════════════════════════════════

    def open_url(self, url: str, browser: str = None) -> dict:
        """
        Ouvre une URL spécifique.

        Args:
            url     : URL (avec ou sans https://)
            browser : navigateur à utiliser (optionnel)
        """
        url = url.strip()
        if not url:
            return self._err("URL vide.")

        url = self._normalize_url(url)
        logger.info(f"Ouverture URL : '{url}'")

        if browser:
            return self.open_browser(browser, url)
        return self._open_url_system(url)

    def open_new_tab(self, url: str = "") -> dict:
        """Ouvre un nouvel onglet dans la fenêtre navigateur existante."""
        target = self._normalize_url(url) if url.strip() else ""
        logger.info(f"Nouvel onglet : '{target or 'blank'}'")

        # 1. CDP déjà actif → utiliser l'API directement (jamais auto-launch)
        if self.automation._is_cdp_ready():
            result = self.automation.new_tab(target or "about:blank")
            if result["success"]:
                return result

        # 2. URL donnée → l'OS l'ouvre dans Chrome existant (nouveau onglet par défaut)
        if target:
            import os as _os
            import platform
            try:
                if platform.system() == "Windows":
                    _os.startfile(target)
                else:
                    subprocess.Popen(["xdg-open", target],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._ok(
                    f"Nouvel onglet ouvert sur {target}.",
                    {"url": target, "mode": "os_open"},
                )
            except Exception:
                pass
            try:
                webbrowser.open(target)
                return self._ok(
                    f"Nouvel onglet ouvert sur {target}.",
                    {"url": target, "mode": "webbrowser"},
                )
            except Exception as e:
                return self._err(f"Impossible d'ouvrir {target} : {e}")

        # 3. Onglet vide → Ctrl+T dans la fenêtre navigateur existante
        result = self._open_blank_tab_via_ctrl_t()
        if result["success"]:
            return result

        # 4. Dernier recours : webbrowser
        try:
            webbrowser.open_new_tab("about:blank")
            return self._ok("Nouvel onglet ouvert.", {"url": "about:blank", "mode": "webbrowser"})
        except Exception as e:
            return self._err(f"Impossible d'ouvrir un nouvel onglet : {e}")

    def _open_blank_tab_via_ctrl_t(self) -> dict:
        """Envoie Ctrl+T à une fenêtre navigateur existante."""
        try:
            import win32api
            import win32con
            import win32com.client
            import win32gui
            import win32process
            import psutil
            import pygetwindow as gw

            BROWSER_PROCS = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe"}
            chrome_hwnd = None
            for win in gw.getAllWindows():
                hwnd = getattr(win, "_hWnd", None)
                if not hwnd:
                    continue
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        continue
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    proc_name = psutil.Process(pid).name().lower()
                    if proc_name in BROWSER_PROCS:
                        title = (win32gui.GetWindowText(hwnd) or "").strip()
                        if title:
                            chrome_hwnd = hwnd
                            break
                except Exception:
                    continue

            if not chrome_hwnd:
                return self._err("Aucune fenêtre navigateur active trouvée.")

            try:
                win32gui.ShowWindow(chrome_hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
            try:
                win32gui.SetForegroundWindow(chrome_hwnd)
            except Exception:
                try:
                    shell = win32com.client.Dispatch("WScript.Shell")
                    shell.SendKeys('%')
                    win32gui.SetForegroundWindow(chrome_hwnd)
                except Exception:
                    pass

            time.sleep(0.12)
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord('T'), 0, 0, 0)
            win32api.keybd_event(ord('T'), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.15)

            return self._ok("Nouvel onglet ouvert.", {"url": "about:blank", "mode": "win32_ctrl_t"})

        except ImportError:
            return self._err("win32 non disponible.")
        except Exception as e:
            return self._err(f"Ctrl+T échoué : {e}")

    def open_multiple_tabs(self, count: int = 1, url: str = "") -> dict:
        count = max(1, int(count or 1))
        opened = []
        failures = []
        for _ in range(count):
            result = self.open_new_tab(url=url)
            if result["success"]:
                opened.append((result.get("data") or {}).get("url") or url or "about:blank")
                time.sleep(0.25)
            else:
                failures.append(result.get("message"))
                break

        if failures and not opened:
            return self._err(failures[0])

        message = f"{len(opened)} nouvel onglet(s) ouvert(s)."
        if failures:
            message += f" Arrêt après échec : {failures[0]}"
        return self._ok(message, {"count": len(opened), "opened": opened})

    def search_in_new_tab(self, query: str, engine: str = "google") -> dict:
        """Ouvre un nouvel onglet dans le navigateur existant, puis lance la recherche."""
        from urllib.parse import quote_plus
        if engine.lower() == "google":
            url = f"https://www.google.com/search?q={quote_plus(query)}"
        elif engine.lower() == "bing":
            url = f"https://www.bing.com/search?q={quote_plus(query)}"
        else:
            url = f"https://duckduckgo.com/?q={quote_plus(query)}"

        # Ouvrir directement l'URL (Chrome ouvre dans un nouvel onglet via os.startfile)
        result = self.open_new_tab(url=url)
        if not result["success"]:
            return result
        result = dict(result)
        result["message"] = f"Recherche '{query}' lancée sur {engine.title()} dans un nouvel onglet."
        data = result.get("data") or {}
        data["query"] = query
        data["engine"] = engine
        result["data"] = data
        return result

    def search_youtube(self, query: str) -> dict:
        """Raccourci recherche YouTube."""
        return self.google_search(query, engine="youtube")

    def search_github(self, query: str) -> dict:
        """Raccourci recherche GitHub."""
        return self.google_search(query, engine="github")

    # ══════════════════════════════════════════════════════════════════════════
    #  AUTOMATION AVANCÉE (CDP)
    # ══════════════════════════════════════════════════════════════════════════

    def list_tabs(self) -> dict:
        return self.automation.list_tabs()

    def switch_tab(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.switch_tab(index=index, query=query)

    def close_tab(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.close_tab(index=index, query=query)

    def go_back(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.back(index=index, query=query)

    def go_forward(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.forward(index=index, query=query)

    def reload_tab(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.reload(index=index, query=query)

    def read_page(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.read_page(index=index, query=query)

    def summarize_page(self, index: int | None = None, query: str = "") -> dict:
        return self.automation.summarize_page(index=index, query=query)

    def open_search_result(self, rank: int = 1, new_tab: bool = False) -> dict:
        return self.automation.open_result(rank=rank, new_tab=new_tab)

    def click_by_text(self, text: str, index: int | None = None, query: str = "") -> dict:
        return self.automation.click_text(text=text, index=index, query=query)

    def fill_field(self, selector: str, value: str, submit: bool = False,
                   index: int | None = None, query: str = "") -> dict:
        return self.automation.fill_text_field(
            selector=selector,
            value=value,
            submit=submit,
            index=index,
            query=query,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  UTILITAIRES
    # ══════════════════════════════════════════════════════════════════════════

    def _open_url_system(self, url: str) -> dict:
        """Ouvre une URL via la méthode native du système."""
        import platform, os
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(url)
            elif system == "Darwin":
                subprocess.Popen(["open", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._ok(f"URL ouverte : {url}", {"url": url})
        except Exception:
            try:
                webbrowser.open(url)
                return self._ok(f"URL ouverte : {url}", {"url": url, "method": "webbrowser"})
            except Exception as e2:
                return self._err(f"Impossible d'ouvrir '{url}' : {str(e2)}")

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Ajoute https:// si le schéma est absent."""
        url = url.strip()
        if not url:
            return url
        if url.startswith(("http://", "https://", "ftp://", "about:", "file://")):
            return url
        # localhost / IP locales → http
        if url.startswith("localhost") or re.match(r"^(127\.|192\.168\.|10\.)", url):
            return "http://" + url
        return "https://" + url

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}