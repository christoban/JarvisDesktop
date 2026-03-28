"""
core/router.py — Smart Routing Engine (TONY STARK LEVEL 2 + 4)
==============================================================

Architecture intelligente multi-couches :
1. Correction Detection (contexte)      → 0 tokens
2. Fast Intent (regex + patterns)       → 0 tokens
3. Keyword Matching (fuzzy)             → 0 tokens
4. Semantic Match                       → 0 tokens
5. LLM Fallback (optimisé)              → ~300 tokens

Le secret : 90% des commandes ne vont jamais jusqu'à Groq.
+ contexte conversationnel pour les corrections/modifications.
"""

import re
from dataclasses import dataclass
from difflib import get_close_matches
from config.logger import get_logger
from core.context_memory import get_context_memory
from core.parameter_parser import get_parameter_parser
from core.intent_validator import get_intent_validator

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RouterResult:
    """Résultat du routage intelligent"""
    intent: str
    params: dict
    confidence: float
    source: str  # "fast_intent" | "fuzzy" | "llm"


# ═══════════════════════════════════════════════════════════════════════════════
#  FAST INTENT PATTERNS (0 TOKEN)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Sites web connus (pour BROWSER_GO_TO_SITE) ───────────────────────────
_KNOWN_SITES = (
    "youtube|github|google|gmail|bing|wikipedia|reddit|stackoverflow|"
    "amazon|duckduckgo|twitter|linkedin|facebook|instagram|netflix|"
    "notion|discord|twitch|outlook|whatsapp|chatgpt|claude"
)

