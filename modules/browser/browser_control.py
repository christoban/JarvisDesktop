"""
modules/browser/browser_control.py — Façade principale du browser
==================================================================
Semaine 5 — Améliorations :

  [S5-1] Lancement auto Chrome avec --remote-debugging-port=9222
          Jarvis sait maintenant comment Chrome est lancé — plus besoin
          de le faire manuellement. CDPSession.ensure_session() le démarre si absent.

  [S5-2] Gestion multi-onglets intelligente :
          - find_tab_by_title() : trouver un onglet par titre/URL
          - switch_to_tab()     : basculer sur un onglet par index ou mot-clé
          - list_tabs()         : lister tous les onglets ouverts
          - close_tab()         : fermer l'onglet actif ou un onglet cible

  [S5-3] Recherche Google améliorée :
          - Correction définitive du `or True` parasite (B16 semaine 1)
          - Extraction des résultats avec titres, URLs, descriptions
          - Navigation vers un résultat par rang ("ouvre le 2e résultat")
          - Résumé automatique de la page ouverte

  [S5-4] Formulaires et téléchargements (délégués à PageActions) :
          - fill_form()      : remplir un formulaire par sélecteur/label
          - download_file()  : télécharger un fichier depuis l'URL active

  [S5-5] Résumé de page via Groq :
          - summarize_page() : résumé IA de la page active
          - read_page()      : extraire le texte brut de la page

Architecture (inchangée) :
    BrowserControl (façade)
        └─ CDPSession    : connexion WebSocket Chrome DevTools Protocol
        └─ PageActions   : actions sur les pages (click, type, extract...)
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
    "google":      "https://www.google.com/search?q={}",
    "bing":        "https://www.bing.com/search?q={}",
    "duckduckgo":  "https://duckduckgo.com/?q={}",
    "youtube":     "https://www.youtube.com/results?search_query={}",
    "github":      "https://github.com/search?q={}",
    "stackoverflow": "https://stackoverflow.com/search?q={}",
    "wikipedia":   "https://fr.wikipedia.org/w/index.php?search={}",
    "amazon":      "https://www.amazon.fr/s?k={}",
}

# ── Chemins Chrome selon l'OS ─────────────────────────────────────────────────
CHROME_PATHS = {
    "windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
    ],
    "linux": ["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"],
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
        self._page     = PageActions(self._session)
        self._auto     = AutonomousBrowser(self._session, self._page)

    # ══════════════════════════════════════════════════════════════════════════
    #  [S5-1] LANCEMENT CHROME — automatique si absent
    # ══════════════════════════════════════════════════════════════════════════

    def ensure_chrome_running(self) -> dict:
        """
        Vérifie si Chrome tourne avec CDP, le lance sinon.
        Appelé automatiquement par toutes les méthodes qui ont besoin du navigateur.
        """
        # Tenter de se connecter
        ready = self._session.ensure_session(launch_if_missing=False)
        if ready["success"]:
            return ready

        # Chrome non disponible — le lancer
        logger.info("Chrome non détecté — lancement automatique...")
        launch = self._launch_chrome()
        if not launch["success"]:
            return launch

        # Attendre que CDP soit prêt (max 5s)
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
        """Lance Chrome avec --remote-debugging-port=9222."""
        import platform
        system = platform.system().lower()
        paths = CHROME_PATHS.get("windows" if system == "windows" else system, [])

        chrome_exe = None
        for path in paths:
            if Path(path).exists():
                chrome_exe = path
                break

        if not chrome_exe:
            # Chercher chrome dans le PATH
            import shutil
            chrome_exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")

        if not chrome_exe:
            return self._err(
                "Chrome introuvable. Installe Google Chrome ou ajoute-le au PATH."
            )

        try:
            args = [
                chrome_exe,
                f"--remote-debugging-port={CDP_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-popup-blocking",
            ]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._ok(f"Chrome lancé ({Path(chrome_exe).name}).", {"exe": chrome_exe})
        except Exception as e:
            return self._err(f"Impossible de lancer Chrome : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  [S5-2] GESTION MULTI-ONGLETS INTELLIGENTE
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

        lines = [f"{'N°':>3}  {'TITRE':<40} URL"]
        lines.append("─" * 75)
        for t in tab_list:
            mark = "▶ " if t["active"] else "  "
            lines.append(f"{mark}{t['index']:>2}  {t['title'][:39]:<40} {t['url'][:28]}")

        return self._ok(
            f"{len(tabs)} onglet(s) ouvert(s).",
            {"tabs": tab_list, "count": len(tabs), "display": "\n".join(lines)},
        )

    def switch_to_tab(self, query: str = "", index: int = 0) -> dict:
        """
        Bascule sur un onglet par index (1-based) ou mot-clé (titre/URL).

        Exemple :
          switch_to_tab(index=2)         → onglet n°2
          switch_to_tab(query="youtube") → onglet YouTube
        """
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
            # Correspondance exacte titre
            for t in tabs:
                if q == (t.title or "").lower().strip():
                    target = t
                    break
            # Correspondance partielle titre
            if not target:
                for t in tabs:
                    if q in (t.title or "").lower() or q in (t.url or "").lower():
                        target = t
                        break

        if not target:
            return self._err(
                f"Onglet '{query or index}' non trouvé. "
                f"Dis 'liste les onglets' pour voir les onglets ouverts."
            )

        result = self._session.focus_tab(target)
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
        """
        Ferme un onglet par mot-clé ou index.
        Sans argument : ferme l'onglet actif.
        """
        ready = self._ensure()
        if not ready["success"]:
            return ready

        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet à fermer.")

        if not query and not index:
            # Fermer l'onglet actif (premier de la liste)
            return self._session.close_tab_by_id(tabs[0].id)

        # Trouver l'onglet cible
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
    #  [S5-3] RECHERCHE GOOGLE AMÉLIORÉE
    # ══════════════════════════════════════════════════════════════════════════

    def google_search(
        self,
        query: str,
        engine: str = "google",
        new_tab: bool = False,
    ) -> dict:
        """
        Lance une recherche et extrait les résultats.
        [S5-3] Correction B16 : suppression du `or True` parasite.

        Retourne les résultats (titre, url, description) pour permettre
        à l'utilisateur de dire "ouvre le 2e résultat".
        """
        query = (query or "").strip()
        if not query:
            return self._err("Requête de recherche vide.")

        # [B16 corrigé] : on appelle ensure_session directement, sans `or True`
        ready = self._session.ensure_session(launch_if_missing=True)
        if not ready["success"]:
            # Fallback OS : ouvrir Chrome avec l'URL de recherche
            return self._os_search_fallback(query, engine)

        search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
        url = search_url.format(quote_plus(query))

        # Ouvrir dans un nouvel onglet ou l'actuel
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

        # Extraire les résultats
        extracted = self._page.extract_search_results(active_tab, max_results=8)
        if not extracted["success"] or not extracted.get("data", {}).get("results"):
            return self._ok(
                f"Recherche '{query}' lancée sur {engine.title()}. "
                "Dis 'ouvre le premier résultat' pour continuer.",
                {"query": query, "url": url, "engine": engine, "results": []}
            )

        data = extracted["data"]
        data["query"] = query
        data["engine"] = engine
        count = data.get("count", 0)

        # Formater l'affichage
        results = data.get("results", [])
        lines = [f"Résultats pour '{query}' ({engine.title()}) :"]
        for r in results[:5]:
            lines.append(f"  {r.get('rank', '?')}. {r.get('title', '?')}")
            lines.append(f"     {r.get('url', '')[:60]}")
        if count > 5:
            lines.append(f"  ... et {count - 5} autres.")
        data["display"] = "\n".join(lines)

        return self._ok(
            f"'{query}' : {count} résultat(s). Dis 'ouvre le Nième résultat' pour naviguer.",
            data
        )

    def open_search_result(self, rank: int = 1) -> dict:
        """
        Ouvre le Nième résultat de la dernière recherche.
        Exemple : "ouvre le 2e résultat"
        """
        ready = self._ensure()
        if not ready["success"]:
            return ready

        result = self._page.open_search_result(rank)

        # Si le résultat contient une URL, naviguer dessus
        url = (result.get("data") or {}).get("url", "")
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

    def _os_search_fallback(self, query: str, engine: str = "google") -> dict:
        """Fallback OS : ouvrir la recherche dans le navigateur par défaut."""
        search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
        url = search_url.format(quote_plus(query))
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
    #  NAVIGATION DE BASE
    # ══════════════════════════════════════════════════════════════════════════

    def open_url(self, url: str) -> dict:
        """Ouvre une URL dans l'onglet actif."""
        url = normalize_url(url.strip())
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
    #  LECTURE ET RÉSUMÉ DE PAGE
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
    #  [S5-4] FORMULAIRES ET TÉLÉCHARGEMENTS
    # ══════════════════════════════════════════════════════════════════════════

    def fill_form(self, selector: str, value: str) -> dict:
        """
        Remplit un champ de formulaire.

        Args:
            selector : sélecteur CSS ou texte du label (ex: "#search", "Nom")
            value    : valeur à saisir
        """
        ready = self._ensure()
        if not ready["success"]:
            return ready
        tabs = self._session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")

        # Essayer par sélecteur CSS, puis par label
        if selector.startswith(("#", ".", "[")):
            return self._page.fill_field_by_selector(tabs[0], selector, value)
        else:
            return self._page.fill_field_by_label(tabs[0], selector, value)

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
        """
        Télécharge un fichier depuis une URL ou depuis la page active.

        Args:
            url      : URL directe du fichier (sinon : URL de la page active)
            save_dir : dossier de destination (défaut : ~/Downloads)
        """
        import urllib.request
        import platform

        if not url:
            # Utiliser l'URL de la page active
            ready = self._ensure()
            if not ready["success"]:
                return ready
            tabs = self._session.get_tabs()
            url = tabs[0].url if tabs else ""

        if not url or not url.startswith("http"):
            return self._err("URL de téléchargement invalide.")

        # Dossier de destination
        if not save_dir:
            system = platform.system().lower()
            if system == "windows":
                save_dir = str(Path.home() / "Downloads")
            else:
                save_dir = str(Path.home() / "Downloads")

        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)

        # Nom du fichier depuis l'URL
        filename = url.split("/")[-1].split("?")[0] or "fichier_telecharge"
        dest = save_path / filename

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
    #  NAVIGATION INTELLIGENTE (délégue à AutonomousBrowser)
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
        """
        Exécute une séquence de commandes navigateur.
        [B5 corrigé semaine 1] : utilise la session courante entre les étapes.
        """
        return self._auto.multi_step_task(steps)

    def get_browser_context(self) -> dict:
        """Retourne l'état actuel du navigateur."""
        return self._auto.get_browser_context()

    def close_browser(self) -> dict:
        """Ferme le navigateur (tous les onglets)."""
        ready = self._ensure()
        if not ready["success"]:
            return self._ok("Navigateur déjà fermé.", {})

        tabs = self._session.get_tabs()
        closed = 0
        for tab in tabs:
            result = self._session.close_tab_by_id(tab.id)
            if result.get("success"):
                closed += 1

        return self._ok(f"{closed} onglet(s) fermé(s).", {"closed": closed})

    # ══════════════════════════════════════════════════════════════════════════
    #  DISPATCH — commandes texte libres
    # ══════════════════════════════════════════════════════════════════════════

    def dispatch(self, command: str) -> dict:
        """
        Dispatch une commande en langage naturel vers la bonne méthode.
        Utilisé par les macros et le mode autonome.
        """
        cmd = command.strip().lower()

        # Recherche
        if any(k in cmd for k in ["cherche", "recherche", "google"]):
            q = re.sub(r"^(cherche|recherche|google)\s+", "", cmd).strip()
            return self.google_search(q)

        if "youtube" in cmd:
            q = re.sub(r".*youtube\s*", "", cmd).strip()
            return self.go_to_site("youtube", query=q)

        if "github" in cmd:
            q = re.sub(r".*github\s*", "", cmd).strip()
            return self.go_to_site("github", query=q)

        # Résultats de recherche
        rank_match = re.search(r"(premier|deuxi[eè]me|troisi[eè]me|\d+)[eè]?\s+r[eé]sultat", cmd)
        if rank_match:
            rank_str = rank_match.group(1)
            rank_map = {"premier": 1, "deuxième": 2, "troisième": 3, "deuxieme": 2, "troisieme": 3}
            rank = rank_map.get(rank_str, int(rank_str) if rank_str.isdigit() else 1)
            return self.open_search_result(rank)

        # Résumé / lecture
        if "résume" in cmd or "resume" in cmd or "résumé" in cmd:
            return self.summarize_page()
        if "lis la page" in cmd or "lire la page" in cmd:
            return self.read_page()

        # Onglets
        if "liste les onglets" in cmd or "onglets ouverts" in cmd:
            return self.list_tabs()
        if "nouvel onglet" in cmd:
            return self.new_tab()
        if "ferme l'onglet" in cmd or "ferme cet onglet" in cmd:
            return self.close_tab()

        # Navigation
        if "retour" in cmd or "page précédente" in cmd:
            return self.navigate_back()
        if "page suivante" in cmd:
            return self.navigate_forward()
        if "recharge" in cmd or "actualise" in cmd:
            return self.reload_page()

        # Scroll
        if "scroll" in cmd or "scrolle" in cmd or "défile" in cmd:
            direction = "up" if "haut" in cmd else "bottom" if "bas de la page" in cmd else "down"
            return self.scroll(direction)

        # URL directe
        url = normalize_url(command.strip())
        if url.startswith("http"):
            return self.open_url(url)

        return self._err(f"Commande navigateur non reconnue : '{command}'")

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS PRIVÉS
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
        tabs = self._session.get_tabs() if ready["success"] else []
        return {
            "success":     ready["success"],
            "message":     "Navigateur opérationnel." if ready["success"] else "Navigateur non disponible.",
            "data": {
                "cdp_available": ready["success"],
                "tab_count":     len(tabs),
                "cdp_port":      CDP_PORT,
                "active_url":    tabs[0].url if tabs else None,
            }
        }

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}