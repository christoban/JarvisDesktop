"""
browser/autonomous.py — Navigation autonome et tâches multi-étapes
===================================================================

Responsabilités :
  - "Trouve-moi le meilleur tutoriel Python et ouvre-le"
    → cherche + analyse + choisit + ouvre
  - "Va sur YouTube et cherche Python tutorial"
    → navigue + tape la recherche
  - "Va sur Gmail et ouvre mon dernier email"
    → navigue + trouve + ouvre
  - Gestion du contexte navigateur : "est-ce qu'on est sur Google ?"
  - Séquences d'actions avec rapport d'étapes
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

from config.logger import get_logger
from config.settings import GROQ_API_KEY, GROQ_MODEL_NAME
from modules.browser.cdp_session import CDPSession, CDPTab, normalize_url
from modules.browser.page_actions import PageActions

logger = get_logger(__name__)

# ── Sites courants pour la navigation directe ─────────────────────────────────

SITE_MAP = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://fr.wikipedia.org",
    "twitter": "https://twitter.com",
    "x": "https://twitter.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "amazon": "https://www.amazon.fr",
    "netflix": "https://www.netflix.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "whatsapp": "https://web.whatsapp.com",
    "notion": "https://www.notion.so",
    "figma": "https://www.figma.com",
    "discord": "https://discord.com/app",
    "twitch": "https://www.twitch.tv",
    "outlook": "https://outlook.live.com",
    "drive": "https://drive.google.com",
    "docs": "https://docs.google.com",
    "sheets": "https://sheets.google.com",
    "claude": "https://claude.ai",
    "chatgpt": "https://chat.openai.com",
    "openai": "https://openai.com",
    "python": "https://docs.python.org/fr",
    "mdn": "https://developer.mozilla.org",
    "npm": "https://www.npmjs.com",
    "pypi": "https://pypi.org",
}

# Moteurs de recherche par site
SITE_SEARCH_URLS = {
    "youtube":       "https://www.youtube.com/results?search_query={}",
    "github":        "https://github.com/search?q={}",
    "stackoverflow": "https://stackoverflow.com/search?q={}",
    "amazon":        "https://www.amazon.fr/s?k={}",
    "reddit":        "https://www.reddit.com/search/?q={}",
    "wikipedia":     "https://fr.wikipedia.org/w/index.php?search={}",
    "google":        "https://www.google.com/search?q={}",
    "bing":          "https://www.bing.com/search?q={}",
    "duckduckgo":    "https://duckduckgo.com/?q={}",
}


class AutonomousBrowser:
    """
    Niveau avancé : navigation autonome, multi-étapes, intelligente.
    Utilisé par BrowserControl pour les commandes complexes.
    """

    def __init__(self, session: CDPSession, page: PageActions):
        self.session = session
        self.page = page
        self._context: dict[str, Any] = {}   # contexte navigateur courant

    # ── Navigation intelligente ───────────────────────────────────────────────

    def go_to_site(self, site: str, query: str = "") -> dict:
        """
        Navigue vers un site connu ou une URL.
        Si `query` est fourni, lance une recherche sur ce site.

        Exemples :
          go_to_site("youtube", "Python tutorial")
          go_to_site("gmail")
          go_to_site("github", "jarvis windows agent")
        """
        site_lower = site.strip().lower()

        # Chercher la recherche directe par site d'abord
        if query and site_lower in SITE_SEARCH_URLS:
            url = SITE_SEARCH_URLS[site_lower].format(urllib.parse.quote_plus(query))
            tab = self._get_active_tab(launch=True)
            if isinstance(tab, dict):
                return tab
            result = self.session.navigate_tab(tab, url)
            if result["success"]:
                time.sleep(1.5)
                self._update_context(tab)
                return self._ok(
                    f"Recherche '{query}' sur {site.title()} lancée.",
                    {"url": url, "site": site, "query": query},
                )
            return result

        # URL connue ou normalisée
        url = SITE_MAP.get(site_lower) or normalize_url(site)
        tab = self._get_active_tab(launch=True)
        if isinstance(tab, dict):
            return tab

        result = self.session.navigate_tab(tab, url)
        if not result["success"]:
            return result

        time.sleep(1.2)
        self._update_context(tab)

        msg = f"Navigation vers {site.title()}."
        if query:
            # Tenter de remplir la barre de recherche du site
            search_result = self.page.smart_type(tab, query, submit=True)
            if search_result["success"]:
                msg = f"Navigation vers {site.title()} et recherche '{query}' lancée."

        return self._ok(msg, {"url": url, "site": site})

    def smart_search_and_open(self, query: str, engine: str = "google") -> dict:
        """
        Recherche intelligente :
        1. Lance la recherche
        2. Attend le chargement
        3. Extrait les résultats
        4. Propose d'ouvrir le meilleur

        Retourne les résultats + un message conversationnel.
        """
        search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
        url = search_url.format(urllib.parse.quote_plus(query))

        tab = self._get_active_tab(launch=True)
        if isinstance(tab, dict):
            return tab

        nav = self.session.navigate_tab(tab, url)
        if not nav["success"]:
            return nav

        time.sleep(1.8)  # attendre le chargement
        self._update_context(tab)

        extracted = self.page.extract_search_results(tab, max_results=8)
        if not extracted["success"]:
            return self._ok(
                f"Recherche '{query}' lancée sur {engine.title()}. Dis 'ouvre le premier résultat' pour continuer.",
                {"query": query, "engine": engine, "url": url},
            )

        data = extracted.get("data") or {}
        data["query"] = query
        data["engine"] = engine
        count = data.get("count", 0)

        return self._ok(
            f"Recherche '{query}' : {count} résultat(s) trouvé(s). "
            f"Dis 'ouvre le premier' ou 'ouvre le deuxième' pour continuer.",
            data,
        )

    def find_best_result_and_open(self, query: str) -> dict:
        """
        Niveau 8 : Trouve le meilleur résultat et l'ouvre automatiquement.
        1. Recherche
        2. Analyse les titres avec Groq
        3. Ouvre le meilleur
        """
        steps = []

        # Étape 1 : rechercher
        search = self.smart_search_and_open(query)
        steps.append({"step": 1, "action": f"Recherche '{query}'", "success": search["success"]})
        if not search["success"]:
            return self._with_steps(search, steps)

        results = (search.get("data") or {}).get("results") or []
        if not results:
            return self._with_steps(
                self._err("Aucun résultat trouvé."), steps
            )

        # Étape 2 : choisir le meilleur
        best_rank = self._pick_best_result(query, results)
        steps.append({"step": 2, "action": f"Sélection résultat #{best_rank}", "success": True})

        # Étape 3 : ouvrir
        best = self.page.open_search_result(best_rank)
        url = best.get("data", {}).get("url") or results[best_rank - 1].get("url", "")

        tab = self._get_active_tab(launch=False)
        if not isinstance(tab, dict):
            nav = self.session.navigate_tab(tab, url)
            steps.append({"step": 3, "action": f"Ouverture de {url[:50]}", "success": nav["success"]})
            if nav["success"]:
                time.sleep(1.5)
                return self._with_steps(
                    self._ok(
                        f"J'ai ouvert le meilleur résultat pour '{query}' (résultat #{best_rank}).",
                        {"url": url, "rank": best_rank, "results": results},
                    ),
                    steps,
                )

        return self._with_steps(best, steps)

    def multi_step_task(self, steps_description: list[str]) -> dict:
        """
        Exécute une séquence de commandes navigateur en ordre.
        Chaque élément de `steps_description` est une instruction naturelle.
        Utilisé par les macros navigateur.
        """
        results = []
        for i, step in enumerate(steps_description):
            logger.info(f"Multi-step navigateur {i+1}/{len(steps_description)}: {step}")
            # Import ici pour éviter import circulaire
            from modules.browser.browser_control import BrowserControl
            bc = BrowserControl()
            # Exécution simplifiée — chaque step est une commande dispatch
            result = bc.dispatch(step)
            results.append({
                "step": i + 1,
                "instruction": step,
                "success": result.get("success", False),
                "message": result.get("message", ""),
            })
            if not result["success"]:
                break
            time.sleep(0.8)

        ok_count = sum(1 for r in results if r["success"])
        return self._ok(
            f"Séquence navigateur : {ok_count}/{len(results)} étape(s) réussie(s).",
            {"steps": results, "ok": ok_count, "total": len(results)},
        )

    # ── Contexte navigateur ───────────────────────────────────────────────────

    def get_browser_context(self) -> dict:
        """
        Retourne l'état actuel du navigateur :
        site actif, titre, URL, nombre d'onglets.
        """
        ready = self.session.ensure_session(launch_if_missing=False)
        if not ready["success"]:
            return self._ok(
                "Le navigateur n'est pas ouvert en mode pilotable.",
                {"browser_open": False},
            )

        tabs = self.session.get_tabs()
        if not tabs:
            return self._ok(
                "Chrome est ouvert mais aucun onglet n'est détecté.",
                {"browser_open": True, "tab_count": 0},
            )

        active = tabs[0]
        site = self._extract_site_name(active.url)
        return self._ok(
            f"Navigateur actif : {active.title or '(sans titre)'} ({site}). {len(tabs)} onglet(s) ouvert(s).",
            {
                "browser_open": True,
                "active_title": active.title,
                "active_url": active.url,
                "active_site": site,
                "tab_count": len(tabs),
                "tabs": [{"title": t.title, "url": t.url} for t in tabs],
            },
        )

    def is_on_site(self, site: str) -> bool:
        """Vérifie si l'onglet actif est sur le site donné."""
        tabs = self.session.get_tabs()
        if not tabs:
            return False
        url = (tabs[0].url or "").lower()
        return site.lower().strip() in url

    def _update_context(self, tab: CDPTab):
        self._context["last_url"] = tab.url
        self._context["last_title"] = tab.title
        self._context["last_site"] = self._extract_site_name(tab.url)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_tab(self, launch: bool = False) -> CDPTab | dict:
        """Retourne l'onglet actif ou une erreur."""
        ready = self.session.ensure_session(launch_if_missing=launch)
        if not ready["success"]:
            return ready
        tabs = self.session.get_tabs()
        if not tabs:
            return self._err("Aucun onglet ouvert.")
        return tabs[0]

    def _pick_best_result(self, query: str, results: list[dict]) -> int:
        """Choisit le meilleur résultat via Groq ou heuristique simple."""
        if GROQ_API_KEY and results:
            try:
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                items = "\n".join(f"{r['rank']}. {r['title']} — {r['url']}" for r in results[:5])
                prompt = (
                    f"Recherche : '{query}'\n\n"
                    f"Résultats disponibles :\n{items}\n\n"
                    f"Quel numéro de résultat est le plus pertinent et fiable ? "
                    f"Réponds UNIQUEMENT avec le chiffre (1, 2, 3, 4 ou 5)."
                )
                resp = client.chat.completions.create(
                    model=GROQ_MODEL_NAME,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=5,
                    temperature=0.1,
                    timeout=6,
                )
                rank_str = (resp.choices[0].message.content or "").strip()
                rank = int(rank_str[0]) if rank_str and rank_str[0].isdigit() else 1
                return max(1, min(rank, len(results)))
            except Exception:
                pass
        return 1  # fallback : premier résultat

    @staticmethod
    def _extract_site_name(url: str) -> str:
        if not url:
            return "inconnu"
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower()
            host = host.replace("www.", "").replace("m.", "")
            return host.split(".")[0] if host else "inconnu"
        except Exception:
            return "inconnu"

    @staticmethod
    def _with_steps(result: dict, steps: list) -> dict:
        r = dict(result)
        data = dict(r.get("data") or {})
        data["steps"] = steps
        r["data"] = data
        return r

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}