FAST_INTENTS = {
    # ══════════════════════════════════════════════════════════════════════
    #  NAVIGATEUR — patterns spécifiques AVANT les patterns génériques
    #  Corrige le bug : "ouvre un nouvel onglet" capturé par APP_OPEN
    # ══════════════════════════════════════════════════════════════════════
    "BROWSER_NEW_TAB": {
        "patterns": [
            # "ouvre un nouvel onglet", "ouvre onglet", "ouvre juste un onglet"
            (r"(?:ouvre|ouvrir|lance)\s+(?:(?:un|juste|encore|moi)\s+)*(?:nouvel?\s+)?onglet(?:\s+(?P<url>.+))?", {"url": "{url}"}),
            # "nouvel onglet", "nouveau onglet"
            (r"nouvel?\s+onglet", {}),
            (r"new\s+tab", {}),
        ],
        "keywords": ["nouvel onglet", "new tab", "ouvre un onglet"],
    },
    "BROWSER_CLOSE_TAB": {
        "patterns": [
            (r"(?:ferme|referme)\s+(?:l'|l |cet\s+)?onglet", {}),
            (r"close\s+(?:the\s+)?tab", {}),
        ],
        "keywords": ["ferme l'onglet", "ferme cet onglet", "close tab"],
    },
    "BROWSER_GO_TO_SITE": {
        "patterns": [
            # "ouvre youtube", "va sur github", "lance gmail"
            (r"(?:ouvre|lance|va\s+sur|visite)\s+(?P<site>" + _KNOWN_SITES + r")\b", {"site": "{site}"}),
        ],
        "keywords": [],
    },
    "BROWSER_SEARCH": {
        "patterns": [
            # "cherche X sur google", "recherche X sur internet"
            (r"(?:cherche|recherche)\s+(?P<query>.+?)\s+sur\s+(?:le\s+)?(?:web|internet|google|bing)", {"query": "{query}"}),
        ],
        "keywords": [],
    },

    # ══════════════════════════════════════════════════════════════════════
    #  APPLICATIONS — avec exclusion des termes navigateur/musique
    # ══════════════════════════════════════════════════════════════════════
    "APP_OPEN": {
        "patterns": [
            # Exclut : onglet, musique/chanson/playlist, sites web connus
            (r"(?:ouvre|lance|démarre|mets)\s+"
             r"(?!(?:(?:un|juste|encore|moi)\s+)*(?:nouvel?\s+)?onglet"
             r"|(?:de\s+la\s+)?(?:musique|chanson)"
             r"|(?:la\s+|ma\s+)?playlist"
             r"|(?:en\s+)?pause"
             r"|(?:" + _KNOWN_SITES + r")\b"
             r")(?P<app>.+)",
             {"app_name": "{app}"}),
        ],
        "keywords": ["ouvre", "lance", "démarre"],
    },
    "APP_CLOSE": {
        "patterns": [
            # "ferme spotify", "quitte discord" — exclut onglet/fenêtre/navigateur
            (r"(?:ferme|quitte|close|arrête)\s+"
             r"(?!(?:l'|l |cet\s+)?onglet"
             r"|(?:le\s+)?navigateur"
             r"|(?:la\s+|cette\s+)?(?:fenêtre|fenetre)"
             r"|ça|ca"
             r")(?P<app>.+)",
             {"app_name": "{app}"}),
        ],
        "keywords": [],
    },

    # FICHIERS
    "FILE_OPEN": {
        "patterns": [
            (r"ouvre.*fichier\s+(?P<file>.+)", {"path": "{file}"}),
            (r"ouvre\s+(?P<file>.+\.(?:txt|pdf|doc|docx))", {"path": "{file}"}),
        ],
        "keywords": ["fichier", "document", "pdf"],
    },

    # VOLUME/SON
    "AUDIO_VOLUME_SET": {
        "patterns": [
            (r"(?:mets?|volume)\s+(?:le\s+)?volume\s+(?:à|a)\s+(?P<level>\d+)", {"level": "{level}"}),
        ],
        "keywords": ["volume"],
    },

    "AUDIO_VOLUME_UP": {
        "patterns": [
            (r"(?:monte|augmente).*volume", {}),
        ],
        "keywords": ["monte le son", "augmente volume"],
    },

    "AUDIO_VOLUME_DOWN": {
        "patterns": [
            (r"(?:baisse|diminue|baisse).*volume", {}),
        ],
        "keywords": ["baisse le son", "diminue volume"],
    },

    "AUDIO_MUTE": {
        "patterns": [
            (r"(?:coupe|mute).*son", {}),
        ],
        "keywords": ["coupe le son", "mute"],
    },

    # MUSIQUE
    "MUSIC_PLAY": {
        "patterns": [
            (r"(?:joue|mets|lance)\s+(?P<music>.+)", {"query": "{music}"}),
        ],
        "keywords": ["joue", "musique", "chanson"],
    },

    "MUSIC_PAUSE": {
        "patterns": [
            (r"(?:pause|mets\s+en\s+pause)", {}),
        ],
        "keywords": ["pause"],
    },

    "MUSIC_NEXT": {
        "patterns": [
            (r"(?:suivant|suivante|next)", {}),
        ],
        "keywords": ["suivant", "suivante"],
    },

    # ÉCRAN
    "SCREEN_BRIGHTNESS": {
        "patterns": [
            (r"luminosité\s+(?:à|a)\s+(?P<level>\d+)", {"level": "{level}"}),
            (r"brightness\s+(?P<level>\d+)", {"level": "{level}"}),
        ],
        "keywords": ["luminosité", "brightness"],
    },

    "SCREEN_OFF": {
        "patterns": [
            (r"(?:éteins?|eteins?)\s+l(?:a|')?\s*écran", {}),
        ],
        "keywords": ["éteins l'écran"],
    },

    # SYSTÈME
    "SYSTEM_SHUTDOWN": {
        "patterns": [
            (r"(?:eteins?|éteins?)\s+(?:l(?:a|')|le\s+)?(?:pc|ordi|ordinateur)", {}),
        ],
        "keywords": ["éteins", "shutdown"],
    },

    "SYSTEM_LOCK": {
        "patterns": [
            (r"(?:verrouille|verrou)", {}),
        ],
        "keywords": ["verrouille"],
    },

    "SYSTEM_TIME": {
        "patterns": [
            (r"(?:quelle|quel)\s+(?:heure|jour|date)", {}),
        ],
        "keywords": ["heure", "date"],
    },

    # FENÊTRE
    "WINDOW_CLOSE": {
        "patterns": [
            (r"(?:ferme|referme)\s+(?:ça|ca|ça|cette|l(?:a|')?|le\s+)?(?:fenetre|fenêtre|ça|ca)", {}),
        ],
        "keywords": ["ferme la fenêtre", "ferme ça"],
    },

    # RÉSEAU
    "WIFI_LIST": {
        "patterns": [
            (r"(?:liste|affiche).*wifi", {}),
        ],
        "keywords": ["wifi", "réseau"],
    },

    # HISTORIQUE
    "REPEAT_LAST": {
        "patterns": [
            (r"(?:répète|repete|rejoue|refais)\s+(?:la|le)?\s*(?:dernière|derniere|même|meme)", {}),
        ],
        "keywords": ["répète", "rejoue"],
    },

    # VISION (Semaine 13)
    "VISION_READ_SCREEN": {
        "patterns": [
            (r"(?:lis|lecture|what's on|qu'y a-t-il).*(?:écran|ecran|screen)", {}),
            (r"(?: OCR |lit le texte)", {}),
        ],
        "keywords": ["lis l'écran", "lecture écran", "OCR"],
    },
    "VISION_CLICK_TEXT": {
        "patterns": [
            (r"clique\s+(?:sur|sur\s+le|sur\s+la)\s+(?P<text>.+)", {"text": "{text}"}),
            (r"click\s+(?:on|on\s+the)\s+(?P<text>.+)", {"text": "{text}"}),
        ],
        "keywords": ["clique sur", "click on"],
    },
    "VISION_SUMMARIZE": {
        "patterns": [
            (r"(?:résume|summarize|what's displayed).*", {}),
        ],
        "keywords": ["résumé écran", "summarize"],
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
#  FUZZY MATCHING (avec tolérance d'erreurs)
# ═══════════════════════════════════════════════════════════════════════════════

APPS_KNOWN = [
    "chrome", "firefox", "edge", "brave",
    "spotify", "vscode", "visual studio code", "notepad", "word", "excel",
    "discord", "slack", "teams", "vlc", "gimp", "blender",
]

FOLDERS_KNOWN = [
    "documents", "desktop", "téléchargements", "downloads",
    "musique", "music", "images", "pictures", "videos", "vidéos",
    "bureau",
]


def fuzzy_match(word: str, choices: list) -> str | None:
    """Fuzzy matching avec tolérance 0.7"""
    matches = get_close_matches(word, choices, n=1, cutoff=0.7)
    return matches[0] if matches else None


def extract_app_name(text: str) -> str | None:
    """Extrait le nom d'une app avec fuzzy matching"""
    words = text.split()
    for word in words:
        matched = fuzzy_match(word, APPS_KNOWN)
        if matched:
            return matched
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  NORMALISATION
# ═══════════════════════════════════════════════════════════════════════════════

def normalize(text: str) -> str:
    """Normalise la commande pour améliorer la compréhension"""
    text = text.lower().strip()

    # Remplacements courants
    # IMPORTANT : les formes normalisées doivent correspondre aux verbes
    # utilisés dans FAST_INTENTS (ouvre, lance, mets, ferme, etc.)
    replacements = {
        "s'il te plaît": "",
        "stp": "",
        "est-ce que tu peux": "",
        "peux-tu": "",
        "est-ce que": "",
        "lancer": "lance",
        "démarrer": "ouvre",
        "démarre": "ouvre",
        "mets-moi": "mets",
        "amène": "mets",
        "ouvre-moi": "ouvre",
        "exécute": "lance",
        "execute": "lance",
    }

    for k, v in replacements.items():
        text = text.replace(k, v)

    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  FAST INTENT PARSER (0 TOKENS)
# ═══════════════════════════════════════════════════════════════════════════════

def fast_parse(text: str) -> RouterResult | None:
    """
    Parse rapide basé sur regex + patterns
    Retourne None si pas de match confiant
    """
    text_norm = normalize(text)

    # 1. Essayer les patterns d'abord (plus spécifiques)
    for intent_name, intent_data in FAST_INTENTS.items():
        for pattern, param_template in intent_data.get("patterns", []):
            match = re.search(pattern, text_norm)
            if match:
                params = {}
                # Construire les params à partir du template
                for param_key, param_template_val in param_template.items():
                    if isinstance(param_template_val, str) and "{" in param_template_val:
                        # Remplacer {app} par la valeur du groupe
                        var_name = param_template_val.strip("{}")
                        if var_name in match.groupdict():
                            params[param_key] = match.group(var_name)
                    else:
                        params[param_key] = param_template_val

                logger.info(f"[Router] fast_parse MATCH: {intent_name} (pattern)")
                return RouterResult(
                    intent=intent_name,
                    params=params,
                    confidence=0.90,
                    source="fast_intent"
                )

    # 2. Fallback keywords (moins confiant)
    for intent_name, intent_data in FAST_INTENTS.items():
        keywords = intent_data.get("keywords", [])
        if any(kw in text_norm for kw in keywords):
            logger.info(f"[Router] fast_parse MATCH: {intent_name} (keyword)")
            return RouterResult(
                intent=intent_name,
                params={},
                confidence=0.65,
                source="fuzzy"
            )

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTEXT MEMORY (optionnel mais utile)
# ═══════════════════════════════════════════════════════════════════════════════

_last_intent = None
_context_memory = {}


def update_context(intent: str, params: dict = None):
    """Met à jour le contexte de la dernière commande"""
    global _last_intent
    _last_intent = intent
    if params:
        _context_memory[intent] = params


def get_context():
    """Retourne le contexte courant"""
    return _last_intent, _context_memory.get(_last_intent, {})


# ═══════════════════════════════════════════════════════════════════════════════
#  LEVEL 4: CORRECTION DETECTION & CONTEXT FUSION
# ═══════════════════════════════════════════════════════════════════════════════

CORRECTION_WORDS = [
    "non", "plutôt", "je veux dire", "pas ça", "mais",
    "correction", "attends", "stop", "refais", "non non"
]

REFINEMENT_PATTERNS = {
    # Patterns pour modifier les paramètres selon la commande suivante
    "onglet": {"action": "new_tab"},
    "nouvel onglet": {"action": "new_tab"},
    "nouvelle fenêtre": {"action": "new_window"},
    "incognito": {"action": "incognito"},
    "fenêtre privée": {"action": "incognito"},
    "fenêtre incognito": {"action": "incognito"},
}


def is_correction(command: str) -> bool:
    """Détecte si la commande est une correction/modification."""
    command_lower = command.lower().strip()
    return any(word in command_lower for word in CORRECTION_WORDS)


def refine_params_from_command(command: str, last_params: dict, intent: str = "") -> dict:
    """
    Affine les paramètres en fonction de la commande de correction.
    
    Exemple:
      intent = "APP_OPEN"
      last_params = {"app_name": "chrome"}
      command = "non, ouvre un onglet"
      
      result = {"app_name": "chrome", "action": "new_tab"}
    """
    from core.param_refiner import refine_params
    
    refined = last_params.copy()
    
    # Utiliser le système de refinement basé sur les patterns d'intents
    if intent:
        refined = refine_params(intent, refined, command)
        return refined
    
    # Fallback: patterns simples dans router
    command_lower = command.lower()
    for pattern, action in REFINEMENT_PATTERNS.items():
        if pattern in command_lower:
            refined.update(action)
            logger.info(f"[Router] Refined params: {action}")
            return refined
    
    return refined


def handle_correction(command: str, context_memory) -> RouterResult:
    """
    Gère les corrections/modifications basées sur le contexte.
    
    Si l'utilisateur dit "non, plutôt..." ou "attends, pas ça",
    on utilise l'intention précédente mais avec params modifiés.
    """
    frame = context_memory.get_current_frame()
    
    if not frame:
        # Pas de contexte précédent
        return None
    
    # Affiner les params avec les mots-clés de la nouvelle commande
    # Passer l'intent pour un refinement intelligent basé sur le type d'intention
    refined_params = refine_params_from_command(command, frame.params, intent=frame.intent)
    
    logger.info(f"[Router] 🔄 Correction detected → {frame.intent} (refined params)")
    
    return RouterResult(
        intent=frame.intent,
        params=refined_params,
        confidence=0.95,
        source="context_correction"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTER PRINCIPAL (NIVEAU 2 + 4)
# ═══════════════════════════════════════════════════════════════════════════════

def route(command: str, llm_parser=None) -> RouterResult:
    """
    Router intelligent multi-couches (LEVEL 2 + 4 + Parameter Parser).

    Flow :
    0. Correction Detection (contexte)    → 0 tokens (LEVEL 4)
    1. Fast parse (regex)                 → 0 tokens
    2. Fuzzy match                        → 0 tokens  
    3. Semantic match                     → 0 tokens
    4. Fallback LLM                       → ~300 tokens
    5. Parameter enrichment               → intelligent extraction

    Args:
        command: commande utilisateur brute
        llm_parser: fonction(command) -> dict avec intent/params/confidence

    Returns:
        RouterResult avec intent, params, confidence, source
    """

    logger.info(f"[Router] Route command: '{command}'")
    
    context_memory = get_context_memory()
    param_parser = get_parameter_parser()

    def _validate_non_llm_result(candidate: RouterResult) -> RouterResult:
        """Validate/correct non-LLM candidate using Groq validator when available."""
        if candidate is None:
            return candidate

        if candidate.source == "llm":
            return candidate

        validator = get_intent_validator()
        verdict = validator.validate(command=command, intent=candidate.intent, params=candidate.params or {})

        if verdict.is_valid:
            return candidate

        corrected_intent = verdict.corrected_intent or candidate.intent
        corrected_params = verdict.corrected_params or candidate.params
        logger.info(
            f"[Router] Groq correction: {candidate.intent} -> {corrected_intent} "
            f"(src={candidate.source}, reason={verdict.reason})"
        )
        return RouterResult(
            intent=corrected_intent,
            params=corrected_params,
            confidence=max(candidate.confidence, verdict.confidence),
            source="groq_correction",
        )

    # 0. CORRECTION DETECTION (LEVEL 4 — 0 tokens)
    if is_correction(command):
        correction_result = handle_correction(command, context_memory)
        if correction_result:
            correction_result = _validate_non_llm_result(correction_result)
            context_memory.push_frame(correction_result.intent, correction_result.params, command, correction_result.confidence)
            return correction_result

    # 1. FAST PARSE (0 tokens)
    result = fast_parse(command)
    if result:
        if result.confidence >= 0.85:
            result = _validate_non_llm_result(result)
            logger.info(f"[Router] ✅ Fast parse → {result.intent} (conf={result.confidence:.2f})")
            update_context(result.intent, result.params)
            context_memory.push_frame(result.intent, result.params, command, result.confidence)
            return result

        # Confiante moyenne → peut utiliser contexte
        if result.confidence >= 0.70:
            result = _validate_non_llm_result(result)
            logger.info(f"[Router] ✅ Fuzzy match → {result.intent} (conf={result.confidence:.2f})")
            update_context(result.intent, result.params)
            context_memory.push_frame(result.intent, result.params, command, result.confidence)
            return result

    # 3. SEMANTIC MATCH (0 tokens) — LEVEL 3
    try:
        from core.semantic_router import get_semantic_router
        semantic_router = get_semantic_router()
        semantic_result = semantic_router.classify(command, threshold=0.60)
        if semantic_result:
            router_result = RouterResult(
                intent=semantic_result.intent,
                params={},
                confidence=semantic_result.confidence,
                source="semantic"
            )
            router_result = _validate_non_llm_result(router_result)
            logger.info(f"[Router] ✅ Semantic match → {router_result.intent} (conf={router_result.confidence:.2f})")
            update_context(router_result.intent, {})
            context_memory.push_frame(router_result.intent, {}, command, router_result.confidence)
            return router_result
    except Exception as e:
        logger.debug(f"[Router] Semantic match error (skip): {e}")

    # 4. FALLBACK LLM (si disponible et pas de match confiant)
    if llm_parser:
        logger.info(f"[Router] ⚠️ Fallback to LLM parser")
        try:
            llm_result = llm_parser(command)
            if isinstance(llm_result, dict):
                router_result = RouterResult(
                    intent=llm_result.get("intent", "UNKNOWN"),
                    params=llm_result.get("params", {}),
                    confidence=llm_result.get("confidence", 0.5),
                    source="llm"
                )
                update_context(router_result.intent, router_result.params)
                context_memory.push_frame(router_result.intent, router_result.params, command, router_result.confidence)
                return router_result
        except Exception as e:
            logger.error(f"[Router] LLM parser error: {e}")

    # FINAL FALLBACK (UNKNOWN)
    logger.warning(f"[Router] ❌ No match → UNKNOWN")
    fallback_result = RouterResult(
        intent="UNKNOWN",
        params={},
        confidence=0.0,
        source="fallback"
    )
    return fallback_result
