"""
agent.py — Cerveau de l'agent PC
SEMAINE 4+ : Pipeline IA + Mémoire conversationnelle.

Mémoire conversationnelle :
  - Jarvis garde en mémoire les N derniers échanges
  - Si la commande est ambiguë, Groq reçoit le contexte pour la résoudre
  - Les réponses en attente (ex: "Lequel ouvrir ?") sont mémorisées
    pour interpréter la prochaine réponse de l'utilisateur
"""

import time
import re
from pathlib import Path

from config.logger import get_logger
from core.jarvis_memory import JarvisMemory

logger = get_logger(__name__)

MAX_HISTORY = 8   # Nombre d'échanges conservés en mémoire


class ConversationContext:
    """
    Mémoire universelle de Jarvis.
    Capture automatiquement tout ce qui se passe — fichiers, apps,
    navigateur, musique, système, ou n'importe quelle future fonctionnalité.
    """

    def __init__(self):
        self.history: list = []
        self.pending_context: dict = {}

        # ── Mémoire universelle ───────────────────────────────────────────────
        # Stocke le dernier résultat de CHAQUE catégorie d'action.
        # Extensible automatiquement — pas besoin de modifier le code
        # quand une nouvelle fonctionnalité est ajoutée.
        self._memory: dict = {}

        # Raccourcis maintenus pour compatibilité avec le reste du code
        self.current_directory: str | None = None
        self.last_opened_item: dict = {}
        self.active_surface: dict = {}

    # ── Historique conversation ───────────────────────────────────────────────

    def add_user(self, message: str):
        self.history.append({"role": "user", "content": message})
        self._trim()

    def add_assistant(self, message: str):
        self.history.append({"role": "assistant", "content": message})
        self._trim()

    def get_history_for_groq(self) -> list:
        return self.history[-(MAX_HISTORY * 2):]

    # ── Mémoire universelle ───────────────────────────────────────────────────

    def remember(self, category: str, data: dict):
        """
        Mémorise le résultat d'une action par catégorie.

        Catégories automatiques :
          file, folder, app, browser, audio, document,
          system, network, screen, macro, search ...

        Exemple :
          context.remember("file", {"path": "E:/films", "name": "films", "is_dir": True})
          context.remember("audio", {"track": "lofi mix", "volume": 70})
          context.remember("browser", {"url": "youtube.com", "query": "Python"})
        """
        if not category or not data:
            return
        self._memory[category] = {
            **data,
            "_remembered_at": int(time.time()),
        }

    def recall(self, category: str) -> dict:
        """
        Récupère la dernière mémoire d'une catégorie.
        Retourne {} si rien n'est mémorisé.
        """
        return dict(self._memory.get(category) or {})

    def recall_all(self) -> dict:
        """Retourne toute la mémoire — utilisé pour donner le contexte à Groq."""
        return dict(self._memory)

    def recall_recent(self, max_age_seconds: int = 300) -> dict:
        """
        Retourne les mémoires récentes (moins de max_age_seconds).
        Utile pour savoir ce qui est encore pertinent.
        """
        now = time.time()
        return {
            k: v for k, v in self._memory.items()
            if (now - v.get("_remembered_at", 0)) <= max_age_seconds
        }

    def get_memory_summary(self) -> str:
        """
        Résumé de la mémoire en texte — injecté dans le prompt Groq
        pour que Groq sache ce qui s'est passé récemment.
        """
        recent = self.recall_recent(600)  # 10 dernières minutes
        if not recent:
            return ""

        lines = []
        for category, data in recent.items():
            # Nettoyer les métadonnées internes
            clean = {k: v for k, v in data.items() if not k.startswith("_")}
            if category == "file":
                lines.append(f"Dernier fichier manipulé : {clean.get('name')} ({clean.get('path')})")
            elif category == "folder":
                lines.append(f"Dernier dossier ouvert : {clean.get('name')} ({clean.get('path')})")
            elif category == "app":
                lines.append(f"Dernière app lancée : {clean.get('name')}")
            elif category == "browser":
                lines.append(f"Navigateur actif sur : {clean.get('url') or clean.get('query', '')}")
            elif category == "audio":
                lines.append(f"Audio : {clean.get('track') or clean.get('action', '')}, volume {clean.get('volume', '')}")
            elif category == "search":
                lines.append(f"Dernière recherche : '{clean.get('query')}' → {clean.get('count', 0)} résultat(s)")
            elif category == "document":
                lines.append(f"Document actif : {clean.get('name')} ({clean.get('path')})")
            else:
                lines.append(f"{category} : {clean}")
        return "\n".join(lines)

    # ── Contexte en attente ───────────────────────────────────────────────────

    def set_pending(self, intent: str, params: dict, question: str,
                    choices: list = None, raw_command: str = ""):
        self.pending_context = {
            "intent":      intent,
            "params":      params,
            "question":    question,
            "choices":     choices or [],
            "raw_command": raw_command,
        }

    def clear_pending(self):
        self.pending_context = {}

    def has_pending(self) -> bool:
        return bool(self.pending_context)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self.history = []
        self.pending_context = {}
        self._memory = {}
        self.current_directory = None
        self.last_opened_item = {}
        self.active_surface = {}

    def _trim(self):
        if len(self.history) > MAX_HISTORY * 2:
            self.history = self.history[-(MAX_HISTORY * 2):]


