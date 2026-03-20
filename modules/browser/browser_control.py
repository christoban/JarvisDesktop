"""
modules/browser_control.py — Façade principale du contrôle navigateur
=======================================================================

Point d'entrée unique pour TOUTES les actions navigateur.
Utilisé par IntentExecutor via les intents BROWSER_*.

Architecture interne :
  BrowserControl
    ├── CDPSession    (browser/cdp_session.py)   → connexion, onglets, navigation bas niveau
    ├── PageActions   (browser/page_actions.py)  → lecture, interaction, scroll, téléchargement
    └── AutonomousBrowser (browser/autonomous.py)→ tâches multi-étapes, navigation intelligente

Capacités complètes (niveaux 1→9) :
  Niveau 1 — Basique    : open, close, new_tab, back, forward, reload, switch_tab
  Niveau 2 — Recherche  : google_search, open_search_result, extract_results
  Niveau 3 — Navigation : navigate to URL, click, type in fields
  Niveau 4 — Lecture    : read_page, extract_links, get_page_info
  Niveau 5 — Analyse    : summarize_page (via Groq IA)
  Niveau 6 — Interaction: fill_form, click_text, smart_type, download
  Niveau 7 — Multi-onglets : list_tabs, switch, close ciblé
  Niveau 8 — Autonome   : find_best_and_open, go_to_site + search
  Niveau 9 — Séquences  : multi_step_task

Détection contextuelle :
  - Sait si Chrome est ouvert / en mode pilotable
  - Maintient le contexte : quel site est actif
  - Signale captcha, paywall, mot de passe
  - Propose des alternatives en cas d'échec
"""

from __future__ import annotations

import time
import webbrowser
import subprocess
import re
from urllib.parse import quote_plus
from config.logger import get_logger
from modules.browser.cdp_session import CDPSession, normalize_url
from modules.browser.page_actions import PageActions
from modules.browser.autonomous import AutonomousBrowser, SITE_MAP, SITE_SEARCH_URLS

logger = get_logger(__name__)


