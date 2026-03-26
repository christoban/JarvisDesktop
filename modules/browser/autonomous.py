"""
browser/autonomous.py — Navigation autonome et tâches multi-étapes
===================================================================
Semaine 6 — Améliorations :

  [S6-1] AuthManager : gestion cookies/sessions par site
         - Sauvegarde/restauration des cookies CDP
         - Détection de l'état de connexion (logged_in / login_required)
         - Attente intelligente après login

  [S6-2] ContentExtractor : extraction avancée avec BeautifulSoup + OCR fallback
         - Extraction article propre (titre, corps, auteur, date)
         - Résumé structuré multi-sections
         - Fallback Tesseract OCR si le texte JS est vide (page rendue en image)

  [S6-3] _dispatch_step() étendu : 30+ patterns FR/EN
         - Login, logout, accepter cookies
         - Attendre un élément, faire un screenshot
         - Aller sur un onglet spécifique
         - Scroller jusqu'à un texte/élément

  [S6-4] Commandes Gmail/email complexes
         - go_to_gmail_inbox() : navigation + attente chargement
         - open_latest_email() : click premier email non lu
         - open_email_by_subject() : cherche par objet
         - reply_to_email() : rédige + envoie une réponse
         - compose_email() : nouvel email complet

  [S6-5] Retry intelligent
         - multi_step_task() : retry automatique (max 2) si étape échoue
         - Détection blocage (captcha, paywall, rate-limit) avant retry
         - Rapport d'erreur détaillé par étape

  [S6-6] Contexte enrichi
         - _context stocke : url, titre, site, état auth, dernières actions
         - get_browser_context() retourne l'état auth en plus

CORRECTIONS SEMAINE 1 conservées :
  [B5] multi_step_task ne crée plus un nouveau BrowserControl à chaque étape.
       Utilise _dispatch_step() qui réutilise la session CDP courante.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

from config.logger import get_logger
from config.settings import GROQ_API_KEY, GROQ_MODEL_NAME
from modules.browser.cdp_session import CDPSession, CDPTab, normalize_url
from modules.browser.page_actions import PageActions

logger = get_logger(__name__)

# ── Sites courants pour la navigation directe ─────────────────────────────────

SITE_MAP = {
    "youtube":       "https://www.youtube.com",
    "gmail":         "https://mail.google.com",
    "google":        "https://www.google.com",
    "github":        "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia":     "https://fr.wikipedia.org",
    "twitter":       "https://twitter.com",
    "x":             "https://twitter.com",
    "linkedin":      "https://www.linkedin.com",
    "reddit":        "https://www.reddit.com",
    "amazon":        "https://www.amazon.fr",
    "netflix":       "https://www.netflix.com",
    "facebook":      "https://www.facebook.com",
    "instagram":     "https://www.instagram.com",
    "whatsapp":      "https://web.whatsapp.com",
    "notion":        "https://www.notion.so",
    "figma":         "https://www.figma.com",
    "discord":       "https://discord.com/app",
    "twitch":        "https://www.twitch.tv",
    "outlook":       "https://outlook.live.com",
    "drive":         "https://drive.google.com",
    "docs":          "https://docs.google.com",
    "sheets":        "https://sheets.google.com",
    "claude":        "https://claude.ai",
    "chatgpt":       "https://chat.openai.com",
    "openai":        "https://openai.com",
    "python":        "https://docs.python.org/fr",
    "mdn":           "https://developer.mozilla.org",
    "npm":           "https://www.npmjs.com",
    "pypi":          "https://pypi.org",
    "hotmail":       "https://outlook.live.com",
    "yahoo":         "https://mail.yahoo.com",
    "protonmail":    "https://mail.proton.me",
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

# ── Répertoire de stockage des cookies ───────────────────────────────────────
_COOKIES_DIR = Path.home() / "AppData" / "Local" / "JarvisDesktop" / "cookies"


# ══════════════════════════════════════════════════════════════════════════════
# [S6-1] AUTH MANAGER — cookies et sessions
# ══════════════════════════════════════════════════════════════════════════════

class AuthManager:
    """
    Gère la persistance des cookies et l'état de connexion par site.
    Les cookies sont sauvegardés en JSON dans JarvisDesktop/cookies/<site>.json.

    Usage :
        auth = AuthManager(session)
        auth.save_cookies(tab, "gmail")    # après s'être connecté manuellement
        auth.restore_cookies(tab, "gmail") # pour se reconnecter automatiquement
        state = auth.check_login_state(tab, "gmail")
    """

    # Sélecteurs qui indiquent qu'on est connecté sur chaque site
    _LOGIN_INDICATORS: dict[str, list[str]] = {
        "gmail":    ["[aria-label='Boîte de réception']", "[data-email]", ".gb_A"],
        "github":   ["[aria-label='View profile']", ".Header-link--profile", ".avatar-user"],
        "twitter":  ["[aria-label='Home']", "[data-testid='SideNav_AccountSwitcher_Button']"],
        "linkedin": [".global-nav__me", ".nav__button-secondary--ghost"],
        "facebook": ["[aria-label='Facebook']", "#userNavigationLabel"],
        "outlook":  ["[aria-label='Folder list']", ".ms-FocusZone"],
        "notion":   [".notion-sidebar", ".notion-cursor-listener"],
    }

    # Sélecteurs du bouton/champ login pour détecter si on N'est PAS connecté
    _LOGOUT_INDICATORS: dict[str, list[str]] = {
        "gmail":    ["[type='email']", "[data-action='login']"],
        "github":   [".btn-primary[href='/login']", "#login"],
        "twitter":  ["[data-testid='loginButton']", "[href='/login']"],
        "linkedin": ["[data-tracking-control-name='guest_homepage-basic_sign-in-submit']"],
        "facebook": ["[name='email']", "[name='pass']"],
    }

    def __init__(self, session: CDPSession):
        self._session = session
        _COOKIES_DIR.mkdir(parents=True, exist_ok=True)

    def save_cookies(self, tab: CDPTab, site: str) -> dict:
        """
        Sauvegarde les cookies du site courant en JSON.
        Appeler après s'être connecté manuellement.
        """
        js = "document.cookie"
        try:
            raw_cookies = self._session.execute_js(tab, js)
            # Récupérer aussi via CDP Network.getAllCookies pour plus de précision
            cdp_result = self._session.cdp_call(tab, "Network.getAllCookies")
            cdp_cookies = []
            if cdp_result["success"]:
                cdp_cookies = (cdp_result.get("data") or {}).get("cookies", [])

            cookie_data = {
                "site":        site,
                "url":         tab.url,
                "js_cookies":  raw_cookies or "",
                "cdp_cookies": cdp_cookies,
                "saved_at":    time.strftime("%Y-%m-%dT%H:%M:%S"),
            }

            cookie_file = _COOKIES_DIR / f"{site}.json"
            cookie_file.write_text(json.dumps(cookie_data, indent=2, ensure_ascii=False))
            logger.info(f"Cookies {site} sauvegardés ({len(cdp_cookies)} cookies CDP)")
            return _ok(f"Cookies {site} sauvegardés ({len(cdp_cookies)} cookies).", {"site": site, "count": len(cdp_cookies)})
        except Exception as e:
            return _err(f"Sauvegarde cookies échouée : {e}")

    def restore_cookies(self, tab: CDPTab, site: str) -> dict:
        """
        Restaure les cookies sauvegardés pour un site.
        Navigue sur le site si nécessaire, puis injecte les cookies.
        """
        cookie_file = _COOKIES_DIR / f"{site}.json"
        if not cookie_file.exists():
            return _err(
                f"Aucun cookie sauvegardé pour '{site}'. "
                f"Connecte-toi manuellement puis dis 'sauvegarde ma session {site}'."
            )

        try:
            data = json.loads(cookie_file.read_text())
            cdp_cookies = data.get("cdp_cookies", [])
            if not cdp_cookies:
                return _err(f"Cookies {site} vides ou invalides.")

            # Activer le domaine réseau CDP
            self._session.cdp_call(tab, "Network.enable")

            # Injecter chaque cookie
            ok_count = 0
            for cookie in cdp_cookies:
                result = self._session.cdp_call(tab, "Network.setCookie", {
                    "name":     cookie.get("name", ""),
                    "value":    cookie.get("value", ""),
                    "domain":   cookie.get("domain", ""),
                    "path":     cookie.get("path", "/"),
                    "secure":   cookie.get("secure", False),
                    "httpOnly": cookie.get("httpOnly", False),
                    "sameSite": cookie.get("sameSite", "Lax"),
                })
                if result["success"]:
                    ok_count += 1

            logger.info(f"Cookies {site} restaurés : {ok_count}/{len(cdp_cookies)}")
            return _ok(
                f"{ok_count} cookie(s) restauré(s) pour {site}.",
                {"site": site, "restored": ok_count, "total": len(cdp_cookies)}
            )
        except Exception as e:
            return _err(f"Restauration cookies échouée : {e}")

    def check_login_state(self, tab: CDPTab, site: str) -> dict:
        """
        Vérifie si on est connecté sur le site.
        Retourne : {logged_in: bool, site, indicators_found}
        """
        site_key = self._normalize_site(site)
        login_selectors = self._LOGIN_INDICATORS.get(site_key, [])
        logout_selectors = self._LOGOUT_INDICATORS.get(site_key, [])

        found_login = []
        found_logout = []

        for sel in login_selectors:
            js = f"!!document.querySelector({json.dumps(sel)})"
            try:
                result = self._session.execute_js(tab, js)
                if result:
                    found_login.append(sel)
            except Exception:
                pass

        for sel in logout_selectors:
            js = f"!!document.querySelector({json.dumps(sel)})"
            try:
                result = self._session.execute_js(tab, js)
                if result:
                    found_logout.append(sel)
            except Exception:
                pass

        # Heuristique : si on a des indicateurs login → connecté
        # Si on a des indicateurs logout ET aucun login → non connecté
        if found_login:
            logged_in = True
        elif found_logout:
            logged_in = False
        else:
            # Vérification générique de l'URL
            url = (tab.url or "").lower()
            logged_in = not any(k in url for k in ["login", "signin", "sign-in", "accounts.google"])

        return _ok(
            f"{'Connecté' if logged_in else 'Non connecté'} sur {site}.",
            {
                "logged_in":      logged_in,
                "site":           site,
                "login_found":    found_login,
                "logout_found":   found_logout,
            }
        )

    def delete_cookies(self, site: str) -> dict:
        """Supprime les cookies sauvegardés pour un site."""
        cookie_file = _COOKIES_DIR / f"{site}.json"
        if cookie_file.exists():
            cookie_file.unlink()
            return _ok(f"Cookies {site} supprimés.", {"site": site})
        return _err(f"Aucun cookie sauvegardé pour '{site}'.")

    def list_saved_sessions(self) -> dict:
        """Liste les sessions/cookies sauvegardés."""
        files = list(_COOKIES_DIR.glob("*.json"))
        sessions = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                sessions.append({
                    "site":     f.stem,
                    "url":      data.get("url", ""),
                    "saved_at": data.get("saved_at", ""),
                    "cookies":  len(data.get("cdp_cookies", [])),
                })
            except Exception:
                sessions.append({"site": f.stem, "error": "fichier corrompu"})

        if not sessions:
            return _ok("Aucune session sauvegardée.", {"sessions": []})

        lines = ["Sessions sauvegardées :"]
        for s in sessions:
            lines.append(f"  • {s['site']} — {s.get('cookies', 0)} cookies — {s.get('saved_at', '?')}")

        return _ok(
            f"{len(sessions)} session(s) sauvegardée(s).",
            {"sessions": sessions, "display": "\n".join(lines)}
        )

    @staticmethod
    def _normalize_site(site: str) -> str:
        """Normalise le nom du site (gmail.com → gmail)."""
        s = site.lower().strip()
        for key in ["mail.google", "gmail"]:
            if key in s:
                return "gmail"
        for key in ["github.com", "github"]:
            if key in s:
                return "github"
        return s.split(".")[0]


# ══════════════════════════════════════════════════════════════════════════════
# [S6-2] CONTENT EXTRACTOR — BeautifulSoup + OCR
# ══════════════════════════════════════════════════════════════════════════════

class ContentExtractor:
    """
    Extraction avancée du contenu des pages.
    Stratégie :
      1. Extraction JS (rapide, sans dépendances)
      2. BeautifulSoup si beautifulsoup4 est installé (meilleur nettoyage HTML)
      3. Tesseract OCR si le texte est vide (page rendue en image / canvas)
    """

    def __init__(self, session: CDPSession):
        self._session = session

    def extract_article(self, tab: CDPTab) -> dict:
        """
        Extrait le contenu principal d'un article/page.
        Retourne : {title, author, date, body, word_count, url}
        """
        # Étape 1 : extraction JS rapide
        js = """
        (() => {
            const selectors = {
                title:  ['h1', 'article h1', '.post-title', '.entry-title', '[itemprop="headline"]'],
                author: ['[itemprop="author"]', '.author', '.byline', '[rel="author"]', '.post-author'],
                date:   ['[itemprop="datePublished"]', 'time[datetime]', '.date', '.published', '.post-date'],
                body:   ['article', '[itemprop="articleBody"]', '.post-content', '.entry-content',
                         '.article-body', 'main', '#content', '.content'],
            };

            const first = (sels) => {
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) return el;
                }
                return null;
            };

            const titleEl  = first(selectors.title);
            const authorEl = first(selectors.author);
            const dateEl   = first(selectors.date);
            const bodyEl   = first(selectors.body);

            // Nettoyer le body : supprimer nav, ads, scripts inline
            if (bodyEl) {
                ['script','style','nav','aside','iframe','[class*="ad"]','[class*="sidebar"]',
                 '[class*="comment"]','[id*="comment"]'].forEach(s => {
                    bodyEl.querySelectorAll(s).forEach(n => n.remove());
                });
            }

            const rawText = bodyEl?.innerText || document.body?.innerText || '';
            const clean = rawText.replace(/[ \\t]{2,}/g, ' ')
                                 .replace(/[\\n]{3,}/g, '\\n\\n')
                                 .trim();

            return {
                title:      titleEl?.innerText?.trim() || document.title || '',
                author:     authorEl?.innerText?.trim() || '',
                date:       dateEl?.getAttribute('datetime') || dateEl?.innerText?.trim() || '',
                body:       clean.slice(0, 8000),
                word_count: clean.split(/\\s+/).filter(Boolean).length,
                url:        window.location.href,
                has_content: clean.length > 100,
            };
        })()
        """
        try:
            result = self._session.execute_js(tab, js)
            if not isinstance(result, dict):
                return _err("Extraction JS échouée.")

            # Si le contenu est vide → essayer BeautifulSoup via le HTML source
            if not result.get("has_content"):
                logger.info("Contenu JS vide — tentative BeautifulSoup")
                bs_result = self._extract_with_bs4(tab)
                if bs_result["success"]:
                    return bs_result

                # Dernier recours : OCR screenshot
                logger.info("BeautifulSoup échoué — tentative OCR")
                return self._extract_with_ocr(tab, result.get("title", ""))

            return _ok(
                f"Article extrait : '{result.get('title', '')[:50]}' ({result.get('word_count', 0)} mots).",
                result
            )
        except Exception as e:
            return _err(f"extract_article échoué : {e}")

    def _extract_with_bs4(self, tab: CDPTab) -> dict:
        """Extraction via BeautifulSoup4 si disponible."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.debug("beautifulsoup4 non installé — pip install beautifulsoup4")
            return _err("BeautifulSoup4 non disponible.")

        try:
            # Récupérer le HTML complet via CDP
            js_html = "document.documentElement.outerHTML"
            html = self._session.execute_js(tab, js_html)
            if not isinstance(html, str) or len(html) < 100:
                return _err("HTML source vide.")

            soup = BeautifulSoup(html, "html.parser")

            # Supprimer les éléments parasites
            for tag in soup(["script", "style", "nav", "header", "footer",
                              "aside", "iframe", "noscript"]):
                tag.decompose()

            # Chercher le contenu principal
            body_el = (
                soup.find("article") or
                soup.find(attrs={"itemprop": "articleBody"}) or
                soup.find("main") or
                soup.find(id=re.compile(r"content|article|main", re.I)) or
                soup.find(class_=re.compile(r"content|article|post|entry", re.I)) or
                soup.body
            )

            text = body_el.get_text(separator="\n", strip=True) if body_el else ""
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

            title_el = soup.find("h1") or soup.find("title")
            title = title_el.get_text(strip=True) if title_el else ""

            if len(text) < 50:
                return _err("BeautifulSoup : contenu insuffisant.")

            return _ok(
                f"Contenu extrait via BeautifulSoup ({len(text.split())} mots).",
                {
                    "title":      title,
                    "body":       text[:8000],
                    "word_count": len(text.split()),
                    "url":        tab.url,
                    "via":        "beautifulsoup",
                    "has_content": True,
                }
            )
        except Exception as e:
            return _err(f"BeautifulSoup échoué : {e}")

    def _extract_with_ocr(self, tab: CDPTab, title: str = "") -> dict:
        """
        Dernier recours : capture un screenshot et applique Tesseract OCR.
        Nécessite : pip install pytesseract pillow + Tesseract installé.
        """
        try:
            import pytesseract
            from PIL import Image
            import io
            import base64
        except ImportError:
            return _err(
                "OCR non disponible. Installe : pip install pytesseract pillow "
                "et Tesseract depuis https://github.com/UB-Mannheim/tesseract/wiki"
            )

        try:
            # Capture screenshot via CDP
            screenshot_result = self._session.cdp_call(
                tab, "Page.captureScreenshot",
                {"format": "png", "quality": 90, "captureBeyondViewport": False}
            )
            if not screenshot_result["success"]:
                return _err("Screenshot CDP échoué pour OCR.")

            img_b64 = (screenshot_result.get("data") or {}).get("data", "")
            if not img_b64:
                return _err("Screenshot vide.")

            # OCR avec Tesseract
            img_bytes = base64.b64decode(img_b64)
            image = Image.open(io.BytesIO(img_bytes))

            # OCR multilingue (français + anglais)
            text = pytesseract.image_to_string(image, lang="fra+eng", config="--psm 3")
            text = text.strip()

            if len(text) < 30:
                return _err("OCR : texte trop court ou illisible.")

            return _ok(
                f"Texte extrait via OCR ({len(text.split())} mots).",
                {
                    "title":      title,
                    "body":       text[:8000],
                    "word_count": len(text.split()),
                    "via":        "ocr",
                    "has_content": True,
                }
            )
        except Exception as e:
            return _err(f"OCR échoué : {e}")

    def extract_structured_summary(self, tab: CDPTab) -> dict:
        """
        Résumé structuré de la page :
        titre, sections, points clés, liens importants.
        Utilise Groq pour structurer le contenu extrait.
        """
        article = self.extract_article(tab)
        if not article["success"]:
            return article

        data = article.get("data", {})
        body = data.get("body", "")
        title = data.get("title", "")

        if not body:
            return _err("Aucun contenu à structurer.")

        # Résumé structuré via Groq
        if GROQ_API_KEY:
            try:
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                prompt = (
                    f"Page : '{title}'\n"
                    f"URL : {data.get('url', '')}\n\n"
                    f"Contenu :\n{body[:4000]}\n\n"
                    f"Analyse ce contenu et réponds en JSON avec ce format exact :\n"
                    f'{{"title": "...", "summary": "résumé 2-3 phrases", '
                    f'"key_points": ["point1", "point2", "point3"], '
                    f'"sections": ["section1", "section2"], '
                    f'"sentiment": "positif/neutre/négatif", '
                    f'"language": "fr/en/autre"}}\n'
                    f"Réponds UNIQUEMENT avec le JSON, sans markdown ni texte autour."
                )
                resp = client.chat.completions.create(
                    model=GROQ_MODEL_NAME,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=600,
                    temperature=0.2,
                    timeout=15,
                )
                raw = (resp.choices[0].message.content or "").strip()
                structured = json.loads(raw)
                structured["url"] = data.get("url", "")
                structured["word_count"] = data.get("word_count", 0)
                structured["via"] = data.get("via", "js")

                display = f"📄 {structured.get('title', title)}\n\n"
                display += f"📝 {structured.get('summary', '')}\n\n"
                key_points = structured.get("key_points", [])
                if key_points:
                    display += "✅ Points clés :\n"
                    display += "\n".join(f"  • {p}" for p in key_points)

                structured["display"] = display

                return _ok(
                    f"Résumé structuré de '{title[:40]}' prêt.",
                    structured
                )
            except Exception as e:
                logger.warning(f"Groq structured summary échoué : {e}")

        # Fallback : résumé simple
        lines = [l.strip() for l in body.split("\n") if len(l.strip()) > 40][:6]
        return _ok(
            f"Résumé de '{title[:40]}' (local).",
            {
                "title":      title,
                "summary":    " ".join(lines[:3]),
                "key_points": lines[3:6],
                "url":        data.get("url", ""),
                "via":        "local",
            }
        )


