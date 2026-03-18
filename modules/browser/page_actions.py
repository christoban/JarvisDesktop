"""
browser/page_actions.py — Lecture, interaction et analyse de pages web
=======================================================================

Responsabilités :
  - Lire le contenu texte d'une page (innerText)
  - Résumer une page via Groq (IA)
  - Extraire les résultats de recherche Google
  - Scroller (haut, bas, milieu, quantité précise)
  - Cliquer sur un élément par son texte
  - Remplir un formulaire (champ texte, sélection, checkbox)
  - Télécharger un fichier (via URL ou clic sur lien)
  - Détecter les blocages : captcha, mot de passe, protection bot
  - Extraire les liens de la page
  - Lire le titre + URL courants
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

from config.logger import get_logger
from config.settings import GROQ_API_KEY, GROQ_MODEL_NAME
from browser.cdp_session import CDPSession, CDPTab

logger = get_logger(__name__)


class PageActions:
    """
    Actions sur le contenu d'une page web.
    Nécessite une session CDP active.
    """

    def __init__(self, session: CDPSession):
        self.session = session
        self._last_search_results: list[dict[str, Any]] = []

    # ── Lecture / Analyse ─────────────────────────────────────────────────────

    def read_page(self, tab: CDPTab, max_chars: int = 6000) -> dict:
        """
        Lit le contenu texte de la page.
        Détecte également captcha et champ mot de passe.
        """
        script = """
(() => {
  const body = document.body ? document.body.innerText : '';
  const title = document.title || '';
  const url = location.href;
  const hasPwd = !!document.querySelector('input[type="password"]');
  const captchaHint = /captcha|i am not a robot|je ne suis pas un robot|verify you are human/i.test(body + title);
  const hasRateLimit = /429|too many requests|rate limit/i.test(body + title);
  const hasPaywall = /subscribe|abonnez|paywall|premium only/i.test(body);
  return {
    title, url,
    text: body.slice(0, """ + str(int(max_chars)) + """),
    hasPasswordField: hasPwd,
    hasCaptchaHint: captchaHint,
    hasRateLimit,
    hasPaywall,
    textLength: body.length
  };
})();
"""
        result = self.session.cdp_eval(tab, script)
        if not result["success"]:
            return result

        payload = result["data"] or {}
        blockers = self._detect_blockers(payload)

        data = {
            "title": payload.get("title"),
            "url": payload.get("url"),
            "text": payload.get("text", ""),
            "text_length": payload.get("textLength", 0),
            "blockers": blockers,
        }
        msg = f"Contenu de '{payload.get('title') or tab.title}' lu ({payload.get('textLength', 0)} caractères)."
        if blockers:
            data["awaiting_user_action"] = True
            blocker_msgs = " | ".join(b["message"] for b in blockers)
            msg += f" Attention : {blocker_msgs}"

        return self._ok(msg, data)

    def summarize_page_ai(self, tab: CDPTab) -> dict:
        """
        Résume la page active via Groq.
        Si Groq est indisponible, fait un résumé local (premières lignes).
        """
        read = self.read_page(tab, max_chars=8000)
        if not read["success"]:
            return read

        text = (read.get("data") or {}).get("text", "").strip()
        title = (read.get("data") or {}).get("title", "")
        url = (read.get("data") or {}).get("url", "")

        if not text:
            return self._err("Aucun contenu textuel trouvé sur cette page.")

        # Essayer résumé IA via Groq
        ai_summary = self._groq_summarize(title, url, text)
        if ai_summary:
            return self._ok(
                "Résumé IA généré.",
                {
                    "title": title,
                    "url": url,
                    "summary": ai_summary,
                    "method": "groq",
                    **(read.get("data") or {}),
                },
            )

        # Fallback local : premières lignes significatives
        lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 40][:6]
        summary = " ".join(lines)
        if len(summary) > 800:
            summary = summary[:800].rsplit(" ", 1)[0] + "..."
        if not summary:
            summary = text[:500] + ("..." if len(text) > 500 else "")

        return self._ok(
            "Résumé généré (mode local).",
            {
                "title": title,
                "url": url,
                "summary": summary,
                "method": "local",
            },
        )

    def get_page_info(self, tab: CDPTab) -> dict:
        """Retourne le titre et l'URL de l'onglet actif."""
        script = "({title: document.title, url: location.href})"
        result = self.session.cdp_eval(tab, script)
        if not result["success"]:
            return result
        data = result["data"] or {}
        return self._ok(
            f"Page : {data.get('title') or '(sans titre)'}",
            {"title": data.get("title"), "url": data.get("url")},
        )

    # ── Résultats de recherche ────────────────────────────────────────────────

    def extract_search_results(self, tab: CDPTab, max_results: int = 8) -> dict:
        """Extrait les résultats de recherche Google de la page active."""
        script = """