class Agent:
    """
    Agent principal — Pipeline IA + Mémoire conversationnelle.
    Comprend les réponses de suivi, les références ("celui-là", "le premier"),
    et maintient le fil de la conversation comme ChatGPT.
    """

    def __init__(self):
        self.running = False
        self.context = ConversationContext()
        self._dr = None

        # Pré-chargement immédiat — évite les délais à la première commande
        logger.info("Pré-chargement des modules Jarvis...")
        from core.command_parser import CommandParser
        from core.intent_executor import IntentExecutor
        from core.history_manager import HistoryManager
        from core.macros import MacroManager
        from core.jarvis_voice import JarvisVoice
        self._parser   = CommandParser()
        self._executor = IntentExecutor()
        self._history  = HistoryManager()
        self._macros   = MacroManager()
        self._voice    = JarvisVoice()
        self._memory   = JarvisMemory()
        logger.info("Agent prêt — tous les modules chargés.")

    @property
    def parser(self):
        if self._parser is None:
            from core.command_parser import CommandParser
            self._parser = CommandParser()
        return self._parser

    @property
    def executor(self):
        if self._executor is None:
            from core.intent_executor import IntentExecutor
            self._executor = IntentExecutor()
        return self._executor

    @property
    def history(self):
        if self._history is None:
            from core.history_manager import HistoryManager
            self._history = HistoryManager()
        return self._history

    @property
    def macros(self):
        if self._macros is None:
            from core.macros import MacroManager
            self._macros = MacroManager()
        return self._macros

    @property
    def voice(self):
        """JarvisVoice — moteur de réponse naturelle (lazy init)."""
        if self._voice is None:
            from core.jarvis_voice import JarvisVoice
            self._voice = JarvisVoice()
        return self._voice

    def handle_command(self, command: str, source: str | None = None) -> dict:
        """
        Traite une commande avec contexte conversationnel — VERSION JARVIS VOCAL.
        1. Réponse de suivi ?       → résoudre avec le contexte mémorisé
        2. Parsing Groq + contexte  → historique injecté comme vraie conversation
        3. Exécution                → action sur le PC
        4. Réponse naturelle Groq   → JarvisVoice génère la phrase dynamiquement
        5. Mémoriser l'échange      → pour les prochaines questions
        """
        started = time.time()
        logger.info(f"Commande reçue : '{command}'")
        if not command or not command.strip():
            return self._response(False, "Commande vide reçue.")

        raw = command.strip()

        # Détecter et mémoriser les faits personnels — toujours, peu importe le chemin
        self._memory.extract_facts_from_command(raw)

        # ── Réponse de suivi en attente ? ─────────────────────────────────────
        if self.context.has_pending():
            result = self._handle_followup(raw)
            if result is not None:
                result = self._normalize_result(result)
                self.context.add_user(raw)
                # Générer une réponse naturelle pour le followup aussi
                followup_intent = self.context.pending_context.get("intent", "FOLLOWUP")
                followup_params = self.context.pending_context.get("params", {})
                natural_msg = self.voice.generate(
                    user_command=raw,
                    intent=followup_intent,
                    params=followup_params,
                    exec_result=result,
                    conversation_history=self.context.history,
                )
                result = dict(result)
                result["message"] = natural_msg
                self.context.add_assistant(natural_msg)
                data = result.get("data") or {}
                if not (isinstance(data, dict) and data.get("awaiting_choice")):
                    self.context.clear_pending()
                enriched = self._enrich(result, "FOLLOWUP", 0.95, "context")
                self._save_history_entry(
                    command=raw,
                    result=enriched,
                    intent="FOLLOWUP",
                    source=source or "context",
                    duration_ms=int((time.time() - started) * 1000),
                )
                return enriched

        # ── Parsing IA avec contexte ──────────────────────────────────────────
        # Mémoire persistante (survit aux redémarrages)
        persistent_summary = self._memory.get_context_summary(max_age_minutes=60)
        # Mémoire session (RAM — échanges en cours)
        session_summary = self.context.get_memory_summary()

        # Combiner les deux
        full_summary = "\n".join(filter(None, [persistent_summary, session_summary]))

        history_for_groq = self.context.get_history_for_groq()
        if full_summary:
            history_for_groq = [
                {"role": "system", "content": f"MÉMOIRE JARVIS:\n{full_summary}", "memory": full_summary}
            ] + history_for_groq

        parsed       = self.parser.parse_with_context(raw, history_for_groq)
        intent       = parsed.get("intent",     "UNKNOWN")
        params       = self._apply_context_to_params(raw, intent, parsed.get("params", {}) or {})
        intent, params = self._override_intent_with_context(raw, intent, params)
        confidence   = parsed.get("confidence", 0.0)
        parse_source = parsed.get("source",     "fallback")
        logger.info(f"Intent={intent} conf={confidence:.2f} src={parse_source} params={params}")

        clarification = self._build_clarification_if_needed(raw, intent, params, confidence, parse_source)
        if clarification is not None:
            self.context.add_user(raw)
            self.context.add_assistant(clarification.get("message", ""))
            self.context.set_pending(
                intent="__CLARIFY_INTENT__",
                params={"options": clarification.get("data", {}).get("choices", [])},
                question=clarification.get("message", ""),
                choices=clarification.get("data", {}).get("choices", []),
                raw_command=raw,
            )
            enriched = self._enrich(clarification, "UNKNOWN", max(confidence, 0.5), parse_source)
            self._save_history_entry(
                command=raw,
                result=enriched,
                intent="UNKNOWN",
                source=source or parse_source,
                duration_ms=int((time.time() - started) * 1000),
            )
            return enriched

        # ── Réponse directe (sans exécution) pour questions de connaissance ─
        if intent == "KNOWLEDGE_QA":
            direct_msg = (parsed.get("response_message") or "").strip()
            if not direct_msg:
                direct_msg = "Je peux répondre directement à cette question. Reformule-la en une phrase simple."

            result = {
                "success": True,
                "message": direct_msg,
                "data": {"mode": "knowledge_qa", "answered_by": parse_source},
            }

            self.context.add_user(raw)
            self.context.add_assistant(direct_msg)

            enriched = self._enrich(result, intent, confidence, parse_source)
            self._save_history_entry(
                command=raw,
                result=enriched,
                intent=intent,
                source=source or parse_source,
                duration_ms=int((time.time() - started) * 1000),
            )
            return enriched

        # ── Exécution ─────────────────────────────────────────────────────────
        result = self.executor.execute(intent, params, raw_command=raw, agent=self)
        result = self._normalize_result(result)

        # ── Réponse naturelle JARVIS — générée dynamiquement par Groq ─────────
        # Plus de if/if/if avec des répliques figées.
        # Groq reçoit : ce que l'utilisateur a dit + ce qui s'est passé + contexte
        # Et génère une réponse unique, naturelle, contextuelle.
        jarvis_message = self.voice.generate(
            user_command=raw,
            intent=intent,
            params=params,
            exec_result=result,
            conversation_history=self.context.history,
        )
        result = dict(result) if result else {"success": False, "message": jarvis_message, "data": {}}
        result["message"] = jarvis_message
        
        # [FIX MUSIC_PLAYLIST_LIST] - Append display data if available
        # Some intents (like MUSIC_PLAYLIST_LIST) have formatted display tables
        # that should be shown to the user alongside the natural message
        if result and isinstance(result, dict):
            data_block = result.get("data")
            if not isinstance(data_block, dict):
                data_block = {}
            display_data = data_block.get("display", "")
            if display_data and isinstance(display_data, str):
                result["message"] = f"{jarvis_message}\n\n{display_data}"

        # ── Mémoriser l'échange ───────────────────────────────────────────────
        self._update_navigation_context(intent, result)
        self._update_interaction_context(intent, params, result)
        self._update_universal_memory(intent, params, result)
        self.context.add_user(raw)
        self.context.add_assistant(jarvis_message)

        # Si Jarvis a posé une question (choix en attente) → mémoriser
        data = result.get("data") or {}
        if isinstance(data, dict) and data.get("awaiting_choice"):
            # Utiliser pending_intent/pending_params si fournis par l'executor
            # (ex: APP_OPEN qui détecte que l'app est déjà ouverte)
            pending_intent = data.get("pending_intent") or intent
            pending_params = data.get("pending_params") or params
            self.context.set_pending(
                intent=pending_intent,
                params=pending_params,
                question=jarvis_message,
                choices=data.get("choices", ["oui", "non"]),
                raw_command=raw,
            )

        enriched = self._enrich(result, intent, confidence, parse_source)
        self._save_history_entry(
            command=raw,
            result=enriched,
            intent=intent,
            source=source or parse_source,
            duration_ms=int((time.time() - started) * 1000),
        )
        return enriched

    @staticmethod
    def _normalize_result(result) -> dict:
        """
        Normalise les résultats venant de l'executor pour garantir un dict.
        Evite les crashs si un handler retourne une string/list/None par erreur.
        """
        if isinstance(result, dict):
            return result
        if result is None:
            return {
                "success": False,
                "message": "Execution vide renvoyee par l'intent executor.",
                "data": {},
            }
        if isinstance(result, str):
            text = result.strip()
            return {
                "success": False,
                "message": text or "Execution invalide renvoyee par l'intent executor.",
                "data": {"raw_result": result, "result_type": "str"},
            }
        return {
            "success": False,
            "message": f"Execution invalide (type={type(result).__name__}).",
            "data": {"raw_result": str(result), "result_type": type(result).__name__},
        }

    def _handle_followup(self, reply: str) -> dict | None:
        """
        Interprète une réponse courte en tenant compte du contexte en attente.
        Ex: "celui dans Documents", "le 1", "le premier", "2"
        """
        pending      = self.context.pending_context
        intent       = pending.get("intent", "")
        params       = pending.get("params", {})
        choices      = pending.get("choices", [])
        original_cmd = pending.get("raw_command", "")
        r = reply.lower().strip()

        if intent == "__CLARIFY_INTENT__":
            clarified = self._resolve_intent_clarification(reply, pending.get("choices", []), original_cmd)
            if clarified is not None:
                return clarified

        # ── Résolution numérique ──────────────────────────────────────────────
        num_match = re.search(r'\b(\d+)\b', r)
        if num_match:
            idx = int(num_match.group(1)) - 1
            if 0 <= idx < len(choices):
                return self._open_choice(intent, params, choices[idx])

        word_numbers = {
            "premier": 0, "première": 0, "first": 0, "un": 0,
            "deuxième": 1, "second": 1, "deux": 1, "two": 1,
            "troisième": 2, "trois": 2, "three": 2,
        }
        for word, idx in word_numbers.items():
            if word in r and 0 <= idx < len(choices):
                return self._open_choice(intent, params, choices[idx])

        # ── Résolution par dossier mentionné ──────────────────────────────────
        folder_hints = {
            "documents": "documents", "bureau": "desktop", "desktop": "desktop",
            "downloads": "downloads", "téléchargements": "downloads",
            "telechargements": "downloads", "musique": "music", "music": "music",
            "images": "pictures", "pictures": "pictures",
        }
        for hint, folder_key in folder_hints.items():
            if hint in r:
                for choice in choices:
                    choice_text = self._choice_text(choice)
                    if folder_key in choice_text:
                        return self._open_choice(intent, params, choice)

        # ── Résolution par nom partiel ────────────────────────────────────────
        for choice in choices:
            if len(r) > 2 and r in self._choice_text(choice):
                return self._open_choice(intent, params, choice)

        # ── Si la réponse est une phrase complète -> parse contextuel complet ─
        # Réponse longue ou avec verbe d'action: meilleur résultat via parse_with_context.
        action_verbs = [
            "cherche", "trouve", "ouvre", "lance", "ferme", "mets",
            "veux", "aimerais", "voudrais", "dossier", "fichier",
            "appelle", "nomme", "nommé", "appelé", "situe", "situé",
        ]
        is_rich_reply = len(r.split()) >= 4 or any(v in r for v in action_verbs)
        if is_rich_reply:
            # Phrase riche -> vider le pending et retraiter via Groq+contexte.
            self.context.clear_pending()
            # Détecter et mémoriser les faits personnels dans la commande
            self._memory.extract_facts_from_command(reply)

            # Injecter la mémoire dans le contexte Groq
            memory_summary = self._memory.get_context_summary(max_age_minutes=60)
            history_for_groq = self.context.get_history_for_groq()
            if memory_summary:
                history_for_groq = [
                    {"role": "system", "content": f"MÉMOIRE JARVIS :\n{memory_summary}"}
                ] + history_for_groq

            parsed = self.parser.parse_with_context(reply, history_for_groq)
            p_intent = parsed.get("intent", "UNKNOWN")
            p_params = parsed.get("params", {}) or {}
            if p_intent == "KNOWLEDGE_QA":
                return {
                    "success": True,
                    "message": (parsed.get("response_message") or "").strip() or "Je te réponds directement.",
                    "data": {"mode": "knowledge_qa", "answered_by": parsed.get("source", "groq")},
                }
            if p_intent not in ("UNKNOWN", "INCOMPLETE", ""):
                return self.executor.execute(
                    p_intent, p_params,
                    raw_command=reply, agent=self
                )
            return None

        # ── Résolution sémantique légère sur les choix courts ─────────────────
        if choices:
            best_choice = None
            best_score = 0
            reply_tokens = self._match_tokens(r)
            for choice in choices:
                choice_tokens = self._match_tokens(self._choice_text(choice))
                if not choice_tokens:
                    continue
                score = len(reply_tokens.intersection(choice_tokens))
                if "nouvel" in r and "onglet" in r and "nouvel" in choice_tokens and "onglet" in choice_tokens:
                    score += 3
                if score > best_score:
                    best_score = score
                    best_choice = choice

            if best_choice is not None and best_score > 0:
                return self._open_choice(intent, params, best_choice)

        # ── Déléguer à Groq avec contexte enrichi ────────────────────────────
        full_cmd = f"{original_cmd} — précision: {reply}"
        parsed = self.parser.parse(full_cmd)
        if parsed.get("intent") == "KNOWLEDGE_QA":
            return {
                "success": True,
                "message": (parsed.get("response_message") or "").strip() or "Je te réponds directement.",
                "data": {"mode": "knowledge_qa", "answered_by": parsed.get("source", "groq")},
            }
        if parsed.get("intent") not in ("UNKNOWN", ""):
            result = self.executor.execute(parsed["intent"], parsed["params"], raw_command=full_cmd, agent=self)
            return result

        return None

    def _resolve_intent_clarification(self, reply: str, choices: list, original_cmd: str) -> dict | None:
        r = reply.lower().strip()
        if not choices:
            return None

        selected = None
        num_match = re.search(r"\b(\d+)\b", r)
        if num_match:
            idx = int(num_match.group(1)) - 1
            if 0 <= idx < len(choices):
                selected = choices[idx]

        if selected is None:
            for opt in choices:
                label = str((opt or {}).get("label", "")).lower()
                if label and any(tok in r for tok in label.split()[:3]):
                    selected = opt
                    break

        if selected is None:
            return {
                "success": False,
                "message": "Je n'ai pas saisi ton choix. Reponds 1 ou 2, ou reformule.",
                "data": {"awaiting_choice": True, "choices": choices},
            }

        chosen_intent = selected.get("intent")
        if chosen_intent == "AUDIO_VOLUME_SET":
            level = self.parser._extract_number(original_cmd.lower(), default=50)
            return self.executor.execute("AUDIO_VOLUME_SET", {"level": level}, raw_command=original_cmd, agent=self)
        if chosen_intent == "AUDIO_PLAY":
            query = self.parser._extract_after(original_cmd.lower(), ["joue ", "play ", "ecoute ", "écoute ", "mets "])
            return self.executor.execute("AUDIO_PLAY", {"query": query}, raw_command=original_cmd, agent=self)
        if chosen_intent == "APP_OPEN":
            app_name = re.sub(r"^(mets|ouvre|lance|demarre|démarre)\s+", "", original_cmd.lower()).strip()
            return self.executor.execute("APP_OPEN", {"app_name": app_name, "args": []}, raw_command=original_cmd, agent=self)

        return self.executor.execute(chosen_intent or "UNKNOWN", {}, raw_command=original_cmd, agent=self)

    def _build_clarification_if_needed(self, raw: str, intent: str, params: dict, confidence: float, source: str) -> dict | None:
        lower = raw.lower().strip()

        # Garde-fou global: ne jamais executer une action sensible si l'utilisateur
        # est en mode discussion/souvenir ou exprime une negation explicite.
        destructive_intents = {
            "SYSTEM_SHUTDOWN", "SYSTEM_RESTART", "SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL",
            "SYSTEM_LOGOUT", "SYSTEM_KILL_PROCESS", "FILE_DELETE", "APP_CLOSE", "WINDOW_CLOSE",
        }
        if intent in destructive_intents:
            memory_tone_markers = [
                "tu te souviens", "tu te rappelles", "souviens toi", "rappelle toi",
                "que tu avais", "que tu as", "est ce que", "c'etait", "c’était",
            ]
            if any(m in lower for m in memory_tone_markers) and ("?" in raw or "est ce que" in lower):
                return {
                    "success": True,
                    "message": "Je confirme le contexte, sans executer d'action. Si tu veux une action, formule-la explicitement (ex: 'annule maintenant' ou 'laisse telle quelle').",
                    "data": {"awaiting_choice": False, "kind": "context_confirmation_only"},
                }

            has_negation = (
                "n'" in lower and "pas" in lower
                or "ne " in lower and " pas" in lower
                or "n " in lower and " pas" in lower
            )
            if has_negation:
                return {
                    "success": True,
                    "message": "Compris, je n'exécute pas cette action sensible. Dis-moi exactement l'action positive à faire.",
                    "data": {"awaiting_choice": False, "kind": "blocked_by_global_negation"},
                }

        # Cas conversationnel: question de souvenir, pas une action d'annulation.
        memory_markers = ["tu te souviens", "tu te rappelles", "souviens toi", "rappelle toi"]
        if intent in {"SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL"} and any(m in lower for m in memory_markers):
            return {
                "success": True,
                "message": "Oui, je m'en souviens. C'etait une extinction planifiee apres 4 heures. Tu veux la conserver, l'ajuster, ou ajouter un rappel avant ?",
                "data": {"awaiting_choice": False, "kind": "shutdown_memory_check"},
            }

        # Cas critique: negation explicite de l'annulation.
        if intent in {"SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL"}:
            has_negated_cancel = (
                "n'annule pas" in lower
                or "ne l'annule pas" in lower
                or "n annule pas" in lower
                or ("annule" in lower and "pas" in lower)
            )
            if has_negated_cancel:
                return {
                    "success": True,
                    "message": "Compris, je n'annule rien. Tu veux que j'ajoute seulement un rappel 5 minutes avant l'extinction ?",
                    "data": {"awaiting_choice": False, "kind": "cancel_blocked_by_negation"},
                }

        # [Fix P4] Guard rappel SYSTEM_SHUTDOWN : si params contient reminder=True
        # c'est une demande de notification, pas une extinction.
        # Jarvis ne peut pas encore créer de rappels système, mais ne doit pas
        # re-programmer une extinction à 5 minutes.
        if intent == "SYSTEM_SHUTDOWN" and params.get("reminder"):
            return {
                "success": True,
                "message": (
                    "J'ai bien noté que tu veux un rappel avant l'extinction programmée. "
                    "La fonctionnalité de rappel système arrive bientôt. "
                    "Pour l'instant, l'extinction à 4h est conservée telle quelle."
                ),
                "data": {"awaiting_choice": False, "kind": "reminder_not_yet_implemented"},
            }

        # Suivi contextuel: "préviens/signal 5 minutes avant" ne doit pas
        # être interprété comme une nouvelle extinction à 240s.
        # [Bug2] Vérifier qu'une extinction a déjà été programmée (via mémoire)
        # avant d'activer ce garde-fou — évite de bloquer "éteins dans 5 min"
        # alors qu'aucune extinction n'est en cours.
        reminder_markers = ["avant", "minute", "minutes", "previens", "previent", "avert", "signal", "rappel"]
        shutdown_already_scheduled = False
        try:
            mem_system = self._memory.recall_last("system")
            if isinstance(mem_system, dict):
                last_intent = str(mem_system.get("intent", "")).upper()
                shutdown_already_scheduled = last_intent in {
                    "SYSTEM_SHUTDOWN", "SYSTEM_RESTART"
                }
        except Exception:
            pass
        if (
            shutdown_already_scheduled
            and intent in {"SYSTEM_SHUTDOWN", "SYSTEM_CANCEL_SHUTDOWN", "POWER_CANCEL"}
            and sum(1 for m in reminder_markers if m in lower) >= 2
        ):
            return {
                "success": True,
                "message": "J'ai compris: tu demandes un rappel avant l'extinction deja programmee. Je ne dois pas reprogrammer l'arret. Je peux garder l'extinction actuelle ou l'annuler si tu veux.",
                "data": {"awaiting_choice": False, "kind": "shutdown_reminder_request"},
            }

        # Cas critique: "mets ..." ambigu entre musique et action système/app.
        if intent == "AUDIO_PLAY" and lower.startswith("mets ") and "volume" not in lower:
            tail = lower[5:].strip()
            app_hints = ["chrome", "firefox", "edge", "vscode", "word", "excel", "spotify", "discord"]
            if any(h in tail for h in app_hints):
                return {
                    "success": False,
                    "message": "Tu veux que je lance l'application ou que je joue une musique ? (1: lancer app, 2: jouer musique)",
                    "data": {
                        "awaiting_choice": True,
                        "choices": [
                            {"id": 1, "label": "Lancer application", "intent": "APP_OPEN"},
                            {"id": 2, "label": "Jouer musique", "intent": "AUDIO_PLAY"},
                        ],
                    },
                }

        # Recherche sans sujet -> demander ce qu'on cherche
        if intent in {"BROWSER_SEARCH", "FILE_SEARCH", "FILE_SEARCH_CONTENT"} and not params.get("query") and not params.get("keyword"):
            return {
                "success": False,
                "message": "Tu veux chercher quoi ? Sur le web, dans tes fichiers, ou dans un document ?",
                "data": {
                    "awaiting_choice": True,
                    "choices": ["sur le web", "dans mes fichiers", "dans un document"],
                },
            }

        # Si fallback + faible confiance, demander une précision au lieu d'exécuter faux.
        if source == "fallback" and confidence < 0.65 and intent in {"UNKNOWN", "AUDIO_PLAY", "SYSTEM_NETWORK"}:
            return {
                "success": False,
                "message": "Je veux etre sur de bien comprendre. Reformule en une phrase directe (ex: 'mets le volume a 70%', 'ouvre chrome', 'liste les reseaux wifi').",
                "data": {"awaiting_choice": False},
            }

        return None

    def _open_choice(self, intent: str, params: dict, choice) -> dict:
        """Exécute l'action sur un choix résolu depuis le contexte."""
        if isinstance(choice, dict):
            choice_path = choice.get("path") or choice.get("name") or ""
            choice_type = "directory" if choice.get("is_dir") else "any"
        else:
            choice_path = str(choice)
            choice_type = "any"

        if intent == "FILE_OPEN" or ('/' in choice_path or '\\' in choice_path):
            return self.executor.execute(
                "FILE_OPEN",
                {**params, "path": choice_path, "target_type": choice_type},
                raw_command=choice_path,
                agent=self,
            )
        if intent == "WINDOW_CLOSE" and isinstance(choice, dict):
            return self.executor.execute(
                "WINDOW_CLOSE",
                {
                    **params,
                    "hwnd": choice.get("hwnd"),
                    "pid": choice.get("pid"),
                    "title": choice.get("title"),
                    "query": choice.get("title") or choice.get("process_name") or choice.get("name") or "",
                    "preferred_kind": choice.get("kind") or params.get("preferred_kind"),
                },
                raw_command=str(choice.get("title") or choice),
                agent=self,
            )
        return self.executor.execute(intent, {**params}, raw_command=str(choice), agent=self)

    def _save_history_entry(self, command: str, result: dict, intent: str, source: str, duration_ms: int):
        try:
            self.history.save(
                command=command,
                result=result,
                source=source,
                intent=intent,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.warning(f"Sauvegarde historique ignorée: {exc}")

    @staticmethod
    def _choice_text(choice) -> str:
        if isinstance(choice, dict):
            return " ".join(
                str(choice.get(key, "")).lower().replace("\\", "/")
                for key in ("name", "path", "parent", "title", "process_name", "kind", "label")
            )
        return str(choice).lower().replace("\\", "/")

    @staticmethod
    def _match_tokens(text: str) -> set[str]:
        import re
        import unicodedata

        normalized = unicodedata.normalize("NFKD", str(text or "").lower())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        tokens = set(re.findall(r"[a-z0-9]{3,}", normalized))
        return {tok for tok in tokens if tok not in {"ferme", "fermer", "close", "window", "fenetre", "fichier", "dossier"}}

    def _apply_context_to_params(self, raw: str, intent: str, params: dict) -> dict:
        params = dict(params or {})
        lower = raw.lower()
        folder_reference_tokens = [
            "dedans", "dans ce dossier", "dans ce répertoire", "dans ce repertoire",
            "dans ça", "dans ca", "ici", "là-dedans", "la-dedans",
        ]

        if self.context.current_directory and any(token in lower for token in folder_reference_tokens):
            if intent in {"FILE_OPEN", "FILE_SEARCH", "FILE_SEARCH_TYPE", "FILE_SEARCH_CONTENT", "FOLDER_LIST"}:
                params.setdefault("search_dirs", [self.context.current_directory])
                params.setdefault("current_dir", self.context.current_directory)

        if intent == "FILE_OPEN" and self.context.current_directory and not params.get("current_dir"):
            params["current_dir"] = self.context.current_directory

        if intent in {"FILE_OPEN", "FILE_SEARCH", "FILE_SEARCH_TYPE", "FILE_SEARCH_CONTENT", "FOLDER_LIST"}:
            if not params.get("search_dirs"):
                inferred_dirs = self._infer_search_dirs_from_raw(lower)
                if inferred_dirs:
                    params["search_dirs"] = inferred_dirs

        if intent == "FILE_OPEN" and not params.get("target_type"):
            if any(token in lower for token in ["dossier", "répertoire", "repertoire"]):
                params["target_type"] = "directory"
            elif any(token in lower for token in ["fichier", "document"]):
                params["target_type"] = "file"

        return params

    @staticmethod
    def _infer_search_dirs_from_raw(lower_command: str) -> list[str]:
        import re

        drive_match = re.search(r"(?:disque|disk|lecteur|drive)\s+([a-z])\b", lower_command, re.IGNORECASE)
        if drive_match:
            return [f"{drive_match.group(1).upper()}:\\"]

        folder_map = {
            "documents": "Documents",
            "document": "Documents",
            "desktop": "Desktop",
            "bureau": "Desktop",
            "downloads": "Downloads",
            "téléchargements": "Downloads",
            "telechargements": "Downloads",
            "music": "Music",
            "musique": "Music",
            "pictures": "Pictures",
            "images": "Pictures",
            "videos": "Videos",
            "vidéos": "Videos",
        }
        for token, mapped in folder_map.items():
            if re.search(rf"(?:dans|sur|sous)\s+(?:le|la|les)?\s*{re.escape(token)}\b", lower_command):
                return [mapped]

        return []

    def _update_navigation_context(self, intent: str, result: dict):
        if not isinstance(result, dict):
            return
        data = result.get("data") or {}
        if not isinstance(data, dict):
            return

        if intent == "FILE_CLOSE" and result.get("success"):
            closed_path = data.get("closed_path")
            if closed_path and closed_path == self.context.last_opened_item.get("path"):
                self.context.last_opened_item = {}
            return

        if intent == "FOLDER_LIST" and data.get("path"):
            self.context.current_directory = data["path"]
            return

        if intent == "FILE_OPEN":
            opened_path = data.get("opened_path") or data.get("path")
            if opened_path:
                self.context.last_opened_item = {
                    "path": opened_path,
                    "name": Path(opened_path).name,
                    "stem": Path(opened_path).stem,
                    "is_dir": bool(data.get("is_dir")),
                    "parent": str(Path(opened_path).parent),
                }
                if data.get("is_dir"):
                    self.context.current_directory = opened_path
                else:
                    self.context.current_directory = str(Path(opened_path).parent)

    def _update_interaction_context(self, intent: str, params: dict, result: dict):
        if not isinstance(result, dict):
            return
        data = result.get("data") or {}
        if not result.get("success"):
            return

        if intent == "APP_OPEN":
            app_name = str(params.get("app_name") or params.get("name") or "").strip().lower()
            if self._is_browser_app(app_name):
                self.context.active_surface = {
                    "kind": "browser",
                    "name": app_name,
                    "pid": (data or {}).get("pid"),
                }
                return
            if app_name:
                self.context.active_surface = {
                    "kind": self._classify_app_kind(app_name),
                    "name": app_name,
                    "pid": (data or {}).get("pid"),
                }
                return

        if intent in {
            "BROWSER_OPEN", "BROWSER_SEARCH", "BROWSER_URL",
            "BROWSER_NEW_TAB", "BROWSER_LIST_TABS", "BROWSER_SWITCH_TAB",
            "BROWSER_BACK", "BROWSER_FORWARD", "BROWSER_RELOAD",
            "BROWSER_READ", "BROWSER_SUMMARIZE", "BROWSER_OPEN_RESULT",
            "BROWSER_CLICK_TEXT",
        }:
            browser_name = str(
                (data or {}).get("browser")
                or params.get("browser")
                or "chrome"
            ).lower()
            if browser_name in {"browser", ""}:
                browser_name = "chrome"
            self.context.active_surface = {
                "kind": "browser",
                "name": browser_name,
                "url": (data or {}).get("url"),
                "query": (data or {}).get("query") or params.get("query"),
            }
            return

        if intent == "FOLDER_LIST" and (data or {}).get("path"):
            self.context.active_surface = {
                "kind": "folder",
                "path": data["path"],
                "name": Path(data["path"]).name,
            }
            return

        if intent == "FILE_OPEN":
            opened_path = (data or {}).get("opened_path") or (data or {}).get("path")
            if opened_path:
                self.context.active_surface = {
                    "kind": self._classify_path_kind(opened_path),
                    "path": opened_path,
                    "name": Path(opened_path).name,
                    "title": Path(opened_path).name,
                }
            return

        if intent in {"DOC_READ", "DOC_SUMMARIZE", "DOC_SEARCH_WORD"}:
            doc_path = str(params.get("path") or params.get("file") or "").strip()
            if doc_path:
                self.context.active_surface = {
                    "kind": "document",
                    "path": doc_path,
                    "name": Path(doc_path).name,
                    "title": Path(doc_path).name,
                }
            return

        if intent in {"APP_CLOSE", "FILE_CLOSE", "WINDOW_CLOSE"}:
            app_name = str(params.get("app_name") or params.get("name") or params.get("query") or params.get("title") or "").strip().lower()
            active = self.context.active_surface or {}
            if app_name and any(app_name in str(active.get(key, "")).lower() for key in ("name", "path", "title")):
                self.context.active_surface = {}

    def _update_universal_memory(self, intent: str, params: dict, result: dict):
        """
        Met à jour la mémoire universelle après chaque action réussie.
        Fonctionne pour TOUTES les catégories automatiquement.
        """
        if not isinstance(result, dict):
            return
        if not result.get("success"):
            return

        data = result.get("data") or {}
        category = ""
        remembered_data = None

        # ── Fichiers & Dossiers ───────────────────────────────────────────────
        if intent in {"FILE_OPEN", "FOLDER_LIST", "FOLDER_CREATE"}:
            # Priorité absolue au resolved_path retourné par file_manager.
            # C'est le chemin absolu vérifié — jamais un chemin reconstruit.
            path = (
                data.get("resolved_path") or
                data.get("opened_path") or
                data.get("path") or ""
            )
            # Ne jamais utiliser params.get("path") pour la mémoire,
            # car c'est souvent un nom partiel non résolu.
            is_dir = data.get("is_dir") or intent in {"FOLDER_LIST", "FOLDER_CREATE"}
            category = "folder" if is_dir else "file"
            if path:
                self._memory.remember_event(category, {
                    "path": path,
                    "name": data.get("name") or Path(path).name,
                    "is_dir": is_dir,
                    "parent": str(Path(path).parent),
                })

        elif intent == "FILE_SEARCH":
            results = data.get("results") or []
            query = params.get("query", "")
            self._memory.remember_event("search", {
                "query": query,
                "results": results[:5],
                "count": len(results),
                "type": "file",
            })
            # Mémoriser aussi le premier résultat trouvé pour "ouvre le"
            if results:
                first = results[0]
                if isinstance(first, dict):
                    first_path = str(first.get("path", "") or "")
                    first_name = str(first.get("name", "") or "")
                    first_is_dir = bool(first.get("is_dir", False))
                    first_parent = str(first.get("parent", "") or "")
                else:
                    first_path = str(first or "")
                    first_name = Path(first_path).name if first_path else ""
                    first_is_dir = False
                    first_parent = str(Path(first_path).parent) if first_path else ""

                self._memory.remember_event(
                    "folder" if first_is_dir else "file",
                    {
                        "path": first_path,
                        "name": first_name,
                        "is_dir": first_is_dir,
                        "parent": first_parent,
                    }
                )

        # ── Applications ─────────────────────────────────────────────────────
        elif intent == "APP_OPEN":
            app = params.get("app_name") or params.get("name") or ""
            if app:
                remembered_data = {
                    "name": app,
                    "pid": data.get("pid"),
                    "args": params.get("args", []),
                }
                self._memory.remember_event("app", remembered_data)
                category = "app"

        # ── Navigateur ────────────────────────────────────────────────────────
        elif intent.startswith("BROWSER_"):
            self._memory.remember_event("browser", {
                "intent": intent,
                "url": data.get("url") or params.get("url", ""),
                "query": data.get("query") or params.get("query", ""),
                "site": data.get("site") or params.get("site", ""),
                "tabs": data.get("count"),
            })

        # ── Audio ─────────────────────────────────────────────────────────────
        elif intent.startswith("AUDIO_"):
            remembered_data = {
                "intent": intent,
                "volume": data.get("level") or params.get("level"),
                "track": data.get("track") or params.get("query", ""),
                "action": intent.replace("AUDIO_", "").lower(),
            }
            self._memory.remember_event("audio", remembered_data)
            category = "audio"

        # ── Documents ─────────────────────────────────────────────────────────
        elif intent.startswith("DOC_"):
            doc_path = params.get("path") or params.get("file") or ""
            if doc_path:
                self._memory.remember_event("document", {
                    "path": doc_path,
                    "name": Path(doc_path).name,
                    "action": intent.replace("DOC_", "").lower(),
                    "keyword": params.get("keyword") or params.get("word", ""),
                })

        # ── Système ───────────────────────────────────────────────────────────
        elif intent.startswith("SYSTEM_") or intent.startswith("POWER_"):
            self._memory.remember_event("system", {
                "intent": intent,
                "action": intent.lower(),
                "delay_seconds": params.get("delay_seconds"),
            })

        # ── Réseau ────────────────────────────────────────────────────────────
        elif intent.startswith("WIFI_") or intent.startswith("BLUETOOTH_") or intent == "NETWORK_INFO":
            self._memory.remember_event("network", {
                "intent": intent,
                "ssid": params.get("ssid", ""),
                "action": intent.lower(),
            })

        # ── Macros ────────────────────────────────────────────────────────────
        elif intent.startswith("MACRO_"):
            self._memory.remember_event("macro", {
                "name": params.get("name", ""),
                "action": intent.replace("MACRO_", "").lower(),
                "steps": data.get("steps"),
            })

        # ── Écran ─────────────────────────────────────────────────────────────
        elif intent.startswith("SCREEN_"):
            self._memory.remember_event("screen", {
                "intent": intent,
                "action": intent.replace("SCREEN_", "").lower(),
            })

        # ── Recherche web ─────────────────────────────────────────────────────
        elif intent == "BROWSER_SEARCH":
            self._memory.remember_event("search", {
                "query": params.get("query", ""),
                "engine": params.get("engine", "google"),
                "results": data.get("results", []),
                "count": data.get("count", 0),
                "type": "web",
            })

        # Détecter habitudes répétées → mémoriser comme préférence
        if category == "audio" and remembered_data and remembered_data.get("volume") is not None:
            vol = remembered_data["volume"]
            recent = self._memory.recall_recent("audio", max_age_minutes=10080)
            same_vol = [e for e in recent if e.get("volume") == vol]
            if len(same_vol) >= 3:
                self._memory.remember_fact("preferred_volume", vol)

        if category == "app" and remembered_data and remembered_data.get("name"):
            app = str(remembered_data["name"]).lower()
            all_apps = self._memory.recall_recent("app", max_age_minutes=43200)
            count = sum(1 for e in all_apps if str(e.get("name", "")).lower() == app)
            if count >= 5:
                self._memory.remember_fact("favorite_app", app)

    def _enhance_result_message(self, intent: str, params: dict, result: dict) -> dict:
        if not result.get("success"):
            return result

        message = str(result.get("message") or "").strip()
        if not message:
            return result

        hint = ""
        if intent == "APP_OPEN":
            app_name = str(params.get("app_name") or params.get("name") or "").strip().lower()
            if self._is_browser_app(app_name):
                hint = "Tu veux que je recherche quelque chose ou ouvre un site ?"
        elif intent in {"BROWSER_OPEN"}:
            hint = "Tu veux que je recherche quelque chose ou ouvre un site ?"
        elif intent == "BROWSER_NEW_TAB":
            count = int(params.get("count") or 1)
            if count == 1:
                hint = "Sur quoi veux-tu faire une recherche ?"
            else:
                hint = "Tes onglets sont prêts. Sur quoi veux-tu faire une recherche ?"
        elif intent == "BROWSER_SEARCH":
            hint = "Si tu veux, je peux affiner la recherche ou ouvrir un site precis."
        elif intent in {"FILE_OPEN", "DOC_READ"}:
            path = str(
                (result.get("data") or {}).get("opened_path")
                or (result.get("data") or {}).get("path")
                or params.get("path")
                or params.get("file")
                or ""
            ).strip()
            if path and self._looks_like_document(path):
                hint = "Tu veux que je le lise, le resume ou que je cherche un mot dedans ?"
            elif path and self._looks_like_media(path):
                hint = "Tu veux que je ferme cette fenetre ou que je cherche un autre media ?"
        elif intent == "DOC_SUMMARIZE":
            hint = "Si tu veux, je peux aussi chercher un mot precis dans ce document."

        if hint and hint.lower() not in message.lower():
            result = dict(result)
            result["message"] = f"{message} {hint}".strip()
        return result

    def _override_intent_with_context(self, raw: str, intent: str, params: dict) -> tuple[str, dict]:
        """
        If a folder is already open, an ambiguous "ouvre ..." should prioritize
        opening a file/folder inside that current directory.

        [Fix P1 Music] Si Groq retourne MUSIC_PLAYLIST_CREATE avec songs=[]
        mais la commande contient "dossier"/"ajoute"/"tous les songs",
        on force MUSIC_PLAYLIST_ADD_FOLDER.
        """
        lower = raw.lower().strip()

        # [Fix P1] Override music : ajouter dossier implicite
        music_override = self._override_music_add_folder(lower, intent, params)
        if music_override is not None:
            return music_override

        # [PREFERENCE_SET] Déclarations de préférence implicites :
        # "j'ai ma musique de travail que j'aime jouer quand je code"
        # → Groq route vers MACRO_RUN, mais c'est une déclaration → PREFERENCE_SET
        pref_override = self._override_preference_declaration(lower, intent, params)
        if pref_override is not None:
            return pref_override

        document_override = self._override_document_with_active_context(lower, intent, params)
        if document_override is not None:
            return document_override

        browser_action_override = self._override_browser_action_with_context(lower, intent, params)
        if browser_action_override is not None:
            return browser_action_override

        search_override = self._override_search_with_active_context(lower, intent, params)
        if search_override is not None:
            return search_override

        close_override = self._override_close_with_context(lower, intent, params)
        if close_override is not None:
            return close_override

        if not self.context.current_directory:
            return intent, params

        if not lower.startswith(("ouvre ", "open ", "lis ", "affiche ")):
            return intent, params

        # Do not override obvious app commands.
        app_hints = {
            "chrome", "firefox", "edge", "vscode", "visual studio", "notepad",
            "spotify", "discord", "slack", "teams", "word", "excel", "powerpoint",
            "cmd", "powershell", "terminal", "taskmgr", "gestionnaire",
        }

        candidate = (
            params.get("path")
            or params.get("name")
            or params.get("app_name")
            or ""
        )
        candidate_lower = str(candidate).lower()
        if any(h in candidate_lower for h in app_hints):
            return intent, params

        if intent in {"APP_OPEN", "FILE_SEARCH", "FOLDER_LIST", "UNKNOWN"}:
            path_value = candidate or raw.split(" ", 1)[1].strip()
            new_params = dict(params)
            new_params["path"] = path_value
            new_params.setdefault("current_dir", self.context.current_directory)
            new_params.setdefault("search_dirs", [self.context.current_directory])
            new_params.setdefault("target_type", "any")
            return "FILE_OPEN", new_params

        return intent, params


    @staticmethod
    def _override_preference_declaration(lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        """
        [PREFERENCE_SET] Détecte les déclarations implicites de préférence.

        Groq route souvent ces phrases vers MACRO_RUN ou UNKNOWN :
          "j'ai ma musique de travail que j'aime jouer quand je code"
          "quand je passe en mode travail j'aime jouer ma playlist"
          "mon son de codage c'est ma playlist"

        On les intercepte et on force PREFERENCE_SET.
        """
        # Seulement si Groq n'a pas déjà trouvé PREFERENCE_SET
        if intent == "PREFERENCE_SET":
            return None

        # Marqueurs de déclaration de préférence
        decl_markers = [
            "j ai ma musique de", "j ai mon son de", "j aime souvent jouer",
            "j aime jouer quand je", "quand je passe en mode",
            "quand je code je joue", "quand je travaille je joue",
            "mon son de travail", "mon son de codage", "ma musique de travail",
            "ma musique de codage", "ma playlist de travail",
            "je demanderais joue en mode", "joue en mode travail",
            "joue en mode codage", "mode travail ou mode codage",
        ]

        if not any(m in lower for m in decl_markers):
            return None

        # Extraire le contexte (label)
        label = "travail"
        for ctx in ["codage", "code", "travail", "detente", "concentration", "sport"]:
            if ctx in lower:
                label = ctx
                break

        # Extraire la valeur (playlist/son)
        value = "ma playlist"
        for v in ["ma playlist", "cette playlist", "mon son", "ma musique"]:
            if v in lower:
                value = v
                break

        return "PREFERENCE_SET", {
            "label": label,
            "value": value,
            "category": "music",
        }

    @staticmethod
    def _override_music_add_folder(lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        """
        [Fix P1] Détecte les commandes "ajoute dossier musique à playlist"
        mal parsées par Groq comme MUSIC_PLAYLIST_CREATE avec songs=[].

        Patterns couverts :
          "va dans le dossier Musique, ajoute tous les songs à ma playlist"
          "ajoute ma musique à la playlist X"
          "remplis la playlist avec mes fichiers"
          "je t'ai dit d'ajouter ma musique à la playlist"
        """
        if intent != "MUSIC_PLAYLIST_CREATE":
            return None

        songs  = params.get("songs") or []
        folder = params.get("folder") or params.get("path") or ""

        # Si songs non vides ou dossier déjà fourni → pas d'override nécessaire
        if songs or folder:
            return None

        # Détecter : mention d'un dossier/source + verbe d'ajout
        folder_triggers = [
            "dossier", "musique", "tous les song", "tous les fichier",
            "toute ma", "tout mon", "tout ce qui", "les song",
            "les fichier", "les morceaux", "les titres", "ma biblioth",
        ]
        add_triggers = [
            "ajoute", "ajouter", "mets", "remplis", "remplir",
            "va dans", "copie", "importe", "t ai dit", "j ai dit",
        ]

        has_folder = any(t in lower for t in folder_triggers)
        has_add    = any(t in lower for t in add_triggers)

        if has_folder and has_add:
            name = params.get("name") or ""
            return "MUSIC_PLAYLIST_ADD_FOLDER", {"name": name, "folder": ""}

        return None

    def _override_browser_action_with_context(self, lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        if self._is_result_open_request(lower) and intent in {
            "APP_OPEN", "UNKNOWN", "BROWSER_OPEN", "BROWSER_SEARCH", "FILE_OPEN", "FILE_SEARCH"
        }:
            rank = self._extract_result_rank(lower)
            target_type = "new_tab" if self._mentions_new_tab(lower) else ""
            out = {"rank": rank}
            if target_type:
                out["target_type"] = target_type
            query = self._extract_search_query(lower, params)
            if query:
                out["query"] = query
            return "BROWSER_OPEN_RESULT", out

        new_tab_override = self._override_new_tab_request(lower, intent, params)
        if new_tab_override is not None:
            return new_tab_override

        # Cas fréquent: Groq renvoie APP_OPEN(app=chrome,args=[query]) alors qu'il faut BROWSER_SEARCH.
        if intent == "APP_OPEN":
            app_name = str(params.get("app_name") or params.get("name") or "").strip().lower()
            args = params.get("args") or []
            if self._is_browser_app(app_name) and isinstance(args, list) and args:
                query = str(args[0] or "").strip()
                if query and self._is_search_like_request(lower):
                    return "BROWSER_SEARCH", {"query": query, "browser": app_name}

        # Si l'utilisateur mentionne explicitement chrome + recherche, forcer la recherche web.
        if intent in {"APP_OPEN", "UNKNOWN", "FILE_SEARCH", "FILE_SEARCH_CONTENT"}:
            if self._mentions_browser(lower) and self._is_search_like_request(lower):
                query = self._extract_search_query(lower, params)
                if query and not self._is_explicit_file_request(query.lower()):
                    active = self.context.active_surface or {}
                    browser = active.get("name") if active.get("kind") == "browser" else "chrome"
                    return "BROWSER_SEARCH", {"query": query, "browser": browser}

        return None

    def _override_new_tab_request(self, lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        if not self._mentions_new_tab(lower):
            return None

        if intent in {"BROWSER_NEW_TAB", "BROWSER_SEARCH", "BROWSER_URL"}:
            return None

        count = self._extract_count_from_text(lower)
        query = ""
        if self._is_search_like_request(lower):
            query = self._extract_search_query(lower, params)

        new_params: dict = {"count": count}
        if query:
            new_params["query"] = query
        else:
            url = str(params.get("url") or params.get("link") or "").strip()
            if url:
                new_params["url"] = url

        active = self.context.active_surface or {}
        browser_name = str(params.get("browser") or params.get("app_name") or params.get("name") or "").strip().lower()
        if browser_name and self._is_browser_app(browser_name):
            new_params["browser"] = browser_name
        elif active.get("kind") == "browser" and active.get("name"):
            new_params["browser"] = active.get("name")

        return "BROWSER_NEW_TAB", new_params

    @staticmethod
    def _is_result_open_request(lower: str) -> bool:
        return any(token in lower for token in [
            "ouvre le resultat", "ouvre le résultat", "open result",
            "ouvre le premier résultat", "ouvre le premier resultat",
            "ouvre le deuxième résultat", "ouvre le deuxieme résultat", "ouvre le deuxieme resultat",
            "ouvre le troisième résultat", "ouvre le troisieme résultat", "ouvre le troisieme resultat",
            "ouvre le lien", "ouvre le premier lien", "ouvre le deuxième lien", "ouvre le deuxieme lien",
        ])

    @staticmethod
    def _extract_result_rank(lower: str) -> int:
        if any(k in lower for k in ["premier", "1er", "first"]):
            return 1
        if any(k in lower for k in ["deuxième", "deuxieme", "2e", "second"]):
            return 2
        if any(k in lower for k in ["troisième", "troisieme", "3e", "third"]):
            return 3
        match = re.search(r"\b(\d+)\b", lower)
        return int(match.group(1)) if match else 1

    @staticmethod
    def _mentions_new_tab(lower: str) -> bool:
        return any(token in lower for token in [
            "ouvre un nouvel onglet",
            "ouvre une nouvel onglet",
            "ouvre une nouvelle onglet",
            "ouvre deux nouveaux onglets",
            "ouvre trois nouveaux onglets",
            "ouvre quatre nouveaux onglets",
            "ouvre cinq nouveaux onglets",
            "ouvre des nouveaux onglets",
            "ouvre plusieurs nouveaux onglets",
            "nouvel onglet",
            "nouveaux onglets",
            "new tab",
            "new tabs",
        ])

    @staticmethod
    def _extract_count_from_text(lower: str) -> int:
        match = re.search(r"\b(\d+)\s+(?:nouveau|nouveaux|new)\s+onglets?\b", lower)
        if match:
            return max(1, int(match.group(1)))

        word_map = {
            "un": 1,
            "une": 1,
            "deux": 2,
            "trois": 3,
            "quatre": 4,
            "cinq": 5,
            "six": 6,
            "sept": 7,
            "huit": 8,
            "neuf": 9,
            "dix": 10,
        }
        for word, value in word_map.items():
            if re.search(rf"\b{word}\s+(?:nouveau|nouveaux)\s+onglets?\b", lower):
                return value
        return 1

    def _override_search_with_active_context(self, lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        active = self.context.active_surface or {}
        if active.get("kind") == "folder" and intent in {"FILE_SEARCH", "FILE_SEARCH_TYPE", "FILE_SEARCH_CONTENT"}:
            scoped = dict(params)
            scoped.setdefault("search_dirs", [active.get("path")])
            return intent, scoped

        if active.get("kind") != "browser":
            return None

        if intent not in {"UNKNOWN", "FILE_SEARCH", "FILE_SEARCH_CONTENT", "FOLDER_LIST"}:
            return None

        if not self._is_search_like_request(lower) or self._is_explicit_file_request(lower):
            return None

        query = self._extract_search_query(lower, params)
        if not query:
            return None

        new_params = {"query": query}
        if active.get("name") and active.get("name") != "browser":
            new_params["browser"] = active["name"]
        return "BROWSER_SEARCH", new_params

    def _override_document_with_active_context(self, lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        active = self.context.active_surface or {}
        if active.get("kind") != "document" or not active.get("path"):
            return None

        if params.get("path") or params.get("file"):
            return None

        if self._is_summary_like_request(lower) and intent in {"UNKNOWN", "FILE_OPEN", "FILE_SEARCH", "FILE_SEARCH_CONTENT"}:
            return "DOC_SUMMARIZE", {"path": active["path"]}

        if self._is_document_read_request(lower) and intent in {"UNKNOWN", "FILE_OPEN"}:
            return "DOC_READ", {"path": active["path"]}

        if intent in {"UNKNOWN", "FILE_SEARCH", "FILE_SEARCH_CONTENT"} and self._is_search_like_request(lower):
            query = self._extract_search_query(lower, params)
            if query and not self._is_explicit_file_request(query.lower()):
                return "DOC_SEARCH_WORD", {"path": active["path"], "word": query}

        return None

    def _override_close_with_context(self, lower: str, intent: str, params: dict) -> tuple[str, dict] | None:
        # [Fix] Les intents MUSIC_* ne doivent JAMAIS être écrasés par close_override.
        # "arrete la musique" → MUSIC_STOP doit rester MUSIC_STOP, pas WINDOW_CLOSE.
        if intent.startswith("MUSIC_") or intent.startswith("AUDIO_"):
            return None

        close_prefixes = ("ferme", "fermer", "referme", "refermer", "close", "quitte", "arrete", "arrête")
        is_screen_off_false_positive = intent == "SCREEN_OFF" and any(
            k in lower for k in ["referme", "ferme", "fermer", "close", "quitte"]
        )
        if not lower.startswith(close_prefixes) and not (intent == "APP_CLOSE" and self._has_close_verb(lower)) and not is_screen_off_false_positive:
            return None

        candidate = self._extract_close_candidate(lower, params)
        tab_target = self._extract_tab_or_page_target(lower)
        if tab_target and self._is_browser_app(str(params.get("app_name") or params.get("name") or "")):
            candidate = tab_target
        close_scope = self._infer_close_scope(lower, candidate)
        recent = self.context.last_opened_item or {}
        if recent and (not candidate or candidate in {
            "maintenant", "ça", "ca", "cela", "celui", "celle", "le", "la",
            "le fichier", "le dossier", "ce fichier", "ce dossier",
        }):
            return "FILE_CLOSE", self._build_close_params(recent)

        if recent and self._matches_recent_item(candidate, recent):
            return "FILE_CLOSE", self._build_close_params(recent)

        active = self.context.active_surface or {}
        preferred_kind = self._infer_window_kind_from_close_candidate(candidate, active)
        app_name = str(params.get("app_name") or params.get("name") or "").strip().lower()
        if preferred_kind is None and self._is_browser_app(app_name):
            preferred_kind = "browser"
        query = candidate
        if not query or query in self._generic_close_terms():
            query = str(active.get("title") or active.get("name") or active.get("path") or "").strip()

        if preferred_kind or query:
            return "WINDOW_CLOSE", {
                "query": query,
                "preferred_kind": preferred_kind,
                "close_scope": close_scope,
                "title_candidates": self._build_window_title_candidates(active, query),
            }

        return None

    @staticmethod
    def _extract_close_candidate(lower: str, params: dict) -> str:
        param_candidate = str(
            params.get("target")
            or params.get("query")
            or params.get("title")
            or params.get("path")
            or params.get("name")
            or params.get("app_name")
            or ""
        ).strip().lower()
        if param_candidate:
            # Si la cible est le navigateur lui-même mais une cible fine existe, garder la cible fine.
            browser_names = {"chrome", "google chrome", "firefox", "edge", "microsoft edge", "opera", "brave"}
            if param_candidate in browser_names and params.get("target"):
                param_candidate = str(params.get("target") or "").strip().lower()
            return param_candidate

        candidate = re.sub(r"^.*?(ferme|fermer|close|quitte|arrete|arrête)\s+", "", lower).strip()
        candidate = re.sub(r"^(le|la|les)\s+", "", candidate).strip()
        candidate = re.sub(r"^(fichier|dossier|document)\s+", "", candidate).strip()
        candidate = re.sub(r"^(l')?(onglet|tab|page)s?\s+(de\s+chrome\s+)?", "", candidate).strip()
        candidate = re.sub(r"^(moi|m\'|moi\s+)\s*", "", candidate).strip()
        candidate = re.sub(r"^celle\s+sur\s+", "", candidate).strip()
        candidate = re.sub(r"^celui\s+sur\s+", "", candidate).strip()
        candidate = re.sub(r"^qui\s+(?:est|n\'?est)?\s*sur\s+", "", candidate).strip()
        return candidate

    @staticmethod
    def _infer_close_scope(lower: str, candidate: str) -> str | None:
        text = f"{lower} {candidate}".lower()
        if any(token in text for token in ["onglet", "tab", "page"]):
            return "tab"
        if any(token in text for token in ["fenetre", "fenêtre", "window"]):
            return "window"
        return None

    @staticmethod
    def _extract_tab_or_page_target(lower: str) -> str:
        mention_match = re.search(
            r"(?:onglet|tab|page)\s+(?:de\s+chrome\s+)?(?:qui\s+(?:est|n\'?est)?\s*)?sur\s+([a-z0-9\s\-_'’\.]+)$",
            lower,
            re.IGNORECASE,
        )
        if not mention_match:
            return ""
        return mention_match.group(1).strip(" .,!?:;\"'")

    @staticmethod
    def _has_close_verb(lower: str) -> bool:
        return any(token in lower for token in [" ferme ", " fermer ", "close", "quitte", "arrête", "arrete"])

    @staticmethod
    def _matches_recent_item(candidate: str, recent: dict) -> bool:
        normalized = candidate.strip().lower()
        if not normalized:
            return True

        recent_name = str(recent.get("name", "")).lower()
        recent_stem = str(recent.get("stem", "")).lower()
        recent_path = str(recent.get("path", "")).lower().replace("\\", "/")
        return any([
            normalized in recent_name,
            normalized in recent_stem,
            normalized in recent_path,
            recent_stem and recent_stem in normalized,
        ])

    @staticmethod
    def _build_close_params(recent: dict) -> dict:
        return {
            "path": recent.get("path", ""),
            "current_dir": recent.get("parent"),
            "window_title": recent.get("name") or recent.get("stem") or recent.get("path", ""),
        }

    @staticmethod
    def _build_window_title_candidates(active: dict, query: str) -> list[str]:
        candidates = []
        for value in [query, active.get("title"), active.get("name"), active.get("path")]:
            text = str(value or "").strip()
            if text and text not in candidates:
                candidates.append(text)
                try:
                    path = Path(text)
                    if path.name and path.name not in candidates:
                        candidates.append(path.name)
                    if path.stem and path.stem not in candidates:
                        candidates.append(path.stem)
                except OSError:
                    continue
        return candidates

    def _infer_window_kind_from_close_candidate(self, candidate: str, active: dict) -> str | None:
        normalized = str(candidate or "").strip().lower()
        if not normalized:
            return active.get("kind")

        if any(token in normalized for token in ["video", "vidéo", "film", "vlc", ".mp4", ".mkv", ".avi", ".mp3"]):
            return "media"
        if any(token in normalized for token in ["navigateur", "browser", "chrome", "edge", "firefox", "opera", "brave", "onglet"]):
            return "browser"
        if any(token in normalized for token in ["document", "pdf", "word", "excel", ".doc", ".docx", ".pdf", ".txt"]):
            return "document"
        if any(token in normalized for token in ["dossier", "explorateur", "explorer", "répertoire", "repertoire"]):
            return "folder"
        return active.get("kind") if normalized in self._generic_close_terms() else None

    @staticmethod
    def _mentions_browser(lower: str) -> bool:
        return any(token in lower for token in [
            "chrome", "firefox", "edge", "opera", "brave", "navigateur", "browser", "onglet", "page ouverte",
        ])

    @staticmethod
    def _generic_close_terms() -> set[str]:
        return {
            "", "la", "le", "les", "ca", "ça", "cela", "celle", "celui",
            "fenetre", "fenêtre", "window", "app", "application", "programme",
        }

    @staticmethod
    def _is_browser_app(app_name: str) -> bool:
        normalized = str(app_name or "").strip().lower()
        return normalized in {
            "chrome", "google chrome", "firefox", "edge", "microsoft edge",
            "opera", "brave", "browser", "navigateur",
        }

    @staticmethod
    def _looks_like_document(path: str) -> bool:
        return Path(path).suffix.lower() in {
            ".txt", ".md", ".pdf", ".doc", ".docx", ".rtf", ".odt",
            ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
        }

    @staticmethod
    def _looks_like_media(path: str) -> bool:
        return Path(path).suffix.lower() in {
            ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
            ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
        }

    def _classify_path_kind(self, path: str) -> str:
        if self._looks_like_document(path):
            return "document"
        if self._looks_like_media(path):
            return "media"
        p = Path(path)
        return "folder" if p.suffix == "" and ("\\" in path or "/" in path) else "file"

    def _classify_app_kind(self, app_name: str) -> str:
        normalized = str(app_name or "").strip().lower()
        if self._is_browser_app(normalized):
            return "browser"
        if normalized in {"vlc", "media player", "groove", "spotify"}:
            return "media"
        if normalized in {"word", "microsoft word", "excel", "microsoft excel", "powerpoint", "onenote", "notepad", "bloc-notes"}:
            return "document"
        return "app"

    @staticmethod
    def _is_search_like_request(lower: str) -> bool:
        prefixes = (
            "recherche ", "cherche ", "trouve ", "search ", "google ",
            "cherche-moi ", "cherche moi ", "trouve-moi ", "trouve moi ",
        )
        return lower.startswith(prefixes)

    @staticmethod
    def _is_summary_like_request(lower: str) -> bool:
        return lower.startswith(("resume", "résume", "fais un resume", "fais un résumé", "summarize", "summary"))

    @staticmethod
    def _is_document_read_request(lower: str) -> bool:
        return lower.startswith(("lis", "read", "relis", "ouvre le document", "affiche le document"))

    @staticmethod
    def _is_explicit_file_request(lower: str) -> bool:
        file_hints = [
            "fichier", "fichiers", "dossier", "dossiers", "document", "documents",
            "repertoire", "répertoire", "dans mes", "sur le disque", "dans le disque",
            "dans ce dossier", "dans ce repertoire", "dans ce répertoire",
        ]
        if any(hint in lower for hint in file_hints):
            return True
        return bool(re.search(r"\.[a-z0-9]{2,4}\b", lower))

    def _extract_search_query(self, lower: str, params: dict) -> str:
        query = str(params.get("query") or params.get("keyword") or "").strip()
        if query and not self._is_explicit_file_request(query.lower()):
            return query

        extracted = self._extract_target(
            lower,
            [
                "recherche ", "cherche ", "trouve ", "search ",
                "cherche-moi ", "cherche moi ", "trouve-moi ", "trouve moi ",
            ],
        ).strip()
        return extracted

    def start(self):
        """Démarre l'agent en mode terminal interactif."""
        self.running = True
        groq_status = "✅ Groq (LLaMA 3.3 70B)" if self.parser.ai_available else "⚡ Mode offline (keywords)"
        logger.info("Agent démarré — mode terminal interactif.")
        print("\n" + "=" * 58)
        print("   🤖  JARVIS WINDOWS — Agent IA")
        print(f"   {groq_status}")
        print("=" * 58)
        print("   Parlez naturellement en français ou anglais.")
        print("   Tapez 'aide' pour voir les commandes | 'quitter' pour sortir.\n")

        while self.running:
            try:
                command = input("🎤 Jarvis > ").strip()
                if not command:
                    continue
                if command.lower() in ["quitter", "exit", "quit", "q"]:
                    self.stop()
                    break

                result     = self.handle_command(command)
                intent     = result.get("_intent", "")
                confidence = result.get("_confidence", 0.0)
                source     = result.get("_source", "")
                status     = "✅" if result.get("success") else "❌"

                print(f"\n{status} {result.get('message', '')}")

                # Afficher le tableau SEULEMENT si demande explicite (flag _show_display)
                should_show_display = result.get("_show_display", False)
                data = result.get("data") or {}
                if should_show_display and isinstance(data, dict) and "display" in data:
                    print(data["display"])

                if intent and intent not in ("UNKNOWN", "FOLLOWUP"):
                    src_icon = "🤖" if source in ("groq", "azure") else "⚡"
                    print(f"\n   {src_icon} [{intent}] conf={confidence:.0%}")

                print()

            except KeyboardInterrupt:
                self.stop()
                break

    def stop(self):
        self.running = False
        logger.info("Agent arrêté.")
        print("\n👋 Jarvis arrêté. À bientôt !")

    def _enrich(self, result: dict, intent: str, confidence: float, source: str) -> dict:
        result = dict(result)
        result["_intent"]     = intent
        result["_confidence"] = round(confidence, 2)
        result["_source"]     = source
        return result

    @staticmethod
    def _response(success: bool, message: str, data=None) -> dict:
        return {"success": success, "message": message, "data": data}

    @staticmethod
    def _extract_delay(command: str, default: int = 10) -> int:
        import re
        match = re.search(r"(\d+)\s*(seconde|sec|s\b|minute|min)", command)
        if match:
            val, unit = int(match.group(1)), match.group(2)
            return val * 60 if "min" in unit else val
        return default

    @staticmethod
    def _extract_target(command: str, keywords: list) -> str:
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in command:
                after = command.split(kw, 1)[1].strip()
                if after:
                    return after
        return ""