# ══════════════════════════════════════════════════════════════════════════════
# CLASSE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

class AutonomousBrowser:
    """
    Niveau avancé : navigation autonome, multi-étapes, intelligente.
    Utilisé par BrowserControl pour les commandes complexes.
    """

    def __init__(self, session: CDPSession, page: PageActions):
        self.session = session
        self.page = page
        self._context: dict[str, Any] = {}
        # [S6-1] AuthManager et ContentExtractor
        self.auth = AuthManager(session)
        self.content = ContentExtractor(session)

    # ── Navigation intelligente ───────────────────────────────────────────────

    def go_to_site(self, site: str, query: str = "") -> dict:
        """
        Navigue vers un site connu ou une URL.
        Si `query` est fourni, lance une recherche sur ce site.
        """
        site_lower = site.strip().lower()

        # Recherche directe par site d'abord
        if query and site_lower in SITE_SEARCH_URLS:
            url = SITE_SEARCH_URLS[site_lower].format(urllib.parse.quote_plus(query))
            tab = self._get_active_tab(launch=True)
            if isinstance(tab, dict):
                return tab
            result = self.session.navigate_tab(tab, url)
            if result["success"]:
                time.sleep(1.5)
                self._update_context(tab)
                return _ok(
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
            search_result = self.page.smart_type(tab, query, submit=True)
            if search_result["success"]:
                msg = f"Navigation vers {site.title()} et recherche '{query}' lancée."

        return _ok(msg, {"url": url, "site": site})

    def smart_search_and_open(self, query: str, engine: str = "google") -> dict:
        """
        Recherche intelligente : lance + attend + extrait les résultats.
        """
        search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
        url = search_url.format(urllib.parse.quote_plus(query))

        tab = self._get_active_tab(launch=True)
        if isinstance(tab, dict):
            return tab

        nav = self.session.navigate_tab(tab, url)
        if not nav["success"]:
            return nav

        time.sleep(1.8)
        self._update_context(tab)

        extracted = self.page.extract_search_results(tab, max_results=8)
        if not extracted["success"]:
            return _ok(
                f"Recherche '{query}' lancée sur {engine.title()}. "
                "Dis 'ouvre le premier résultat' pour continuer.",
                {"query": query, "engine": engine, "url": url},
            )

        data = extracted.get("data") or {}
        data["query"] = query
        data["engine"] = engine
        count = data.get("count", 0)

        return _ok(
            f"Recherche '{query}' : {count} résultat(s) trouvé(s). "
            "Dis 'ouvre le premier' ou 'ouvre le deuxième' pour continuer.",
            data,
        )

    def find_best_result_and_open(self, query: str) -> dict:
        """Niveau 8 : Trouve le meilleur résultat et l'ouvre automatiquement."""
        steps = []

        search = self.smart_search_and_open(query)
        steps.append({"step": 1, "action": f"Recherche '{query}'", "success": search["success"]})
        if not search["success"]:
            return _with_steps(search, steps)

        results = (search.get("data") or {}).get("results") or []
        if not results:
            return _with_steps(_err("Aucun résultat trouvé."), steps)

        best_rank = self._pick_best_result(query, results)
        steps.append({"step": 2, "action": f"Sélection résultat #{best_rank}", "success": True})

        best = self.page.open_search_result(best_rank)
        url = best.get("data", {}).get("url") or results[best_rank - 1].get("url", "")

        tab = self._get_active_tab(launch=False)
        if not isinstance(tab, dict):
            nav = self.session.navigate_tab(tab, url)
            steps.append({"step": 3, "action": f"Ouverture de {url[:50]}", "success": nav["success"]})
            if nav["success"]:
                time.sleep(1.5)
                return _with_steps(
                    _ok(
                        f"J'ai ouvert le meilleur résultat pour '{query}' (résultat #{best_rank}).",
                        {"url": url, "rank": best_rank, "results": results},
                    ),
                    steps,
                )

        return _with_steps(best, steps)

    # ══════════════════════════════════════════════════════════════════════════
    # [S6-5] MULTI-STEP AVEC RETRY INTELLIGENT
    # ══════════════════════════════════════════════════════════════════════════

    def multi_step_task(self, steps_description: list[str], max_retries: int = 2) -> dict:
        """
        Exécute une séquence de commandes navigateur en ordre.

        [S6-5] Retry automatique (max_retries) si une étape échoue.
        Détecte les blocages (captcha, paywall) avant de retenter.

        CORRECTION B5 conservée : utilise _dispatch_step() qui réutilise
        la session CDP courante (self.session, self.page).
        """
        results = []
        for i, step in enumerate(steps_description):
            logger.info(f"Multi-step navigateur {i+1}/{len(steps_description)}: {step}")
            step_result = None
            attempt = 0

            while attempt <= max_retries:
                attempt += 1
                result = self._dispatch_step(step)

                if result.get("success"):
                    step_result = {
                        "step":        i + 1,
                        "instruction": step,
                        "success":     True,
                        "message":     result.get("message", ""),
                        "attempts":    attempt,
                    }
                    break

                # Vérifier s'il y a un blocage (captcha, paywall...)
                tab = self._get_active_tab(launch=False)
                if not isinstance(tab, dict):
                    blocker = self.page.detect_blocker(tab)
                    if blocker.get("data", {}).get("blocked"):
                        step_result = {
                            "step":        i + 1,
                            "instruction": step,
                            "success":     False,
                            "message":     f"Bloqué : {blocker.get('message', '')}",
                            "blocker":     blocker.get("data", {}),
                            "attempts":    attempt,
                        }
                        logger.warning(f"Étape {i+1} bloquée (captcha/paywall) — abandon.")
                        break

                if attempt <= max_retries:
                    logger.info(f"Étape {i+1} échouée (tentative {attempt}) — retry dans 1s...")
                    time.sleep(1.0)
                else:
                    step_result = {
                        "step":        i + 1,
                        "instruction": step,
                        "success":     False,
                        "message":     result.get("message", "Échec après retries"),
                        "attempts":    attempt,
                    }

            results.append(step_result)

            if not step_result["success"]:
                logger.warning(f"Étape {i+1} définitivement échouée — arrêt de la séquence.")
                break

            time.sleep(0.8)

        ok_count = sum(1 for r in results if r["success"])
        total = len(results)
        success = ok_count == len(steps_description)

        return _ok(
            f"Séquence navigateur : {ok_count}/{total} étape(s) réussie(s).",
            {"steps": results, "ok": ok_count, "total": total, "complete": success},
        )

    # ══════════════════════════════════════════════════════════════════════════
    # [S6-3] DISPATCH STEP — 30+ patterns FR/EN
    # ══════════════════════════════════════════════════════════════════════════

    def _dispatch_step(self, natural_command: str) -> dict:
        """
        Dispatch une commande de séquence en utilisant la session CDP courante.

        [S6-3] 30+ patterns couvrant :
          - Recherche, navigation, onglets
          - Contenu (résumé, lecture, OCR)
          - Auth (login, cookies)
          - Attente d'éléments
          - Gmail/email
          - Scroll, screenshot

        CORRECTION B5 conservée : réutilise self.session et self.page.
        """
        cmd = natural_command.strip().lower()

        # ── Cookies / Auth ────────────────────────────────────────────────────
        if re.search(r"sauvegarde.*(session|cookie|connexion)", cmd) or \
           re.search(r"save.*(session|cookie|login)", cmd):
            site = self._extract_site_from_cmd(cmd)
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.auth.save_cookies(tab, site or "site")

        if re.search(r"restaure.*(session|cookie)", cmd) or \
           re.search(r"restore.*(session|cookie|login)", cmd):
            site = self._extract_site_from_cmd(cmd)
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.auth.restore_cookies(tab, site or "site")

        if re.search(r"(check|vérifie).*(connect|login|session)", cmd):
            site = self._extract_site_from_cmd(cmd)
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.auth.check_login_state(tab, site or "site")

        if re.search(r"(liste|list).*(session|cookie)", cmd):
            return self.auth.list_saved_sessions()

        # ── Accepter les cookies / popups ──────────────────────────────────
        if re.search(r"(accepte|accept|ferme|dismiss).*(cookie|banner|popup|consentement|gdpr)", cmd):
            return self._accept_cookies_popup()

        # ── Gmail / Email ──────────────────────────────────────────────────
        if re.search(r"(ouvre|open|go to).*(gmail|mail.google|inbox|boîte)", cmd):
            return self.go_to_gmail_inbox()

        if re.search(r"(ouvre|open|lis|read).*(dernier|last|premier|first|recent).*(email|mail|message)", cmd):
            return self.open_latest_email()

        if re.search(r"(réponds?|reply|répondre)", cmd) and re.search(r"(email|mail)", cmd):
            body = self._extract_quoted_text(natural_command) or ""
            return self.reply_to_email(body)

        if re.search(r"(compose|rédige|écris|new email|nouvel email|envoie)", cmd) and \
           re.search(r"(email|mail)", cmd):
            to = self._extract_email_address(natural_command)
            subject = self._extract_after(natural_command, ["objet:", "subject:", "sujet:"])
            body = self._extract_quoted_text(natural_command) or ""
            return self.compose_email(to=to, subject=subject, body=body)

        if re.search(r"cherche.*(email|mail).*(objet|subject|de:|from:)", cmd):
            subject_query = re.sub(r".*(?:objet|subject)\s*:?\s*", "", cmd).strip()
            return self.open_email_by_subject(subject_query)

        # ── Résumé / lecture ──────────────────────────────────────────────
        if re.search(r"(résumé structuré|structured summary|résume structuré)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.content.extract_structured_summary(tab)

        if re.search(r"(extrait|extract).*(article|contenu|content)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.content.extract_article(tab)

        if re.search(r"(résume|resume|résumé)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.summarize_page_ai(tab)

        if re.search(r"(lis la page|lire la page|read page|read the page)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.read_page(tab)

        # ── OCR ───────────────────────────────────────────────────────────
        if re.search(r"(ocr|reconnaissance|extrait.*texte.*image|read.*image)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.content._extract_with_ocr(tab)

        # ── Screenshot ────────────────────────────────────────────────────
        if re.search(r"(screenshot|capture.*(écran|screen)|prends une photo)", cmd):
            return self._take_screenshot()

        # ── Détection blocage ─────────────────────────────────────────────
        if re.search(r"(détect|detect).*(bloqu|block|captcha|paywall)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.detect_blocker(tab)

        # ── Attendre un élément ───────────────────────────────────────────
        wait_match = re.search(r"attends?\s+(?:que\s+)?['\"]?(.+?)['\"]?\s*(?:apparaisse|charge|soit visible|s'affiche)", cmd)
        if wait_match:
            selector_or_text = wait_match.group(1).strip()
            return self._wait_for_element(selector_or_text, timeout=10)

        # ── Scroller jusqu'à un texte ─────────────────────────────────────
        scroll_to_match = re.search(r"(scrolle|scroll|va|descends?)\s+(?:jusqu'?[àa])?\s+['\"]?(.+?)['\"]?\s*$", cmd)
        if scroll_to_match and "bas" not in cmd and "haut" not in cmd:
            target_text = scroll_to_match.group(2).strip()
            if len(target_text) > 2:
                return self._scroll_to_text(target_text)

        # ── Scroll direction ──────────────────────────────────────────────
        if re.search(r"(remonte|remonter|scroll up|haut de la page|top)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.scroll(tab, "top")

        if re.search(r"(bas de la page|bottom|scroll.*bottom)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.scroll(tab, "bottom")

        if re.search(r"(scrolle|scroll).*(haut|up)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.scroll(tab, "up")

        if re.search(r"(scrolle|scroll)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.scroll(tab, "down")

        # ── Onglets ───────────────────────────────────────────────────────
        if re.search(r"(nouvel onglet|new tab|ouvre un onglet)", cmd):
            url_match = re.search(r"(https?://\S+|[\w.-]+\.\w{2,})", natural_command)
            url = url_match.group(1) if url_match else ""
            return self.session.new_tab(url)

        if re.search(r"(ferme|close).*(onglet|tab)", cmd):
            tabs = self.session.get_tabs()
            if not tabs:
                return _err("Aucun onglet à fermer.")
            return self.session.close_tab_by_id(tabs[0].id)

        if re.search(r"(va sur|switch|bascule).*(onglet|tab)\s*(\d+)", cmd):
            num_match = re.search(r"(\d+)", cmd)
            if num_match:
                idx = int(num_match.group(1))
                tabs = self.session.get_tabs()
                if tabs and idx <= len(tabs):
                    return self.session.focus_tab(tabs[idx - 1])

        # ── Navigation ────────────────────────────────────────────────────
        if re.search(r"(recharge|actualise|refresh|reload)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.session.reload_tab(tab)

        if re.search(r"(retour|page précédente|go back|back)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.session.history_nav(tab, "back")

        if re.search(r"(suivant|page suivante|go forward|forward)", cmd) and "résultat" not in cmd:
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.session.history_nav(tab, "forward")

        # ── Recherche ─────────────────────────────────────────────────────
        if re.search(r"(cherche|recherche|google|search)", cmd):
            query = re.sub(r"^(cherche|recherche|google|search)\s+(sur\s+\w+\s+)?", "", cmd).strip()
            # Détecter le moteur
            engine = "google"
            for eng in ["bing", "duckduckgo", "youtube", "github"]:
                if eng in cmd:
                    engine = eng
                    break
            search_url = SITE_SEARCH_URLS.get(engine, SITE_SEARCH_URLS["google"])
            url = search_url.format(urllib.parse.quote_plus(query))
            tab = self._get_active_tab(launch=True)
            if isinstance(tab, dict):
                return tab
            nav = self.session.navigate_tab(tab, url)
            if nav["success"]:
                time.sleep(1.8)
                extracted = self.page.extract_search_results(tab, max_results=8)
                if extracted["success"]:
                    data = extracted.get("data") or {}
                    data["query"] = query
                    return _ok(f"Recherche '{query}' : {data.get('count', 0)} résultat(s).", data)
                return _ok(f"Recherche '{query}' lancée.", {"query": query})
            return nav

        # ── Ouvrir résultat numéroté ──────────────────────────────────────
        rank_match = re.search(
            r"ouvre\s+le\s+(premier|deuxième|troisième|quatrième|cinquième|\d+(?:er|ème|e)?)",
            cmd
        )
        if rank_match:
            rank_str = rank_match.group(1)
            rank_map = {"premier": 1, "deuxième": 2, "troisième": 3, "quatrième": 4, "cinquième": 5}
            if rank_str in rank_map:
                rank = rank_map[rank_str]
            elif re.match(r"^\d+", rank_str):
                rank = int(re.match(r"(\d+)", rank_str).group(1))
            else:
                rank = 1
            result = self.page.open_search_result(rank)
            if result["success"]:
                url = (result.get("data") or {}).get("url", "")
                if url:
                    tab = self._get_active_tab(launch=False)
                    if not isinstance(tab, dict):
                        return self.session.navigate_tab(tab, url)
            return result

        # ── Clic sur texte ────────────────────────────────────────────────
        click_match = re.search(r"(clique|click|appuie|presse)\s+(?:sur\s+)?['\"]?(.+?)['\"]?\s*$", cmd)
        if click_match:
            text_to_click = click_match.group(2).strip()
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.click_text(tab, text_to_click)

        # ── Remplir champ ─────────────────────────────────────────────────
        fill_match = re.search(
            r"(remplis?|fill|tape|écris?|write)\s+(?:le champ\s+)?['\"]?(.+?)['\"]?\s+"
            r"(?:avec|with|=)\s+['\"]?(.+?)['\"]?\s*$",
            cmd
        )
        if fill_match:
            field = fill_match.group(2).strip()
            value = fill_match.group(3).strip()
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.fill_field_by_label(tab, field, value)

        # ── Soumettre formulaire ──────────────────────────────────────────
        if re.search(r"(soumets?|submit|valide|envoie)\s+(le\s+)?(formulaire|form)", cmd):
            tab = self._get_active_tab(launch=False)
            if isinstance(tab, dict):
                return tab
            return self.page.submit_form(tab)

        # ── Navigation URL directe ────────────────────────────────────────
        url = normalize_url(natural_command.strip())
        if url.startswith("http"):
            tab = self._get_active_tab(launch=True)
            if isinstance(tab, dict):
                return tab
            return self.session.navigate_tab(tab, url)

        # ── Navigation vers site connu ────────────────────────────────────
        for site_key in SITE_MAP:
            if re.search(r'\b' + re.escape(site_key) + r'\b', cmd):
                return self.go_to_site(site_key)

        return _err(f"Commande navigateur non reconnue dans la séquence: '{natural_command}'")

    def execute_natural_command(self, natural_command: str) -> dict:
        """
        Exécute une commande navigateur unique en langage naturel.
        Point d'entrée public qui encapsule le dispatcher interne.
        """
        return self._dispatch_step(natural_command)

    # ══════════════════════════════════════════════════════════════════════════
    # [S6-4] GMAIL / EMAIL
    # ══════════════════════════════════════════════════════════════════════════

    def go_to_gmail_inbox(self) -> dict:
        """
        Navigue vers Gmail et attend le chargement de la boîte de réception.
        Tente de restaurer les cookies si disponibles.
        """
        tab = self._get_active_tab(launch=True)
        if isinstance(tab, dict):
            return tab

        # Tenter de restaurer les cookies Gmail si disponibles
        cookie_file = _COOKIES_DIR / "gmail.json"
        if cookie_file.exists():
            self.auth.restore_cookies(tab, "gmail")

        # Naviguer vers Gmail
        nav = self.session.navigate_tab(tab, "https://mail.google.com")
        if not nav["success"]:
            return nav

        # Attendre le chargement (max 8 secondes)
        time.sleep(2.0)
        for _ in range(6):
            state = self.auth.check_login_state(tab, "gmail")
            if state.get("data", {}).get("logged_in"):
                self._update_context(tab)
                return _ok(
                    "Gmail ouvert — boîte de réception chargée.",
                    {"url": "https://mail.google.com", "logged_in": True}
                )
            time.sleep(1.0)

        # Pas connecté → informer l'utilisateur
        return _ok(
            "Gmail ouvert mais tu n'es pas connecté. "
            "Connecte-toi manuellement puis dis 'sauvegarde ma session gmail' "
            "pour que Jarvis se souvienne de ta connexion.",
            {"url": "https://mail.google.com", "logged_in": False}
        )

    def open_latest_email(self) -> dict:
        """
        Ouvre le dernier email (premier de la liste) dans Gmail.
        """
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        # Vérifier qu'on est sur Gmail
        if "mail.google" not in (tab.url or ""):
            goto = self.go_to_gmail_inbox()
            if not goto["success"] or not goto.get("data", {}).get("logged_in"):
                return goto

        # JS : cliquer sur le premier email non lu (ou le premier de la liste)
        js = """
        (() => {
            // Chercher le premier email (lu ou non lu)
            const selectors = [
                'tr.zA:first-child',             // Gmail classique
                '[role="row"].zA',               // Gmail nouveau
                'div[data-message-id]',          // Gmail autre version
                'li.zA',                         // Autre variante
            ];
            for (const sel of selectors) {
                const row = document.querySelector(sel);
                if (row) {
                    const subject = row.querySelector('.y6, .bog, [data-legacy-message-id]')?.innerText?.trim()
                                 || row.querySelector('[role="gridcell"]')?.innerText?.trim()
                                 || '(sujet inconnu)';
                    row.click();
                    return {ok: true, subject};
                }
            }
            return {ok: false, reason: 'Aucun email trouvé dans la liste'};
        })()
        """
        try:
            result = self.session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                time.sleep(1.5)
                self._update_context(tab)
                return _ok(
                    f"Email ouvert : '{result.get('subject', '(sujet inconnu)')}'",
                    {"subject": result.get("subject", ""), "action": "open_latest"}
                )
            reason = result.get("reason", "inconnu") if isinstance(result, dict) else str(result)
            return _err(f"Impossible d'ouvrir le dernier email : {reason}")
        except Exception as e:
            return _err(f"open_latest_email échoué : {e}")

    def open_email_by_subject(self, subject_query: str) -> dict:
        """
        Cherche et ouvre un email par son objet dans Gmail.
        """
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        if "mail.google" not in (tab.url or ""):
            goto = self.go_to_gmail_inbox()
            if not goto["success"]:
                return goto

        # Utiliser la barre de recherche Gmail
        js_search = """
        (() => {
            const searchBox = document.querySelector('input[aria-label*="Rechercher"], input[aria-label*="Search"], #gbqfq, [role="searchbox"]');
            if (!searchBox) return {ok: false, reason: 'Barre de recherche non trouvée'};
            searchBox.focus();
            searchBox.value = %s;
            searchBox.dispatchEvent(new Event('input', {bubbles: true}));
            return {ok: true};
        })()
        """ % json.dumps(subject_query)

        try:
            result = self.session.execute_js(tab, js_search)
            if isinstance(result, dict) and result.get("ok"):
                # Appuyer sur Entrée
                js_enter = """
                (() => {
                    const searchBox = document.querySelector('input[aria-label*="Rechercher"], input[aria-label*="Search"], #gbqfq, [role="searchbox"]');
                    if (searchBox) {
                        searchBox.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
                        return true;
                    }
                    return false;
                })()
                """
                self.session.execute_js(tab, js_enter)
                time.sleep(2.0)

                # Ouvrir le premier résultat
                return self.open_latest_email()

            return _err(f"Recherche Gmail échouée : {result}")
        except Exception as e:
            return _err(f"open_email_by_subject échoué : {e}")

    def reply_to_email(self, body: str = "") -> dict:
        """
        Répond à l'email actuellement ouvert dans Gmail.
        """
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        # Cliquer sur le bouton "Répondre"
        js = """
        (() => {
            const replyBtns = document.querySelectorAll(
                '[aria-label*="Répondre"], [aria-label*="Reply"], [data-tooltip*="Reply"], button.reply'
            );
            const btn = Array.from(replyBtns).find(b => b.offsetParent !== null);
            if (!btn) return {ok: false, reason: 'Bouton Répondre non trouvé'};
            btn.click();
            return {ok: true};
        })()
        """
        try:
            result = self.session.execute_js(tab, js)
            if not (isinstance(result, dict) and result.get("ok")):
                return _err("Bouton 'Répondre' introuvable. Es-tu bien sur un email ouvert ?")

            time.sleep(1.0)

            # Si un corps de réponse est fourni, le taper
            if body:
                js_type = """
                (() => {
                    const compose = document.querySelector('[role="textbox"][aria-label*="Corps"], [contenteditable="true"].Am.Al.editable, div[aria-label*="Message"]');
                    if (!compose) return {ok: false};
                    compose.focus();
                    document.execCommand('selectAll');
                    document.execCommand('insertText', false, %s);
                    return {ok: true};
                })()
                """ % json.dumps(body)
                self.session.execute_js(tab, js_type)

            return _ok(
                "Zone de réponse ouverte" + (f" avec le texte '{body[:40]}'" if body else "") + ". "
                "Dis 'envoie' pour envoyer ou complète ta réponse manuellement.",
                {"action": "reply", "body": body}
            )
        except Exception as e:
            return _err(f"reply_to_email échoué : {e}")

    def compose_email(self, to: str = "", subject: str = "", body: str = "") -> dict:
        """
        Compose un nouvel email dans Gmail.
        """
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        if "mail.google" not in (tab.url or ""):
            goto = self.go_to_gmail_inbox()
            if not goto["success"]:
                return goto

        # Cliquer sur "Rédiger"
        js_compose = """
        (() => {
            const btn = document.querySelector('[gh="cm"], [aria-label="Nouveau message"], [data-tooltip="Rédiger"], .T-I.T-I-KE.L3');
            if (!btn) return {ok: false, reason: 'Bouton Rédiger non trouvé'};
            btn.click();
            return {ok: true};
        })()
        """
        try:
            result = self.session.execute_js(tab, js_compose)
            if not (isinstance(result, dict) and result.get("ok")):
                return _err("Bouton 'Rédiger' introuvable. Es-tu bien sur Gmail ?")

            time.sleep(1.0)

            # Remplir le destinataire
            if to:
                js_to = """
                (() => {
                    const field = document.querySelector('[name="to"], [aria-label*="À"], [aria-label*="To"]');
                    if (!field) return false;
                    field.focus();
                    field.value = %s;
                    field.dispatchEvent(new Event('input', {bubbles: true}));
                    field.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true}));
                    return true;
                })()
                """ % json.dumps(to)
                self.session.execute_js(tab, js_to)
                time.sleep(0.3)

            # Remplir l'objet
            if subject:
                js_subject = """
                (() => {
                    const field = document.querySelector('[name="subjectbox"], [aria-label*="Objet"], [aria-label*="Subject"]');
                    if (!field) return false;
                    field.focus();
                    field.value = %s;
                    field.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                })()
                """ % json.dumps(subject)
                self.session.execute_js(tab, js_subject)
                time.sleep(0.3)

            # Remplir le corps
            if body:
                js_body = """
                (() => {
                    const compose = document.querySelector('[role="textbox"][aria-label*="Corps"], div[aria-label*="Message"], .Am.Al.editable');
                    if (!compose) return false;
                    compose.focus();
                    document.execCommand('insertText', false, %s);
                    return true;
                })()
                """ % json.dumps(body)
                self.session.execute_js(tab, js_body)

            return _ok(
                f"Email rédigé{'→ ' + to if to else ''}. Dis 'envoie' pour envoyer.",
                {"to": to, "subject": subject, "body": body[:100], "action": "compose"}
            )
        except Exception as e:
            return _err(f"compose_email échoué : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # ACTIONS UTILITAIRES
    # ══════════════════════════════════════════════════════════════════════════

    def _accept_cookies_popup(self) -> dict:
        """
        Tente d'accepter automatiquement les popups de cookies / GDPR.
        """
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        js = """
        (() => {
            const keywords = [
                'accepter', 'accept', 'agree', 'd\\'accord', 'ok', 'j\\'accepte',
                'allow all', 'autoriser', 'accept all', 'tout accepter', 'continuer',
                'allow cookies', 'got it', 'i agree', 'close', 'dismiss'
            ];
            const btns = document.querySelectorAll('button, a, [role="button"]');
            for (const btn of btns) {
                const text = (btn.innerText || btn.value || btn.title || '').toLowerCase().trim();
                for (const kw of keywords) {
                    if (text === kw || text.startsWith(kw)) {
                        btn.click();
                        return {ok: true, text: btn.innerText?.trim()};
                    }
                }
            }
            // Chercher dans les iframes
            for (const iframe of document.querySelectorAll('iframe')) {
                try {
                    const doc = iframe.contentDocument;
                    if (!doc) continue;
                    const ibtn = doc.querySelector('button');
                    if (ibtn) { ibtn.click(); return {ok: true, via: 'iframe'}; }
                } catch (e) {}
            }
            return {ok: false, reason: 'Aucun bouton d\\'acceptation trouvé'};
        })()
        """
        try:
            result = self.session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return _ok(
                    f"Cookies acceptés (bouton '{result.get('text', '?')}').",
                    {"action": "accept_cookies"}
                )
            return _ok("Aucun popup de cookies trouvé.", {"action": "accept_cookies", "found": False})
        except Exception as e:
            return _err(f"accept_cookies_popup échoué : {e}")

    def _wait_for_element(self, selector_or_text: str, timeout: int = 10) -> dict:
        """
        Attend qu'un élément apparaisse sur la page (par sélecteur CSS ou texte).
        """
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        start = time.time()
        is_selector = selector_or_text.startswith(("#", ".", "[")) or " " not in selector_or_text

        while time.time() - start < timeout:
            if is_selector:
                js = f"!!document.querySelector({json.dumps(selector_or_text)})"
            else:
                js = f"document.body?.innerText?.toLowerCase().includes({json.dumps(selector_or_text.lower())})"

            try:
                found = self.session.execute_js(tab, js)
                if found:
                    return _ok(
                        f"Élément '{selector_or_text}' trouvé ({round(time.time()-start, 1)}s).",
                        {"selector": selector_or_text, "found": True}
                    )
            except Exception:
                pass
            time.sleep(0.5)

        return _err(
            f"Élément '{selector_or_text}' non trouvé après {timeout}s.",
            {"selector": selector_or_text, "found": False, "timeout": timeout}
        )

    def _scroll_to_text(self, text: str) -> dict:
        """Scrolle jusqu'au premier élément contenant le texte donné."""
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        js = """
        (() => {
            const target = %s.toLowerCase();
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                if (node.textContent.toLowerCase().includes(target)) {
                    const el = node.parentElement;
                    el?.scrollIntoView({behavior: 'smooth', block: 'center'});
                    return {ok: true, text: node.textContent.trim().slice(0, 60)};
                }
            }
            return {ok: false};
        })()
        """ % json.dumps(text)

        try:
            result = self.session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return _ok(f"Scrollé jusqu'à '{text}'.", {"text": text})
            return _err(f"Texte '{text}' non trouvé sur la page.")
        except Exception as e:
            return _err(f"_scroll_to_text échoué : {e}")

    def _take_screenshot(self, save_dir: str = "") -> dict:
        """Capture un screenshot et le sauvegarde dans Downloads."""
        tab = self._get_active_tab(launch=False)
        if isinstance(tab, dict):
            return tab

        try:
            import base64
            result = self.session.cdp_call(
                tab, "Page.captureScreenshot",
                {"format": "png", "quality": 90}
            )
            if not result["success"]:
                return result

            img_b64 = (result.get("data") or {}).get("data", "")
            if not img_b64:
                return _err("Screenshot vide.")

            save_path = Path(save_dir or Path.home() / "Downloads")
            save_path.mkdir(parents=True, exist_ok=True)
            filename = f"screenshot_{int(time.time())}.png"
            dest = save_path / filename

            dest.write_bytes(base64.b64decode(img_b64))
            return _ok(
                f"Screenshot sauvegardé : {dest}",
                {"path": str(dest), "filename": filename}
            )
        except Exception as e:
            return _err(f"Screenshot échoué : {e}")

    # ── Contexte navigateur ───────────────────────────────────────────────────

    def get_browser_context(self) -> dict:
        """
        [S6-6] Retourne l'état actuel du navigateur + état auth.
        """
        ready = self.session.ensure_session(launch_if_missing=False)
        if not ready["success"]:
            return _ok(
                "Le navigateur n'est pas ouvert en mode pilotable.",
                {"browser_open": False},
            )

        tabs = self.session.get_tabs()
        if not tabs:
            return _ok(
                "Chrome est ouvert mais aucun onglet n'est détecté.",
                {"browser_open": True, "tab_count": 0},
            )

        active = tabs[0]
        site = _extract_site_name(active.url)

        # [S6-6] Vérifier l'état de connexion si site connu
        auth_state = {}
        known_auth_sites = ["gmail", "github", "twitter", "linkedin", "facebook", "outlook"]
        if site in known_auth_sites:
            state_result = self.auth.check_login_state(active, site)
            auth_state = state_result.get("data", {})

        return _ok(
            f"Navigateur actif : {active.title or '(sans titre)'} ({site}). {len(tabs)} onglet(s) ouvert(s).",
            {
                "browser_open":  True,
                "active_title":  active.title,
                "active_url":    active.url,
                "active_site":   site,
                "tab_count":     len(tabs),
                "tabs":          [{"title": t.title, "url": t.url} for t in tabs],
                "auth":          auth_state,
                "context":       self._context,
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
        self._context["last_url"]   = tab.url
        self._context["last_title"] = tab.title
        self._context["last_site"]  = _extract_site_name(tab.url)
        self._context["last_ts"]    = time.strftime("%H:%M:%S")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_tab(self, launch: bool = False) -> CDPTab | dict:
        """Retourne l'onglet actif ou une erreur."""
        ready = self.session.ensure_session(launch_if_missing=launch)
        if not ready["success"]:
            return ready
        tabs = self.session.get_tabs()
        if not tabs:
            return _err("Aucun onglet ouvert.")
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
                    f"Résultats :\n{items}\n\n"
                    f"Quel numéro est le plus pertinent et fiable ? "
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
        return 1

    def _extract_site_from_cmd(self, cmd: str) -> str:
        """Extrait le nom du site depuis une commande."""
        for site in sorted(SITE_MAP.keys(), key=len, reverse=True):
            if site in cmd:
                return site
        # Chercher une URL
        url_match = re.search(r"(https?://)?([a-z0-9.-]+\.[a-z]{2,})", cmd)
        if url_match:
            return url_match.group(2).split(".")[0]
        return ""

    def _extract_quoted_text(self, text: str) -> str:
        """Extrait le texte entre guillemets."""
        match = re.search(r"['\"](.+?)['\"]", text)
        return match.group(1) if match else ""

    def _extract_email_address(self, text: str) -> str:
        """Extrait une adresse email."""
        match = re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", text)
        return match.group(0) if match else ""

    def _extract_after(self, text: str, markers: list[str]) -> str:
        """Extrait le texte après l'un des marqueurs."""
        for marker in markers:
            idx = text.lower().find(marker.lower())
            if idx >= 0:
                return text[idx + len(marker):].strip().strip("'\"")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS STATIQUES (module-level pour éviter la répétition)
# ══════════════════════════════════════════════════════════════════════════════

def _ok(message: str, data=None) -> dict:
    return {"success": True, "message": message, "data": data}


def _err(message: str, data=None) -> dict:
    return {"success": False, "message": message, "data": data}


def _with_steps(result: dict, steps: list) -> dict:
    r = dict(result)
    data = dict(r.get("data") or {})
    data["steps"] = steps
    r["data"] = data
    return r


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