(() => {
  const max = %d;
  // Google classique
  let nodes = Array.from(document.querySelectorAll('a h3')).slice(0, max);
  let results = nodes.map((h3, idx) => {
    const a = h3.closest('a');
    return { rank: idx+1, title: h3.innerText.trim(), url: a ? a.href : '' };
  }).filter(r => r.title && r.url && !r.url.startsWith('https://webcache'));

  // Fallback : liens avec texte visible
  if (!results.length) {
    const links = Array.from(document.querySelectorAll('a[href]'))
      .filter(a => {
        const t = (a.innerText || '').trim();
        const h = a.href || '';
        return t.length > 10 && h.startsWith('http') && !h.includes('google.com');
      }).slice(0, max);
    results = links.map((a, idx) => ({
      rank: idx+1,
      title: a.innerText.trim().slice(0, 100),
      url: a.href
    }));
  }
  return { results, title: document.title, url: location.href };
})();
""" % int(max_results)

        call = self.session.cdp_eval(tab, script)
        if not call["success"]:
            return call

        payload = call["data"] or {}
        results = payload.get("results") or []
        if not results:
            return self._err("Aucun résultat détecté sur la page.")

        self._last_search_results = results

        lines = ["Résultats :", "─" * 70]
        for r in results:
            short_url = r["url"][:55] + "..." if len(r["url"]) > 55 else r["url"]
            lines.append(f"  {r['rank']}. {r['title']}  —  {short_url}")

        return self._ok(
            f"{len(results)} résultat(s) trouvé(s). Dis 'ouvre le premier' pour continuer.",
            {
                "results": results,
                "count": len(results),
                "display": "\n".join(lines),
                "page_title": payload.get("title"),
                "page_url": payload.get("url"),
            },
        )

    def open_search_result(self, rank: int = 1, new_tab: bool = False) -> dict:
        """Ouvre le résultat de recherche numéro `rank` mémorisé."""
        if not self._last_search_results:
            return self._err("Aucun résultat mémorisé. Lance d'abord une recherche.")

        idx = max(0, int(rank) - 1)
        if idx >= len(self._last_search_results):
            return self._err(
                f"Résultat {rank} introuvable.",
                {"available": len(self._last_search_results)},
            )

        url = self._last_search_results[idx].get("url")
        title = self._last_search_results[idx].get("title", "")
        if not url:
            return self._err("Ce résultat n'a pas d'URL exploitable.")

        return self._ok(
            f"Ouverture du résultat {rank} : '{title}'.",
            {"url": url, "title": title, "rank": rank, "new_tab": new_tab},
        )

    # ── Scroll ────────────────────────────────────────────────────────────────

    def scroll(self, tab: CDPTab, direction: str = "down", amount: int | None = None) -> dict:
        """
        Scrolle la page.
        direction : "down", "up", "top", "bottom"
        amount    : pixels (défaut: 400 pour up/down)
        """
        if direction == "top":
            script = "window.scrollTo(0, 0); true;"
            label = "Retour en haut de la page."
        elif direction == "bottom":
            script = "window.scrollTo(0, document.body.scrollHeight); true;"
            label = "Bas de la page atteint."
        elif direction == "up":
            px = int(amount or 400)
            script = f"window.scrollBy(0, -{px}); true;"
            label = f"Remonté de {px}px."
        else:  # down
            px = int(amount or 400)
            script = f"window.scrollBy(0, {px}); true;"
            label = f"Descendu de {px}px."

        result = self.session.cdp_eval(tab, script)
        if not result["success"]:
            return result
        return self._ok(label, {"direction": direction, "amount": amount})

    # ── Interaction ───────────────────────────────────────────────────────────

    def click_text(self, tab: CDPTab, text: str) -> dict:
        """Clique sur le premier élément cliquable contenant `text`."""
        target = (text or "").strip()
        if not target:
            return self._err("Texte de clic vide.")

        script = """
