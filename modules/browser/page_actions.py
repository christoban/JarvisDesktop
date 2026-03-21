"""
modules/browser/page_actions.py — Actions sur les pages web
===========================================================
Semaine 5 — Améliorations :

  [S5-A] Extraction résultats Google améliorée :
          - Parsing plus robuste (Google, Bing, DuckDuckGo)
          - Retourne titre + URL + description courte
          - Stockage partagé via CDPSession (correction B14)

  [S5-B] Remplissage de formulaires :
          - fill_field_by_selector() : par sélecteur CSS
          - fill_field_by_label()    : par texte du label
          - submit_form()            : soumettre le formulaire actif

  [S5-C] Téléchargement de fichiers :
          - Déclencher un clic de téléchargement via CDP
          - Attendre et détecter le fichier téléchargé

  [S5-D] Correction B14 :
          - _last_search_results maintenant stocké dans CDPSession
            (survit à la réinstanciation de PageActions)

  [S5-E] Lecture de page améliorée :
          - read_page() : texte brut nettoyé + extraction BeautifulSoup si disponible
          - summarize_page_ai() : résumé Groq avec chunking si contenu long

Toutes les méthodes retournent {success, message, data}.
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

from config.logger import get_logger
from config.settings import GROQ_API_KEY, GROQ_MODEL_NAME
from modules.browser.cdp_session import CDPSession, CDPTab

logger = get_logger(__name__)

# Longueur max du texte envoyé à Groq pour le résumé
_MAX_TEXT_FOR_GROQ = 4000


class PageActions:
    """
    Actions bas niveau sur les pages Chrome via CDP.
    """

    def __init__(self, session: CDPSession):
        self._session = session
        # [B14 corrigé] : _last_search_results maintenant dans CDPSession
        # pour survivre à la réinstanciation de PageActions

    # ── Propriété partagée via CDPSession (correction B14) ────────────────────
    @property
    def _last_search_results(self) -> list:
        return getattr(self._session, "_shared_search_results", [])

    @_last_search_results.setter
    def _last_search_results(self, value: list):
        self._session._shared_search_results = value

    # ══════════════════════════════════════════════════════════════════════════
    #  [S5-A] EXTRACTION RÉSULTATS DE RECHERCHE AMÉLIORÉE
    # ══════════════════════════════════════════════════════════════════════════

    def extract_search_results(
        self,
        tab: CDPTab,
        max_results: int = 8,
    ) -> dict:
        """
        Extrait les résultats de recherche depuis Google, Bing ou DuckDuckGo.
        [S5-A] Version améliorée : détection automatique du moteur + extraction robuste.

        Retourne {results: [{rank, title, url, description}], count, engine}
        """
        if not tab:
            return self._err("Aucun onglet fourni.")

        url = (tab.url or "").lower()

        # Détecter le moteur de recherche
        if "google.com/search" in url:
            return self._extract_google_results(tab, max_results)
        elif "bing.com/search" in url:
            return self._extract_bing_results(tab, max_results)
        elif "duckduckgo.com" in url and "?q=" in url:
            return self._extract_ddg_results(tab, max_results)
        elif "youtube.com/results" in url:
            return self._extract_youtube_results(tab, max_results)
        else:
            # Tenter extraction générique
            return self._extract_generic_results(tab, max_results)

    def _extract_google_results(self, tab: CDPTab, max_results: int) -> dict:
        """Extrait les résultats depuis Google Search."""
        js = """
        (() => {
            const results = [];
            // Sélecteurs Google (multiples versions du DOM)
            const containers = document.querySelectorAll(
                'div.g, div[data-sokoban-container], div[jscontroller] h3, div.tF2Cxc'
            );

            let rank = 1;
            for (const el of containers) {
                if (rank > %d) break;

                // Titre
                const titleEl = el.querySelector('h3') || el.closest('[data-sokoban-container]')?.querySelector('h3');
                const title = titleEl?.innerText?.trim() || '';
                if (!title) continue;

                // URL
                const linkEl = el.querySelector('a[href]') || el.closest('a[href]');
                let url = linkEl?.href || '';
                // Nettoyer les URLs Google trackées
                if (url.startsWith('/url?q=')) {
                    url = decodeURIComponent(url.split('/url?q=')[1].split('&')[0]);
                }
                if (!url.startsWith('http')) continue;

                // Description
                const descEl = el.querySelector('.VwiC3b, .yXK7lf, div[data-sncf], span.aCOpRe');
                const description = descEl?.innerText?.trim()?.slice(0, 200) || '';

                results.push({rank, title, url, description});
                rank++;
            }
            return results;
        })()
        """ % max_results

        return self._run_js_and_parse_results(tab, js, "google")

    def _extract_bing_results(self, tab: CDPTab, max_results: int) -> dict:
        """Extrait les résultats depuis Bing."""
        js = """
        (() => {
            const results = [];
            const items = document.querySelectorAll('#b_results .b_algo');
            let rank = 1;
            for (const item of items) {
                if (rank > %d) break;
                const h2 = item.querySelector('h2 a');
                if (!h2) continue;
                const title = h2.innerText.trim();
                const url = h2.href || '';
                const desc = item.querySelector('.b_caption p')?.innerText?.trim()?.slice(0,200) || '';
                if (title && url.startsWith('http')) {
                    results.push({rank, title, url, description: desc});
                    rank++;
                }
            }
            return results;
        })()
        """ % max_results

        return self._run_js_and_parse_results(tab, js, "bing")

    def _extract_ddg_results(self, tab: CDPTab, max_results: int) -> dict:
        """Extrait les résultats depuis DuckDuckGo."""
        js = """
        (() => {
            const results = [];
            const items = document.querySelectorAll('[data-result="web"] article, .result__body');
            let rank = 1;
            for (const item of items) {
                if (rank > %d) break;
                const titleEl = item.querySelector('a[data-testid="result-title-a"], h2 a, .result__a');
                const descEl  = item.querySelector('[data-result="snippet"], .result__snippet');
                const title = titleEl?.innerText?.trim() || '';
                const url   = titleEl?.href || '';
                const desc  = descEl?.innerText?.trim()?.slice(0, 200) || '';
                if (title && url.startsWith('http')) {
                    results.push({rank, title, url, description: desc});
                    rank++;
                }
            }
            return results;
        })()
        """ % max_results

        return self._run_js_and_parse_results(tab, js, "duckduckgo")

    def _extract_youtube_results(self, tab: CDPTab, max_results: int) -> dict:
        """Extrait les résultats depuis YouTube."""
        js = """
        (() => {
            const results = [];
            const items = document.querySelectorAll('ytd-video-renderer, ytd-compact-video-renderer');
            let rank = 1;
            for (const item of items) {
                if (rank > %d) break;
                const titleEl = item.querySelector('#video-title, a#thumbnail');
                const title = titleEl?.innerText?.trim() || titleEl?.title || '';
                const href  = titleEl?.href || item.querySelector('a')?.href || '';
                const url = href.startsWith('/') ? 'https://www.youtube.com' + href : href;
                const desc = item.querySelector('#description-text')?.innerText?.trim()?.slice(0,150) || '';
                if (title && url.includes('youtube.com')) {
                    results.push({rank, title, url, description: desc});
                    rank++;
                }
            }
            return results;
        })()
        """ % max_results

        return self._run_js_and_parse_results(tab, js, "youtube")

    def _extract_generic_results(self, tab: CDPTab, max_results: int) -> dict:
        """Extraction générique — tente de trouver des liens pertinents."""
        js = """
        (() => {
            const seen = new Set();
            const results = [];
            const links = document.querySelectorAll('a[href]');
            let rank = 1;
            for (const link of links) {
                if (rank > %d) break;
                const url = link.href;
                const title = link.innerText.trim() || link.title || '';
                if (!url.startsWith('http') || seen.has(url) || title.length < 5) continue;
                if (url.includes('google.com') || url.includes('javascript:')) continue;
                seen.add(url);
                results.push({rank, title: title.slice(0, 100), url, description: ''});
                rank++;
            }
            return results;
        })()
        """ % max_results

        return self._run_js_and_parse_results(tab, js, "generic")

    def _run_js_and_parse_results(self, tab: CDPTab, js: str, engine: str) -> dict:
        """Exécute le JS d'extraction et parse les résultats."""
        try:
            raw = self._session.execute_js(tab, js)
            results = []

            if isinstance(raw, list):
                results = raw
            elif isinstance(raw, str) and raw.strip().startswith("["):
                results = json.loads(raw)

            if not results:
                return self._err(
                    f"Aucun résultat extrait depuis {engine}. "
                    "La page est peut-être encore en chargement."
                )

            # Stocker pour open_search_result
            self._last_search_results = results

            return self._ok(
                f"{len(results)} résultat(s) extrait(s).",
                {"results": results, "count": len(results), "engine": engine}
            )
        except Exception as e:
            logger.error(f"Extraction résultats {engine} échouée : {e}")
            return self._err(f"Extraction résultats échouée : {e}")

    def open_search_result(self, rank: int = 1) -> dict:
        """
        Retourne l'URL du Nième résultat de la dernière recherche.
        [B14 corrigé] : utilise _last_search_results stocké dans CDPSession.
        """
        results = self._last_search_results
        if not results:
            return self._err(
                "Aucun résultat de recherche mémorisé. "
                "Lance d'abord une recherche avec 'cherche X'."
            )

        target = next((r for r in results if r.get("rank") == rank), None)
        if not target:
            if rank <= len(results):
                target = results[rank - 1]
            else:
                return self._err(
                    f"Résultat n°{rank} inexistant (il y a {len(results)} résultat(s))."
                )

        url = target.get("url", "")
        title = target.get("title", "")
        return self._ok(
            f"Résultat n°{rank} : {title[:50]}",
            {"rank": rank, "url": url, "title": title}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  [S5-B] REMPLISSAGE DE FORMULAIRES
    # ══════════════════════════════════════════════════════════════════════════

    def fill_field_by_selector(self, tab: CDPTab, selector: str, value: str) -> dict:
        """
        Remplit un champ de formulaire par sélecteur CSS.

        Exemple : fill_field_by_selector(tab, "#search", "Python tutorial")
        """
        js = """
        (() => {
            const el = document.querySelector(%s);
            if (!el) return {ok: false, reason: 'Sélecteur non trouvé'};
            el.focus();
            el.value = %s;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, tag: el.tagName, id: el.id, name: el.name};
        })()
        """ % (json.dumps(selector), json.dumps(value))

        try:
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return self._ok(
                    f"Champ '{selector}' rempli avec '{value[:30]}'.",
                    {"selector": selector, "value": value}
                )
            reason = result.get("reason", "inconnu") if isinstance(result, dict) else str(result)
            return self._err(f"Champ '{selector}' non trouvé : {reason}")
        except Exception as e:
            return self._err(f"fill_field_by_selector échoué : {e}")

    def fill_field_by_label(self, tab: CDPTab, label_text: str, value: str) -> dict:
        """
        Remplit un champ de formulaire par le texte de son label.

        Exemple : fill_field_by_label(tab, "Nom", "Christian")
        """
        js = """
        (() => {
            const labelText = %s.toLowerCase();
            const labels = document.querySelectorAll('label, [placeholder]');

            // Chercher par label
            for (const label of labels) {
                if (label.tagName === 'LABEL' && label.innerText.toLowerCase().includes(labelText)) {
                    const forAttr = label.getAttribute('for');
                    const field = forAttr
                        ? document.getElementById(forAttr)
                        : label.querySelector('input, textarea, select');
                    if (field) {
                        field.focus();
                        field.value = %s;
                        field.dispatchEvent(new Event('input', {bubbles: true}));
                        field.dispatchEvent(new Event('change', {bubbles: true}));
                        return {ok: true, tag: field.tagName};
                    }
                }
                // Chercher par placeholder
                if (label.placeholder && label.placeholder.toLowerCase().includes(labelText)) {
                    label.focus();
                    label.value = %s;
                    label.dispatchEvent(new Event('input', {bubbles: true}));
                    label.dispatchEvent(new Event('change', {bubbles: true}));
                    return {ok: true, tag: label.tagName, via: 'placeholder'};
                }
            }
            return {ok: false, reason: 'Label non trouvé'};
        })()
        """ % (json.dumps(label_text), json.dumps(value), json.dumps(value))

        try:
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return self._ok(
                    f"Champ '{label_text}' rempli avec '{value[:30]}'.",
                    {"label": label_text, "value": value}
                )
            return self._err(f"Champ avec label '{label_text}' non trouvé.")
        except Exception as e:
            return self._err(f"fill_field_by_label échoué : {e}")

    def submit_form(self, tab: CDPTab, selector: str = "form") -> dict:
        """Soumet le formulaire actif ou celui correspondant au sélecteur."""
        js = """
        (() => {
            const form = document.querySelector(%s);
            if (!form) {
                // Essayer de cliquer le bouton submit
                const btn = document.querySelector('button[type="submit"], input[type="submit"], button:last-child');
                if (btn) { btn.click(); return {ok: true, via: 'button'}; }
                return {ok: false, reason: 'Formulaire et bouton submit non trouvés'};
            }
            form.submit();
            return {ok: true, via: 'form.submit()'};
        })()
        """ % json.dumps(selector)

        try:
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return self._ok("Formulaire soumis.", {"via": result.get("via")})
            return self._err("Impossible de soumettre le formulaire.")
        except Exception as e:
            return self._err(f"submit_form échoué : {e}")

    def smart_type(self, tab: CDPTab, text: str, submit: bool = False) -> dict:
        """
        Tape du texte dans le champ actif de la page.
        Si submit=True, appuie sur Entrée après.
        """
        js = """
        (() => {
            // Chercher le champ actif ou le premier champ visible
            let el = document.activeElement;
            if (!el || el.tagName === 'BODY') {
                el = document.querySelector(
                    'input[type="text"]:not([hidden]), input[type="search"]:not([hidden]), ' +
                    'textarea:not([hidden]), input:not([type="hidden"]):not([type="submit"])'
                );
            }
            if (!el) return {ok: false, reason: 'Aucun champ actif'};
            el.focus();
            el.value = %s;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            %s
            return {ok: true, tag: el.tagName, name: el.name || el.id};
        })()
        """ % (
            json.dumps(text),
            "el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));" if submit else ""
        )

        try:
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return self._ok(
                    f"Texte saisi{' + Entrée' if submit else ''} : '{text[:40]}'.",
                    {"text": text, "submitted": submit}
                )
            return self._err(f"Impossible de taper le texte : {result}")
        except Exception as e:
            return self._err(f"smart_type échoué : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  INTERACTIONS DE BASE
    # ══════════════════════════════════════════════════════════════════════════

    def click_text(self, tab: CDPTab, text: str) -> dict:
        """Clique sur un élément par son texte visible."""
        js = """
        (() => {
            const target = %s.toLowerCase();
            const all = document.querySelectorAll('a, button, [role="button"], [onclick], input[type="submit"]');
            for (const el of all) {
                const t = (el.innerText || el.value || el.title || '').toLowerCase().trim();
                if (t === target || t.includes(target)) {
                    el.click();
                    return {ok: true, tag: el.tagName, text: el.innerText?.slice(0,50)};
                }
            }
            return {ok: false, reason: `Élément '${target}' non trouvé`};
        })()
        """ % json.dumps(text)

        try:
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict) and result.get("ok"):
                return self._ok(
                    f"Clic sur '{text}'.",
                    {"text": text, "tag": result.get("tag")}
                )
            return self._err(f"Élément '{text}' non trouvé sur la page.")
        except Exception as e:
            return self._err(f"click_text échoué : {e}")

    def scroll(self, tab: CDPTab, direction: str = "down") -> dict:
        """Faire défiler la page."""
        direction = direction.lower().strip()
        scroll_map = {
            "down":   "window.scrollBy(0, window.innerHeight * 0.7);",
            "up":     "window.scrollBy(0, -window.innerHeight * 0.7);",
            "top":    "window.scrollTo(0, 0);",
            "bottom": "window.scrollTo(0, document.body.scrollHeight);",
        }
        js = scroll_map.get(direction, scroll_map["down"])
        try:
            self._session.execute_js(tab, js)
            return self._ok(f"Scroll {direction}.", {"direction": direction})
        except Exception as e:
            return self._err(f"Scroll échoué : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  [S5-E] LECTURE ET RÉSUMÉ DE PAGE AMÉLIORÉS
    # ══════════════════════════════════════════════════════════════════════════

    def read_page(self, tab: CDPTab) -> dict:
        """
        Extrait le texte lisible de la page active.
        [S5-E] Version améliorée : nettoyage poussé + tentative BeautifulSoup.
        """
        js = """
        (() => {
            // Supprimer les éléments inutiles
            const remove = ['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe'];
            remove.forEach(tag => {
                document.querySelectorAll(tag).forEach(el => el.remove());
            });

            // Extraire le texte du contenu principal
            const mainEl = document.querySelector('main, article, [role="main"], #content, .content, .post-content');
            const el = mainEl || document.body;
            const text = el.innerText || el.textContent || '';

            // Nettoyer
            const clean = text
                .replace(/[\\r\\n]{3,}/g, '\\n\\n')
                .replace(/[ \\t]{2,}/g, ' ')
                .trim()
                .slice(0, 5000);

            return {
                text: clean,
                length: clean.length,
                title: document.title,
                url: window.location.href,
            };
        })()
        """
        try:
            result = self._session.execute_js(tab, js)
            if not isinstance(result, dict):
                return self._err("Impossible de lire le contenu de la page.")

            text = result.get("text", "")
            title = result.get("title", "")
            url = result.get("url", "")

            if not text.strip():
                return self._err("Page vide ou contenu non extrait.")

            return self._ok(
                f"Page lue : '{title[:50]}' ({len(text)} caractères).",
                {
                    "text":   text,
                    "title":  title,
                    "url":    url,
                    "length": len(text),
                    "display": text[:800] + ("..." if len(text) > 800 else ""),
                }
            )
        except Exception as e:
            return self._err(f"read_page échoué : {e}")

    def summarize_page_ai(self, tab: CDPTab) -> dict:
        """
        Résume la page active via Groq IA.
        [S5-E] Chunking si contenu long + fallback résumé local.
        """
        # D'abord, extraire le texte
        page = self.read_page(tab)
        if not page["success"]:
            return page

        data = page.get("data", {})
        text = data.get("text", "")
        title = data.get("title", "")
        url = data.get("url", "")

        if not text.strip():
            return self._err("Aucun contenu à résumer.")

        # Tronquer si trop long pour Groq
        text_for_groq = text[:_MAX_TEXT_FOR_GROQ]

        # Résumé via Groq
        if GROQ_API_KEY:
            try:
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                prompt = (
                    f"Tu es un assistant qui résume des pages web en français.\n"
                    f"Page : '{title}'\n"
                    f"URL : {url}\n\n"
                    f"Contenu :\n{text_for_groq}\n\n"
                    f"Fais un résumé clair en 3-5 phrases. "
                    f"Mets en avant les informations les plus importantes."
                )
                response = client.chat.completions.create(
                    model=GROQ_MODEL_NAME,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.3,
                    timeout=15,
                )
                summary = response.choices[0].message.content.strip()
                return self._ok(
                    f"Résumé de '{title[:40]}' :",
                    {
                        "summary": summary,
                        "title":   title,
                        "url":     url,
                        "display": f"📄 {title}\n\n{summary}",
                    }
                )
            except Exception as e:
                logger.warning(f"Groq résumé échoué : {e} — fallback local")

        # Fallback : résumé local (premières lignes)
        lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 30][:5]
        local_summary = " ".join(lines)[:600]
        return self._ok(
            f"Résumé (local) de '{title[:40]}' :",
            {
                "summary": local_summary,
                "title":   title,
                "url":     url,
                "display": f"📄 {title}\n\n{local_summary}",
                "fallback": True,
            }
        )

    def extract_links(self, tab: CDPTab, max_links: int = 20) -> dict:
        """Extrait les liens de la page active."""
        js = """
        (() => {
            const seen = new Set();
            const links = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const url = a.href;
                const text = a.innerText.trim() || a.title || '';
                if (url.startsWith('http') && !seen.has(url) && text) {
                    seen.add(url);
                    links.push({text: text.slice(0, 80), url});
                }
            });
            return links.slice(0, %d);
        })()
        """ % max_links

        try:
            links = self._session.execute_js(tab, js)
            if not isinstance(links, list):
                return self._err("Impossible d'extraire les liens.")

            display_lines = [f"{i+1}. {l['text'][:50]}" for i, l in enumerate(links[:10])]
            return self._ok(
                f"{len(links)} lien(s) trouvé(s).",
                {"links": links, "count": len(links), "display": "\n".join(display_lines)}
            )
        except Exception as e:
            return self._err(f"extract_links échoué : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  DÉTECTION BLOCAGES (captcha, paywall, rate-limit)
    # ══════════════════════════════════════════════════════════════════════════

    def detect_blocker(self, tab: CDPTab) -> dict:
        """Détecte si la page est bloquée (captcha, paywall, login requis)."""
        js = """
        (() => {
            const text = document.body?.innerText?.toLowerCase() || '';
            const url = window.location.href.toLowerCase();

            const captcha = text.includes('captcha') || text.includes('robot') ||
                            text.includes("i'm not a robot") || document.querySelector('.g-recaptcha');
            const paywall = text.includes('subscribe') || text.includes('abonnez') ||
                            text.includes('premium') || text.includes('paid content');
            const rateLimit = text.includes('too many requests') || text.includes('rate limit') ||
                              text.includes('429');
            const login = text.includes('sign in') || text.includes('connectez-vous') ||
                          text.includes('log in') || document.querySelector('form[action*="login"]');

            return {captcha, paywall, rate_limit: rateLimit, login_required: login,
                    blocked: captcha || rateLimit};
        })()
        """
        try:
            result = self._session.execute_js(tab, js)
            if isinstance(result, dict):
                blocked = result.get("blocked", False)
                issues = [k for k, v in result.items() if v and k != "blocked"]
                msg = f"Blocage détecté : {', '.join(issues)}." if blocked else "Page accessible."
                return self._ok(msg, result)
            return self._ok("Aucun blocage détecté.", {})
        except Exception as e:
            return self._err(f"detect_blocker échoué : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}