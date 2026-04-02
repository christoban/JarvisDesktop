"""
modules/browser/browser_control.py — Façade principale du browser
==================================================================

Semaine 5 — Améliorations :
  [S5-1] Lancement auto Chrome avec --remote-debugging-port=9222
  [S5-2] Gestion multi-onglets intelligente
  [S5-3] Recherche Google améliorée + extraction résultats
  [S5-4] Formulaires et téléchargements
  [S5-5] Résumé de page via Groq

CORRECTIONS AUDIT semaines 1-5 :
  [C4] list_search_results() : nouvelle méthode publique qui expose les résultats
       de la dernière recherche mémorisés dans CDPSession._shared_search_results
       (correction B14 semaine 5). Remplace l'ancien _browser_list_results qui
       pointait par erreur vers extract_links() (liens de la page).
  [C5] fill_form() : ajout du paramètre submit=False.
       Avant : submit était accepté par l'executor mais ignoré silencieusement.
       Après : si submit=True, appelle page_actions.submit_form() après remplissage.

Architecture :
    BrowserControl (façade)
    └─ CDPSession  : connexion WebSocket Chrome DevTools Protocol
    └─ PageActions : actions sur les pages (click, type, extract...)
    └─ AutonomousBrowser : navigation intelligente multi-étapes
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus

from config.logger import get_logger
from modules.browser.cdp_session import CDPSession, CDPTab, normalize_url
from modules.browser.page_actions import PageActions
from modules.browser.autonomous import AutonomousBrowser

logger = get_logger(__name__)

# ── URLs de recherche par moteur ──────────────────────────────────────────────
SITE_SEARCH_URLS = {
    "google":       "https://www.google.com/search?q={}",
    "bing":         "https://www.bing.com/search?q={}",
    "duckduckgo":   "https://duckduckgo.com/?q={}",
    "youtube":      "https://www.youtube.com/results?search_query={}",
    "github":       "https://github.com/search?q={}",
    "stackoverflow":"https://stackoverflow.com/search?q={}",
    "wikipedia":    "https://fr.wikipedia.org/w/index.php?search={}",
    "amazon":       "https://www.amazon.fr/s?k={}",
}

# ── Chemins Chrome selon l'OS ─────────────────────────────────────────────────
CHROME_PATHS = {
    "windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
    ],
    "linux":  ["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"],
    "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
}

CDP_PORT = 9222


class BrowserControl:
    """
    Façade principale — point d'entrée unique pour tout le contrôle navigateur.
    Toutes les méthodes retournent {success, message, data}.
    """

    def __init__(self):
        self._session = CDPSession(debug_port=CDP_PORT)
        self._page    = PageActions(self._session)
        self._auto    = AutonomousBrowser(self._session, self._page)

    # ══════════════════════════════════════════════════════════════════════════
    # [S5-1] LANCEMENT CHROME — automatique si absent
    # ══════════════════════════════════════════════════════════════════════════

    def ensure_chrome_running(self) -> dict:
        """Vérifie si Chrome tourne avec CDP, le lance sinon."""
        ready = self._session.ensure_session(launch_if_missing=False)
        if ready["success"]:
            return ready

        logger.info("Chrome non détecté — lancement automatique...")
        launch = self._launch_chrome()
        if not launch["success"]:
            return launch

        for _ in range(10):
            time.sleep(0.5)
            ready = self._session.ensure_session(launch_if_missing=False)
            if ready["success"]:
                logger.info("Chrome lancé et CDP connecté.")
                return self._ok("Chrome lancé avec succès.", {"launched": True})

        return self._err(
            "Chrome lancé mais CDP non disponible. "
            "Vérifie que Chrome est bien fermé avant de relancer Jarvis."
        )

    def _launch_chrome(self) -> dict:
        """
        Lance Chrome avec --remote-debugging-port=9222.

        CORRECTION CRITIQUE : utilise le même --user-data-dir que CDPSession
        (_launch_debug_chrome) pour garantir qu'une seule instance Chrome tourne.
        Avant : browser_control lançait Chrome sans --user-data-dir → profil
        par défaut → potentiellement une 2e fenêtre Chrome séparée du CDP,
        ce qui faisait que Ctrl+T s'appliquait à la mauvaise fenêtre (Image 2).
        Après : même profil JarvisChrome → une seule fenêtre → Ctrl+T garanti
        dans la fenêtre contrôlée par Jarvis (Image 1).
        """
        import platform
        system     = platform.system().lower()
        paths      = CHROME_PATHS.get("windows" if system == "windows" else system, [])
        chrome_exe = None

        for path in paths:
            if Path(path).exists():
                chrome_exe = path
                break

        if not chrome_exe:
            import shutil
            chrome_exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")

        if not chrome_exe:
            return self._err("Chrome introuvable. Installe Google Chrome ou ajoute-le au PATH.")

        # Même profil que CDPSession._launch_debug_chrome() — UNE SEULE instance
        profile_dir = Path.home() / "AppData" / "Local" / "JarvisChrome"
        profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            args = [
                chrome_exe,
                f"--remote-debugging-port={CDP_PORT}",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile_dir}",   # ← même profil que CDPSession
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
            ]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._ok(f"Chrome lancé ({Path(chrome_exe).name}).", {"exe": chrome_exe})
        except Exception as e:
            return self._err(f"Impossible de lancer Chrome : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # [S5-2] GESTION MULTI-ONGLETS INTELLIGENTE
    # ══════════════════════════════════════════════════════════════════════════

    def list_tabs(self) -> dict:
        """Liste tous les onglets ouverts avec leur titre et URL."""
        ready = self._ensure()
        if not ready["success"]:
            return ready

        tabs = self._session.get_tabs()
        if not tabs:
            return self._ok("Aucun onglet ouvert.", {"tabs": [], "count": 0})

        tab_list = [
            {
                "index":  i + 1,
                "id":     t.id,
                "title":  t.title or "(sans titre)",
                "url":    t.url or "",
                "active": i == 0,
            }
            for i, t in enumerate(tabs)
        ]

        lines = [f"{'N°':>3} {'TITRE':<40} URL", "─" * 75]
        for t in tab_list:
            mark = "▶ " if t["active"] else "  "
            lines.append(f"{mark}{t['index']:>2} {t['title'][:39]:<40} {t['url'][:28]}")

        return self._ok(
            f"{len(tabs)} onglet(s) ouvert(s).",
            {"tabs": tab_list, "count": len(tabs), "display": "\n".join(lines)},
        )

    def switch_to_tab(self, query: str = "", index: int = 0) -> dict:
        """Bascule sur un onglet par index (1-based) ou mot-clé (titre/URL)."""
        ready = self._ensure()
        if not ready["success"]:
            return ready

        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")

        target: CDPTab | None = None

        if index > 0:
            if index <= len(tabs):
                target = tabs[index - 1]
            else:
                return self._err(f"Onglet n°{index} inexistant (il y a {len(tabs)} onglet(s)).")
        elif query:
            q = query.lower().strip()
            for t in tabs:
                if q == (t.title or "").lower().strip():
                    target = t
                    break
            if not target:
                for t in tabs:
                    if q in (t.title or "").lower() or q in (t.url or "").lower():
                        target = t
                        break

        if not target:
            return self._err(
                f"Onglet '{query or index}' non trouvé. "
                "Dis 'liste les onglets' pour voir les onglets ouverts."
            )

        self._session.focus_tab(target)
        return self._ok(
            f"Basculé sur : {target.title or target.url or 'onglet'}.",
            {"tab": {"id": target.id, "title": target.title, "url": target.url}},
        )

    def new_tab(self, url: str = "") -> dict:
        """Ouvre un nouvel onglet (optionnellement avec une URL)."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        result = self._session.new_tab(url)
        if url and result["success"]:
            time.sleep(1.0)
        return result

    def close_tab(self, query: str = "", index: int = 0) -> dict:
        """Ferme un onglet par mot-clé ou index. Sans argument : ferme l'actif."""
        ready = self._ensure()
        if not ready["success"]:
            return ready

        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet à fermer.")

        if not query and not index:
            return self._session.close_tab_by_id(tabs[0].id)

        target: CDPTab | None = None
        if index > 0 and index <= len(tabs):
            target = tabs[index - 1]
        elif query:
            q = query.lower()
            for t in tabs:
                if q in (t.title or "").lower() or q in (t.url or "").lower():
                    target = t
                    break

        if not target:
            return self._err(f"Onglet '{query or index}' non trouvé.")

        return self._session.close_tab_by_id(target.id)

    def get_page_info(self) -> dict:
        """Retourne le titre et l'URL de l'onglet actif."""
        ready = self._ensure()
        if not ready["success"]:
            return ready

        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")

        active = tabs[0]
        return self._ok(
            f"Page active : {active.title or 'sans titre'} ({active.url or ''})",
            {"title": active.title, "url": active.url, "tab_count": len(tabs)},
        )

    # ══════════════════════════════════════════════════════════════════════════
    # [S5-3] RECHERCHE GOOGLE AMÉLIORÉE
    # ══════════════════════════════════════════════════════════════════════════

    def google_search(self, query: str, engine: str = "google", new_tab: bool = False) -> dict:
        """
        Lance une recherche et extrait les résultats.
        [S5-3] Correction B16 : suppression du `or True` parasite.
        """
        query = (query or "").strip()
        if not query:
            return self._err("Requête de recherche vide.")

        ready = self._session.ensure_session(launch_if_missing=True)
        if not ready["success"]:
            return self._os_search_fallback(query, engine)

        search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
        url        = search_url.format(quote_plus(query))

        tabs = self._session.get_tabs()
        if new_tab or not tabs:
            tab_result = self._session.new_tab(url)
            if not tab_result["success"]:
                return self._os_search_fallback(query, engine)
            time.sleep(2.0)
            tabs = self._session.get_tabs()
            if not tabs:
                return self._ok(
                    f"Recherche '{query}' lancée (nouvel onglet).",
                    {"query": query, "url": url}
                )
            active_tab = tabs[0]
        else:
            active_tab = tabs[0]
            nav = self._session.navigate_tab(active_tab, url)
            if not nav["success"]:
                return self._os_search_fallback(query, engine)
            time.sleep(2.0)

        extracted = self._page.extract_search_results(active_tab, max_results=8)
        if not extracted["success"] or not extracted.get("data", {}).get("results"):
            return self._ok(
                f"Recherche '{query}' lancée sur {engine.title()}. "
                "Dis 'ouvre le premier résultat' pour continuer.",
                {"query": query, "url": url, "engine": engine, "results": []}
            )

        data           = extracted["data"]
        data["query"]  = query
        data["engine"] = engine
        count          = data.get("count", 0)

        results = data.get("results", [])
        lines   = [f"Résultats pour '{query}' ({engine.title()}) :"]
        for r in results[:5]:
            lines.append(f"  {r.get('rank', '?')}. {r.get('title', '?')}")
            lines.append(f"     {r.get('url', '')[:60]}")
        if count > 5:
            lines.append(f"  ... et {count - 5} autres.")
        data["display"] = "\n".join(lines)

        return self._ok(
            f"'{query}' : {count} résultat(s). "
            "Dis 'ouvre le Nième résultat' pour naviguer.",
            data
        )

    def open_search_result(self, rank: int = 1) -> dict:
        """Ouvre le Nième résultat de la dernière recherche."""
        ready = self._ensure()
        if not ready["success"]:
            return ready

        result = self._page.open_search_result(rank)
        url    = (result.get("data") or {}).get("url", "")

        if url and result["success"]:
            tabs = self._session.get_tabs()
            if tabs:
                nav = self._session.navigate_tab(tabs[0], url)
                if nav["success"]:
                    time.sleep(1.5)
                    return self._ok(
                        f"Résultat n°{rank} ouvert : {url[:60]}",
                        {"url": url, "rank": rank}
                    )
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # [C4] RÉSULTATS DE RECHERCHE MÉMORISÉS — nouvelle méthode
    # ══════════════════════════════════════════════════════════════════════════

    def list_search_results(self) -> dict:
        """
        [C4] Retourne les résultats de la dernière recherche mémorisés.

        Les résultats sont stockés dans CDPSession._shared_search_results
        par PageActions.extract_search_results() (correction B14 semaine 5).

        Avant cette correction, _browser_list_results dans l'executor pointait
        vers extract_links() qui retournait les liens de la page active —
        pas du tout les résultats de recherche.
        """
        results = self._page._last_search_results  # propriété B14 dans page_actions.py

        if not results:
            return self._ok(
                "Aucun résultat mémorisé. Lance d'abord une recherche "
                "(ex: 'cherche Python sur Google').",
                {"results": [], "count": 0}
            )

        lines = [f"Résultats de recherche ({len(results)}) :"]
        for r in results[:8]:
            rank  = r.get("rank", "?")
            title = (r.get("title") or "(sans titre)")[:55]
            url   = (r.get("url") or "")[:60]
            lines.append(f"  {rank}. {title}")
            lines.append(f"     {url}")

        return self._ok(
            f"{len(results)} résultat(s) disponible(s). "
            "Dis 'ouvre le 2e résultat' pour en ouvrir un.",
            {
                "results": results,
                "count":   len(results),
                "display": "\n".join(lines),
            }
        )

    def _os_search_fallback(self, query: str, engine: str = "google") -> dict:
        """Fallback OS : ouvrir la recherche dans le navigateur par défaut."""
        search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
        url        = search_url.format(quote_plus(query))
        try:
            import platform
            system = platform.system().lower()
            if system == "windows":
                os.startfile(url)
            elif system == "darwin":
                subprocess.run(["open", url])
            else:
                subprocess.run(["xdg-open", url])
            return self._ok(
                f"Recherche '{query}' ouverte dans le navigateur par défaut.",
                {"query": query, "url": url, "fallback": True}
            )
        except Exception as e:
            return self._err(f"Impossible d'ouvrir la recherche : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # NAVIGATION DE BASE
    # ══════════════════════════════════════════════════════════════════════════

    def open_url(self, url: str) -> dict:
        """Ouvre une URL dans l'onglet actif."""
        url   = normalize_url(url.strip())
        if not url:
            return self._err("URL vide.")

        ready = self._ensure()
        if not ready["success"]:
            return self._os_open_fallback(url)

        tabs = self._session.get_tabs()
        if not tabs:
            result = self._session.new_tab(url)
        else:
            result = self._session.navigate_tab(tabs[0], url)

        if result["success"]:
            time.sleep(1.0)
        return result

    def navigate_back(self) -> dict:
        """Page précédente."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._session.history_nav(tabs[0], "back")

    def navigate_forward(self) -> dict:
        """Page suivante."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._session.history_nav(tabs[0], "forward")

    def reload_page(self) -> dict:
        """Recharger la page active."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._session.reload_tab(tabs[0])

    # ══════════════════════════════════════════════════════════════════════════
    # LECTURE ET RÉSUMÉ DE PAGE
    # ══════════════════════════════════════════════════════════════════════════

    def read_page(self) -> dict:
        """Extrait le texte de la page active."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._page.read_page(tabs[0])

    def summarize_page(self) -> dict:
        """Résume la page active via Groq IA."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._page.summarize_page_ai(tabs[0])

    def extract_links(self) -> dict:
        """Extrait tous les liens de la page active."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._page.extract_links(tabs[0])

    def scroll(self, direction: str = "down") -> dict:
        """Faire défiler la page (up/down/top/bottom)."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._page.scroll(tabs[0], direction)

    # ══════════════════════════════════════════════════════════════════════════
    # [S5-4] FORMULAIRES ET TÉLÉCHARGEMENTS
    # ══════════════════════════════════════════════════════════════════════════

    def fill_form(self, selector: str, value: str, submit: bool = False) -> dict:
        """
        Remplit un champ de formulaire et optionnellement soumet.

        [C5] Paramètre submit ajouté. Avant cette correction, submit était
        reçu par l'executor mais ignoré silencieusement car l'ancienne signature
        était fill_form(selector, value) sans submit.

        Args:
            selector : sélecteur CSS ("#search") ou texte du label ("Nom")
            value    : valeur à saisir
            submit   : si True, soumet le formulaire après remplissage
        """
        ready = self._ensure()
        if not ready["success"]:
            return ready

        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")

        tab = tabs[0]

        # Remplir le champ
        if selector.startswith(("#", ".", "[")):
            fill_result = self._page.fill_field_by_selector(tab, selector, value)
        else:
            fill_result = self._page.fill_field_by_label(tab, selector, value)

        if not fill_result["success"]:
            return fill_result

        # [C5] Soumettre si demandé
        if submit:
            submit_result = self._page.submit_form(tab)
            if submit_result["success"]:
                return self._ok(
                    f"Champ '{selector}' rempli avec '{value[:30]}' et formulaire soumis.",
                    {"selector": selector, "value": value, "submitted": True}
                )
            # Remplissage OK mais soumission échouée
            return self._ok(
                f"Champ '{selector}' rempli mais soumission échouée : "
                f"{submit_result.get('message', 'inconnu')}.",
                {
                    "selector":    selector,
                    "value":       value,
                    "submitted":   False,
                    "submit_error": submit_result.get("message", ""),
                }
            )

        return self._ok(
            f"Champ '{selector}' rempli avec '{value[:30]}'.",
            {"selector": selector, "value": value, "submitted": False}
        )

    def click_element(self, text: str) -> dict:
        """Clique sur un élément par son texte visible."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._page.click_text(tabs[0], text)

    def type_text(self, text: str, submit: bool = False) -> dict:
        """Tape du texte dans le champ actif de la page."""
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return self._page.smart_type(tabs[0], text, submit=submit)

    def download_file(self, url: str = "", save_dir: str = "") -> dict:
        """Télécharge un fichier depuis une URL ou depuis la page active."""
        import urllib.request
        import platform

        if not url:
            ready = self._ensure()
            if not ready["success"]:
                return ready
            tabs = self._session.get_tabs()
            url  = tabs[0].url if tabs else ""

        if not url or not url.startswith("http"):
            return self._err("URL de téléchargement invalide.")

        if not save_dir:
            save_dir = str(Path.home() / "Downloads")

        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)

        filename = url.split("/")[-1].split("?")[0] or "fichier_telecharge"
        dest     = save_path / filename

        try:
            logger.info(f"Téléchargement : {url} → {dest}")
            urllib.request.urlretrieve(url, str(dest))
            size_kb = dest.stat().st_size // 1024
            return self._ok(
                f"Fichier téléchargé : {filename} ({size_kb} Ko) dans {save_dir}.",
                {"filename": filename, "path": str(dest), "size_kb": size_kb, "url": url}
            )
        except Exception as e:
            return self._err(f"Téléchargement échoué : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # NAVIGATION INTELLIGENTE (délégue à AutonomousBrowser)
    # ══════════════════════════════════════════════════════════════════════════

    def go_to_site(self, site: str, query: str = "") -> dict:
        """Navigue vers un site connu (youtube, gmail, github...)."""
        return self._auto.go_to_site(site, query)

    def smart_search_and_open(self, query: str, engine: str = "google") -> dict:
        """Cherche + extrait les résultats intelligemment."""
        return self._auto.smart_search_and_open(query, engine)

    def find_best_result_and_open(self, query: str) -> dict:
        """Niveau 8 : cherche + sélectionne le meilleur résultat + ouvre."""
        return self._auto.find_best_result_and_open(query)

    def multi_step_task(self, steps: list[str]) -> dict:
        """Exécute une séquence de commandes navigateur."""
        return self._auto.multi_step_task(steps)

    def dispatch(self, natural_command: str) -> dict:
        """Exécute une commande navigateur unique en langage naturel."""
        return self._auto.execute_natural_command(natural_command)

    def get_browser_context(self) -> dict:
        """Retourne l'état actuel du navigateur."""
        return self._auto.get_browser_context()

    def close_browser(self) -> dict:
        """Ferme le navigateur (tous les onglets)."""
        ready = self._ensure()
        if not ready["success"]:
            return self._ok("Navigateur déjà fermé.", {})

        tabs    = self._session.get_tabs()
        closed  = 0
        for tab in tabs:
            result = self._session.close_tab_by_id(tab.id)
            if result.get("success"):
                closed += 1
        return self._ok(f"{closed} onglet(s) fermé(s).", {"closed": closed})

    def open_browser(self, browser: str = None, url: str = "") -> dict:
        """Ouvre le navigateur, optionnellement sur une URL."""
        result = self.ensure_chrome_running()
        if url and result["success"]:
            return self.open_url(url)
        return result

    def search_youtube(self, query: str) -> dict:
        """Cherche sur YouTube."""
        return self.go_to_site("youtube", query)

    def search_github(self, query: str) -> dict:
        """Cherche sur GitHub."""
        return self.go_to_site("github", query)

    def go_back(self) -> dict:
        return self.navigate_back()

    def go_forward(self) -> dict:
        return self.navigate_forward()

    def reload_tab(self, hard: bool = False, index: int = None) -> dict:
        return self.reload_page()

    def switch_tab(self, index: int = 0, query: str = "") -> dict:
        return self.switch_to_tab(query=query, index=index)

    def open_new_tab(self, url: str = "") -> dict:
        return self.new_tab(url)

    def navigate_to(self, url: str) -> dict:
        return self.open_url(url)

    def find_best_and_open(self, query: str) -> dict:
        return self.find_best_result_and_open(query)

    def click_text(self, text: str) -> dict:
        return self.click_element(text)

    def smart_type(self, text: str, submit: bool = False) -> dict:
        return self.type_text(text, submit)

    def fill_form_field(self, selector: str, value: str, submit: bool = False) -> dict:
        return self.fill_form(selector, value, submit)

    def extract_search_results(self) -> dict:
        return self.list_search_results()

    # ══════════════════════════════════════════════════════════════════════════
    # HELPERS PRIVÉS
    # ══════════════════════════════════════════════════════════════════════════

    def _ensure(self) -> dict:
        """S'assure que Chrome est prêt, le lance si nécessaire."""
        ready = self._session.ensure_session(launch_if_missing=True)
        if not ready["success"]:
            return self.ensure_chrome_running()
        return ready

    def _os_open_fallback(self, url: str) -> dict:
        """Ouvrir une URL via l'OS si CDP est indisponible."""
        try:
            import platform
            system = platform.system().lower()
            if system == "windows":
                os.startfile(url)
            elif system == "darwin":
                subprocess.run(["open", url])
            else:
                subprocess.run(["xdg-open", url])
            return self._ok(f"Page ouverte (OS) : {url[:60]}", {"url": url, "fallback": True})
        except Exception as e:
            return self._err(f"Impossible d'ouvrir la page : {e}")

    def health_check(self) -> dict:
        """Vérifie l'état du navigateur."""
        ready = self._session.ensure_session(launch_if_missing=False)
        tabs  = self._session.get_tabs() if ready["success"] else []
        return {
            "success": ready["success"],
            "message": "Navigateur opérationnel." if ready["success"] else "Navigateur non disponible.",
            "data": {
                "cdp_available": ready["success"],
                "tab_count":     len(tabs),
                "cdp_port":      CDP_PORT,
                "active_url":    tabs[0].url if tabs else None,
            }
        }

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}