(() => {
  const wanted = %s;
  const norm = s => (s||'').toLowerCase().trim();
  const q = norm(wanted);
  const sel = 'a,button,[role="button"],input[type="submit"],input[type="button"],[onclick]';
  const el = Array.from(document.querySelectorAll(sel))
    .find(e => norm(e.innerText || e.value || e.getAttribute('aria-label') || '').includes(q));
  if (!el) return {clicked: false, reason: 'not_found'};
  el.scrollIntoView({behavior: 'smooth', block: 'center'});
  el.click();
  return {clicked: true, label: (el.innerText || el.value || '').trim().slice(0, 80)};
})();
""" % json.dumps(target)

        call = self.session.cdp_eval(tab, script)
        if not call["success"]:
            return call

        payload = call["data"] or {}
        if not payload.get("clicked"):
            return self._err(
                f"Aucun élément cliquable trouvé pour '{target}'.",
                {"tip": "Essaie avec le texte exact visible sur la page."},
            )
        return self._ok(
            f"Clic sur '{payload.get('label') or target}'.",
            {"clicked_label": payload.get("label")},
        )

    def fill_form_field(
        self,
        tab: CDPTab,
        selector: str,
        value: str,
        submit: bool = False,
        clear_first: bool = True,
    ) -> dict:
        """
        Remplit un champ de formulaire.
        selector : sélecteur CSS (ex: 'input[name="q"]', '#email', 'textarea')
        value    : valeur à saisir
        submit   : soumettre le formulaire après remplissage
        """
        if not selector.strip():
            return self._err("Sélecteur CSS vide.")

        script = """
(() => {
  const sel = %s;
  const val = %s;
  const doSubmit = %s;
  const clear = %s;
  const el = document.querySelector(sel);
  if (!el) return {filled: false, reason: 'not_found'};
  el.scrollIntoView({behavior: 'smooth', block: 'center'});
  el.focus();
  if (clear) { el.value = ''; el.select(); }
  el.value = val;
  el.dispatchEvent(new Event('input',  {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
  if (doSubmit) {
    if (el.form) el.form.submit();
    else el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, bubbles:true}));
  }
  return {filled: true, tag: el.tagName, type: el.type || ''};
})();
""" % (
            json.dumps(selector),
            json.dumps(value),
            "true" if submit else "false",
            "true" if clear_first else "false",
        )

        call = self.session.cdp_eval(tab, script)
        if not call["success"]:
            return call

        data = call["data"] or {}
        if not data.get("filled"):
            return self._err(
                f"Sélecteur '{selector}' introuvable sur la page.",
                {"tip": "Utilise l'inspecteur DevTools pour trouver le bon sélecteur CSS."},
            )

        msg = f"Champ rempli avec '{value}'."
        if submit:
            msg += " Formulaire soumis."
        return self._ok(msg, {"selector": selector, "value": value, "submit": submit})

    def smart_type(self, tab: CDPTab, text: str, submit: bool = False) -> dict:
        """
        Tape du texte dans le champ actif/principal de la page
        (barre de recherche, premier input visible, etc.).
        """
        script = """
(() => {
  const text = %s;
  const submit = %s;
  // Chercher le champ principal : search > text > textarea > premier input visible
  const candidates = [
    document.querySelector('input[type="search"]'),
    document.querySelector('input[type="text"]'),
    document.querySelector('textarea'),
    document.querySelector('input:not([type="hidden"]):not([type="submit"])'),
    document.activeElement,
  ];
  const el = candidates.find(e => e && e.tagName && e.offsetParent !== null);
  if (!el) return {typed: false, reason: 'no_input_found'};
  el.focus();
  el.value = text;
  el.dispatchEvent(new Event('input',  {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  if (submit) {
    if (el.form) el.form.submit();
    else el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, bubbles:true}));
  }
  return {typed: true, fieldType: el.type || el.tagName};
})();
""" % (json.dumps(text), "true" if submit else "false")

        call = self.session.cdp_eval(tab, script)
        if not call["success"]:
            return call

        data = call["data"] or {}
        if not data.get("typed"):
            return self._err(
                "Aucun champ de saisie trouvé sur la page.",
                {"tip": "Utilise fill_form_field avec un sélecteur précis."},
            )
        msg = f"Texte '{text}' saisi."
        if submit:
            msg += " Envoyé."
        return self._ok(msg, {"text": text, "field_type": data.get("fieldType")})

    # ── Liens et téléchargements ──────────────────────────────────────────────

    def extract_links(self, tab: CDPTab, max_links: int = 20) -> dict:
        """Extrait tous les liens de la page."""
        script = """