class BrowserControl:
    """
    Façade principale — toutes les actions navigateur passent par ici.
    Instanciation rapide, modules initialisés à la demande.
    """

    def __init__(self, debug_port: int = 9222, default_browser: str = "chrome"):
        self.default_browser = default_browser
        self._session = CDPSession(debug_port=debug_port)
        self._page = PageActions(self._session)
        self._auto = AutonomousBrowser(self._session, self._page)

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 1 — Contrôle basique
    # ══════════════════════════════════════════════════════════════════════════

    def open_browser(self, browser: str = None, url: str = "") -> dict:
        """Ouvre le navigateur, optionnellement sur une URL."""
        browser = (browser or self.default_browser).lower().strip()
        url = normalize_url(url) if url.strip() else ""

        # Si Chrome est déjà ouvert en mode CDP et qu'on a une URL → naviguer
        if url and self._session.is_ready():
            tabs = self._session.get_tabs()
            if tabs:
                result = self._session.navigate_tab(tabs[0], url)
                if result["success"]:
                    result["message"] = f"Navigation vers {url}."
                    return result

        # Lancer le navigateur
        result = self._session.ensure_session(launch_if_missing=True)
        if result["success"]:
            if url:
                tabs = self._session.get_tabs()
                if tabs:
                    self._session.navigate_tab(tabs[0], url)
                else:
                    self._session.new_tab(url)
            msg = f"{browser.title()} ouvert"
            if url:
                msg += f" sur {url}"
            return self._ok(msg + ".", {"browser": browser, "url": url})

        # Fallback OS
        try:
            if url:
                import os, platform
                if platform.system() == "Windows":
                    os.startfile(url)
                else:
                    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                webbrowser.open("about:blank")
            return self._ok(f"{browser.title()} ouvert.", {"browser": browser, "url": url})
        except Exception as e:
            return self._err(f"Impossible d'ouvrir {browser}: {e}")

    def close_browser(self) -> dict:
        """Ferme tous les onglets Chrome pilotables."""
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun navigateur pilotable détecté.")
        closed = 0
        for tab in tabs:
            result = self._session.close_tab_by_id(tab.id)
            if result["success"]:
                closed += 1
        return self._ok(f"{closed} onglet(s) fermé(s).", {"closed": closed})

    def open_new_tab(self, url: str = "") -> dict:
        """Ouvre un nouvel onglet."""
        url = normalize_url(url) if url.strip() else "about:blank"

        # CDP direct si dispo
        if self._session.is_ready():
            return self._session.new_tab(url)

        # Fallback OS : ouvrir l'URL (Chrome l'ouvre dans un nouvel onglet)
        if url and url != "about:blank":
            try:
                import os, platform
                if platform.system() == "Windows":
                    os.startfile(url)
                else:
                    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._ok(f"Nouvel onglet ouvert sur {url}.", {"url": url})
            except Exception:
                pass

        try:
            webbrowser.open_new_tab(url or "about:blank")
            return self._ok("Nouvel onglet ouvert.", {"url": url})
        except Exception as e:
            return self._err(f"Impossible d'ouvrir un nouvel onglet: {e}")

    def close_tab(self, index: int | None = None, query: str = "") -> dict:
        """Ferme un onglet par index ou recherche."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            if tab.get("ambiguous"):
                return self._ok(tab["message"], {"awaiting_choice": True, "choices": tab["choices"], "display": tab["display"]})
            return tab
        return self._session.close_tab_by_id(tab.id, label=f"Onglet '{tab.title or tab.url}' fermé.")

    def switch_tab(self, index: int | None = None, query: str = "") -> dict:
        """Bascule sur un onglet."""
        tab = self._session.resolve_tab(index=index, query=query, launch_if_missing=False)
        if isinstance(tab, dict):
            if tab.get("ambiguous"):
                return self._ok(tab["message"], {"awaiting_choice": True, "choices": tab["choices"], "display": tab["display"]})
            return tab
        return self._session.activate_tab(tab)

    def list_tabs(self) -> dict:
        """Liste tous les onglets ouverts."""
        return self._session.list_tabs()

    def go_back(self, index: int | None = None, query: str = "") -> dict:
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._session.history_nav(tab, "back")

    def go_forward(self, index: int | None = None, query: str = "") -> dict:
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._session.history_nav(tab, "forward")

    def reload_tab(self, hard: bool = False, index: int | None = None, query: str = "") -> dict:
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._session.reload_tab(tab, hard=hard)

    def open_url(self, url: str, new_tab: bool = False) -> dict:
        """Ouvre une URL dans l'onglet actif ou un nouvel onglet."""
        url = normalize_url(url)
        if not url:
            return self._err("URL vide.")

        if new_tab:
            return self.open_new_tab(url)

        # CDP si dispo
        tab = self._session.resolve_tab(fallback_first=True, launch_if_missing=True)
        if not isinstance(tab, dict):
            return self._session.navigate_tab(tab, url)

        # S'il n'y a pas encore d'onglet pilotable, en créer un.
        if "Aucun onglet pilotable" in (tab.get("message") or ""):
            created = self._session.new_tab(url)
            if created.get("success"):
                return created

        # Fallback OS
        try:
            import os, platform
            if platform.system() == "Windows":
                os.startfile(url)
            else:
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._ok(f"URL ouverte : {url}", {"url": url})
        except Exception as e:
            return self._err(f"Impossible d'ouvrir '{url}': {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 2 — Recherche web
    # ══════════════════════════════════════════════════════════════════════════

    def google_search(self, query: str, engine: str = "google", new_tab: bool = False) -> dict:
        """Lance une recherche et extrait les résultats."""
        query = (query or "").strip()
        if not query:
            return self._err("Requête de recherche vide.")

        # CDP : recherche + extraction des résultats
        ready = self._session.ensure_session(launch_if_missing=True)
        if ready["success"]:
            search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
            url = search_url.format(quote_plus(query))

            if new_tab:
                self._session.new_tab(url)
                time.sleep(1.5)
                tabs = self._session.get_tabs()
                tab = tabs[-1] if tabs else None
            else:
                tab = self._session.resolve_tab(fallback_first=True, launch_if_missing=False)
                if not isinstance(tab, dict) or tab.get("ambiguous"):
                    from modules.browser.cdp_session import CDPTab as _CDPTab
                    if isinstance(tab, _CDPTab):
                        pass
                    else:
                        tabs = self._session.get_tabs()
                        tab = tabs[0] if tabs else None

            if tab and not isinstance(tab, dict):
                nav = self._session.navigate_tab(tab, url)
                if nav["success"]:
                    time.sleep(1.8)
                    results = self._page.extract_search_results(tab, max_results=8)
                    if results["success"]:
                        data = results.get("data") or {}
                        data["query"] = query
                        data["engine"] = engine
                        count = data.get("count", 0)
                        return self._ok(
                            f"Recherche '{query}' : {count} résultat(s). "
                            f"Dis 'ouvre le premier' pour continuer.",
                            data,
                        )
                        return self._ok(
                            f"Recherche '{query}' lancée sur {engine.title()}.",
                            {"query": query, "engine": engine, "url": url},
                        )

        # Fallback OS
        url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"]).format(quote_plus(query))
        result = self.open_url(url)
        if result["success"]:
            result["message"] = f"Recherche '{query}' lancée sur {engine.title()}."
            result["data"] = {"query": query, "engine": engine, "url": url}
        return result

    def search_youtube(self, query: str) -> dict:
        return self.google_search(query, engine="youtube")

    def search_github(self, query: str) -> dict:
        return self.google_search(query, engine="github")

    def open_search_result(self, rank: int = 1, new_tab: bool = False) -> dict:
        """Ouvre le résultat de recherche numéro `rank`."""
        info = self._page.open_search_result(rank)
        if not info["success"]:
            return info

        url = (info.get("data") or {}).get("url")
        if not url:
            return info

        if new_tab:
            return self.open_new_tab(url)

        tab = self._session.resolve_tab(fallback_first=True, launch_if_missing=False)
        if not isinstance(tab, dict):
            return self._session.navigate_tab(tab, url)
        return self.open_url(url)

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 3 — Navigation intelligente
    # ══════════════════════════════════════════════════════════════════════════

    def go_to_site(self, site: str, query: str = "") -> dict:
        """Va sur un site connu (youtube, gmail, etc.), avec recherche optionnelle."""
        return self._auto.go_to_site(site, query)

    def navigate_to(self, url: str) -> dict:
        """Navigue vers n'importe quelle URL."""
        return self.open_url(url)

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 4 — Lecture de page
    # ══════════════════════════════════════════════════════════════════════════

    def read_page(self, index: int | None = None, query: str = "") -> dict:
        """Lit le contenu texte de la page active."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.read_page(tab)

    def get_page_info(self, index: int | None = None, query: str = "") -> dict:
        """Retourne le titre et l'URL de la page active."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.get_page_info(tab)

    def extract_links(self, index: int | None = None, query: str = "") -> dict:
        """Extrait tous les liens de la page active."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.extract_links(tab)

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 5 — Analyse IA
    # ══════════════════════════════════════════════════════════════════════════

    def summarize_page(self, index: int | None = None, query: str = "") -> dict:
        """Résume la page active via Groq IA."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.summarize_page_ai(tab)

    def extract_search_results(self, index: int | None = None, query: str = "") -> dict:
        """Extrait les résultats de recherche de la page active."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.extract_search_results(tab)

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 6 — Interaction avancée
    # ══════════════════════════════════════════════════════════════════════════

    def scroll(self, direction: str = "down", amount: int | None = None,
               index: int | None = None, query: str = "") -> dict:
        """Scrolle la page (up/down/top/bottom)."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.scroll(tab, direction=direction, amount=amount)

    def click_text(self, text: str, index: int | None = None, query: str = "") -> dict:
        """Clique sur un élément contenant `text`."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.click_text(tab, text)

    def fill_form_field(self, selector: str, value: str, submit: bool = False,
                        index: int | None = None, query: str = "") -> dict:
        """Remplit un champ de formulaire via sélecteur CSS."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.fill_form_field(tab, selector, value, submit=submit)

    def smart_type(self, text: str, submit: bool = False,
                   index: int | None = None, query: str = "") -> dict:
        """Tape du texte dans le champ principal de la page."""
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab
        return self._page.smart_type(tab, text, submit=submit)

    def download_file(self, url: str = "", link_text: str = "",
                      index: int | None = None, query: str = "") -> dict:
        """
        Télécharge un fichier.
        url       : URL directe à télécharger
        link_text : texte du lien à cliquer pour télécharger
        """
        tab = self._session.resolve_tab(index=index, query=query, fallback_first=True, launch_if_missing=False)
        if isinstance(tab, dict):
            return tab

        if url:
            return self._page.trigger_download(tab, normalize_url(url))
        elif link_text:
            return self._page.download_by_link_text(tab, link_text)
        else:
            return self._err("Précise une URL ou un texte de lien pour télécharger.")

    # ══════════════════════════════════════════════════════════════════════════
    #  NIVEAU 8-9 — Navigation autonome
    # ══════════════════════════════════════════════════════════════════════════

    def find_best_and_open(self, query: str) -> dict:
        """Cherche le meilleur résultat pour `query` et l'ouvre automatiquement."""
        return self._auto.find_best_result_and_open(query)

    def multi_step_task(self, steps: list[str]) -> dict:
        """Exécute une séquence d'actions navigateur."""
        return self._auto.multi_step_task(steps)

    def get_browser_context(self) -> dict:
        """Retourne l'état actuel du navigateur."""
        return self._auto.get_browser_context()

    # ══════════════════════════════════════════════════════════════════════════
    #  DISPATCH UNIFIÉ (utilisé par les macros navigateur)
    # ══════════════════════════════════════════════════════════════════════════

    def dispatch(self, natural_command: str) -> dict:
        """
        Dispatch une commande navigateur en langage naturel.
        Utilisé par AutonomousBrowser.multi_step_task().
        Interprétation simple basée sur des mots-clés.
        """
        cmd = natural_command.strip().lower()

        if any(k in cmd for k in ["cherche", "recherche", "google"]):
            query = re.sub(r"^(cherche|recherche|google)\s+", "", cmd).strip()
            return self.google_search(query)

        if "résume" in cmd or "resume" in cmd or "résumé" in cmd:
            return self.summarize_page()

        if "lis la page" in cmd or "lire la page" in cmd:
            return self.read_page()

        if "remonte" in cmd or "haut de la page" in cmd:
            return self.scroll("top")

        if "bas de la page" in cmd:
            return self.scroll("bottom")

        if "scrolle" in cmd or "scroll" in cmd:
            direction = "up" if "haut" in cmd else "down"
            return self.scroll(direction)

        if "nouvel onglet" in cmd or "new tab" in cmd:
            return self.open_new_tab()

        if "ferme" in cmd and "onglet" in cmd:
            return self.close_tab()

        if "recharge" in cmd or "actualise" in cmd:
            return self.reload_tab()

        if "retour" in cmd or "page précédente" in cmd:
            return self.go_back()

        # Tenter ouverture URL ou navigation site
        url = normalize_url(natural_command.strip())
        if url.startswith("http"):
            return self.open_url(url)

        for site in SITE_MAP:
            if site in cmd:
                return self.go_to_site(site)

        return self._err(f"Commande navigateur non reconnue: '{natural_command}'")

    # ══════════════════════════════════════════════════════════════════════════
    #  COMPATIBILITÉ — anciennes méthodes utilisées par IntentExecutor
    # ══════════════════════════════════════════════════════════════════════════

    def back(self, index: int | None = None, query: str = "") -> dict:
        return self.go_back(index=index, query=query)

    def forward(self, index: int | None = None, query: str = "") -> dict:
        return self.go_forward(index=index, query=query)

    def reload(self, index: int | None = None, query: str = "") -> dict:
        return self.reload_tab(index=index, query=query)

    def click_by_text(self, text: str, index: int | None = None, query: str = "") -> dict:
        return self.click_text(text, index=index, query=query)

    def fill_field(self, selector: str, value: str, submit: bool = False,
                   index: int | None = None, query: str = "") -> dict:
        return self.fill_form_field(selector, value, submit=submit, index=index, query=query)

    def open_result(self, rank: int = 1, new_tab: bool = False) -> dict:
        return self.open_search_result(rank=rank, new_tab=new_tab)

    def search_in_new_tab(self, query: str, engine: str = "google") -> dict:
        return self.google_search(query, engine=engine, new_tab=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}