(() => {
  const max = %d;
  return Array.from(document.querySelectorAll('a[href]'))
    .filter(a => a.href && a.href.startsWith('http') && a.innerText.trim())
    .slice(0, max)
    .map((a, i) => ({index: i+1, text: a.innerText.trim().slice(0,80), url: a.href}));
})();
""" % int(max_links)

        call = self.session.cdp_eval(tab, script)
        if not call["success"]:
            return call

        links = call["data"] or []
        if not links:
            return self._err("Aucun lien trouvé sur la page.")

        lines = ["Liens de la page :", "─" * 60]
        for lk in links:
            lines.append(f"  {lk['index']}. {lk['text']}  —  {lk['url'][:50]}")

        return self._ok(
            f"{len(links)} lien(s) trouvé(s).",
            {"links": links, "count": len(links), "display": "\n".join(lines)},
        )

    def trigger_download(self, tab: CDPTab, url: str) -> dict:
        """Déclenche un téléchargement via CDP Page.setDownloadBehavior + navigation."""
        import os
        download_dir = str(
            __import__("pathlib").Path.home() / "Downloads"
        )
        # Autoriser les téléchargements CDP
        allow = self.session.cdp_call(tab, "Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": download_dir,
        })
        if not allow["success"]:
            # Fallback : ouvrir l'URL dans un nouvel onglet (le navigateur télécharge)
            return self.session.new_tab(url)

        nav = self.session.navigate_tab(tab, url)
        if not nav["success"]:
            return nav

        return self._ok(
            f"Téléchargement lancé vers {download_dir}.",
            {"url": url, "download_dir": download_dir},
        )

    def download_by_link_text(self, tab: CDPTab, text: str) -> dict:
        """Trouve un lien par son texte et déclenche son téléchargement."""
        script = """
(() => {
  const q = %s.toLowerCase().trim();
  const a = Array.from(document.querySelectorAll('a[href]'))
    .find(el => el.innerText.toLowerCase().includes(q) || el.href.toLowerCase().includes(q));
  return a ? {found: true, url: a.href, label: a.innerText.trim()} : {found: false};
})();
""" % json.dumps((text or "").strip())

        call = self.session.cdp_eval(tab, script)
        if not call["success"]:
            return call

        data = call["data"] or {}
        if not data.get("found"):
            return self._err(f"Lien '{text}' introuvable sur la page.")

        return self.trigger_download(tab, data["url"])

    # ── Détection de blocages ─────────────────────────────────────────────────

    @staticmethod
    def _detect_blockers(payload: dict) -> list[dict]:
        blockers = []
        if payload.get("hasCaptchaHint"):
            blockers.append({
                "type": "captcha",
                "message": "Un captcha est détecté. Résous-le manuellement, puis dis 'continue'.",
            })
        if payload.get("hasPasswordField"):
            blockers.append({
                "type": "password",
                "message": "Un champ mot de passe est présent. Saisis-le manuellement.",
            })
        if payload.get("hasRateLimit"):
            blockers.append({
                "type": "rate_limit",
                "message": "Le site signale un accès trop fréquent (429). Attends quelques secondes.",
            })
        if payload.get("hasPaywall"):
            blockers.append({
                "type": "paywall",
                "message": "Du contenu payant a été détecté sur cette page.",
            })
        return blockers

    # ── Résumé IA via Groq ────────────────────────────────────────────────────

    def _groq_summarize(self, title: str, url: str, text: str) -> str | None:
        if not GROQ_API_KEY:
            return None
        try:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            prompt = (
                f"Page web : '{title}' ({url})\n\n"
                f"Contenu (extrait) :\n{text[:4000]}\n\n"
                f"Donne un résumé clair et concis en 3-5 phrases en français. "
                f"Commence directement par le contenu, sans phrase d'intro."
            )
            resp = client.chat.completions.create(
                model=GROQ_MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                temperature=0.4,
                timeout=10,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning(f"Groq summarize failed: {e}")
            return None

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}