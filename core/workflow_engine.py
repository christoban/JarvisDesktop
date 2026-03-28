"""
core/workflow_engine.py — Moteur de workflows automatisés
SEMAINE 12 — Automatisation multi-apps (niveau 4)

Un workflow = séquence automatique d'actions cross-applications.
Exemple : "postule à cet emploi" →
  1. Ouvre le site d'offre d'emploi
  2. Extrait les infos du poste
  3. Crée le CV针对性 dans Word
  4. Envoie le CV par email

Déclencheur : phrase naturelle ou macro nommé.
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Any, Callable
from config.logger import get_logger
from config.settings import BASE_DIR

logger = get_logger(__name__)

WORKFLOWS_FILE = BASE_DIR / "data" / "workflows.json"


class WorkflowStep:
    """Une étape dans un workflow."""
    def __init__(self, step_type: str, params: dict, description: str = ""):
        self.step_type = step_type  # "browser", "word", "email", "delay", "macro"
        self.params = params
        self.description = description

    def to_dict(self) -> dict:
        return {"type": self.step_type, "params": self.params, "description": self.description}


class WorkflowResult:
    """Résultat d'exécution d'un workflow."""
    def __init__(self, name: str, steps_results: list, total_ms: int):
        self.name = name
        self.steps_results = steps_results
        self.total_ms = total_ms
        self.success = all(r.get("success", False) for r in steps_results) if steps_results else False
        
        ok_count = sum(1 for r in steps_results if r.get("success", False))
        self.message = f"Workflow '{name}' : {ok_count}/{len(steps_results)} étapes réussies ({total_ms}ms)"

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "workflow": self.name,
            "steps": self.steps_results,
            "total_ms": self.total_ms,
            "ok_count": sum(1 for r in self.steps_results if r.get("success", False)),
            "total": len(self.steps_results),
        }


class WorkflowEngine:
    """
    Moteur de workflows automatisés.
    Orchestre les actions entre browser, Word, email, et autres modules.
    """

    def __init__(self, executor=None, browser_control=None, word_manager=None, email_client=None):
        self.executor = executor
        self.browser = browser_control
        self.word = word_manager
        self.email = email_client
        self._workflows = {}
        self._load_workflows()

    def execute_workflow(self, name: str, context: dict = None, agent=None) -> dict:
        """
        Exécute un workflow par son nom.
        
        Args:
            name: Nom du workflow
            context: Variables de contexte (ex: job_url, company_name, etc.)
            agent: Instance Agent pour exécuter les étapes
            
        Returns:
            dict avec success, message, steps, total_ms
        """
        workflow = self._workflows.get(name.lower())
        if not workflow:
            return {"success": False, "message": f"Workflow '{name}' introuvable."}

        steps = workflow.get("steps", [])
        if not steps:
            return {"success": False, "message": f"Workflow '{name}' est vide."}

        context = context or {}
        results = []
        start_time = time.time()

        logger.info(f"Workflow '{name}' — {len(steps)} étape(s)")

        for i, step in enumerate(steps):
            step_type = step.get("type", "")
            params = dict(step.get("params", {}))
            
            # Interpoler les variables dans les params
            params = self._interpolate_params(params, context)
            
            step_desc = step.get("description", f"Étape {i+1}")
            logger.info(f"  [{i+1}/{len(steps)}] {step_type}: {step_desc}")

            result = self._execute_step(step_type, params, agent)
            results.append({
                "step": i + 1,
                "type": step_type,
                "description": step_desc,
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "data": result.get("data", {}),
            })

            if not result.get("success", False) and workflow.get("stop_on_error", False):
                logger.warning(f"Workflow stoppé à l'étape {i+1}")
                break

            # Delay entre les étapes
            delay = step.get("delay_after", 0)
            if delay > 0:
                time.sleep(delay)

        total_ms = int((time.time() - start_time) * 1000)
        res = WorkflowResult(name, results, total_ms)
        logger.info(res.message)
        return res.to_dict()

    def _execute_step(self, step_type: str, params: dict, agent) -> dict:
        """Exécute une étape selon son type."""
        
        if step_type == "browser":
            return self._execute_browser_step(params)
        
        elif step_type == "word":
            return self._execute_word_step(params)
        
        elif step_type == "email":
            return self._execute_email_step(params)
        
        elif step_type == "macro":
            return self._execute_macro_step(params, agent)
        
        elif step_type == "delay":
            delay = params.get("seconds", 1)
            time.sleep(delay)
            return {"success": True, "message": f"Attente de {delay}s"}
        
        elif step_type == "intent":
            # Exécuter un intent directement via l'executor
            if self.executor and agent:
                intent = params.get("intent", "")
                intent_params = params.get("params", {})
                try:
                    result = self.executor.execute(intent, intent_params, agent=agent)
                    return result if isinstance(result, dict) else {"success": bool(result), "message": str(result)}
                except Exception as e:
                    return {"success": False, "message": str(e)}
            return {"success": False, "message": "Executor non disponible"}
        
        else:
            return {"success": False, "message": f"Type d'étape inconnu: {step_type}"}

    def _execute_browser_step(self, params: dict) -> dict:
        """Exécute une étape browser."""
        if not self.browser:
            return {"success": False, "message": "Browser control non disponible"}
        
        action = params.get("action", "")
        
        try:
            if action == "open_url":
                url = params.get("url", "")
                self.browser.navigate(url)
                return {"success": True, "message": f"Ouvert: {url}"}
            
            elif action == "search":
                query = params.get("query", "")
                engine = params.get("engine", "google")
                self.browser.search(query, engine)
                return {"success": True, "message": f"Recherche: {query}"}
            
            elif action == "click_text":
                text = params.get("text", "")
                self.browser.click_text(text)
                return {"success": True, "message": f"Cliqué sur: {text}"}
            
            elif action == "fill":
                field = params.get("field", "")
                value = params.get("value", "")
                self.browser.fill_field(field, value)
                return {"success": True, "message": f"Rempli: {field}"}
            
            elif action == "extract":
                what = params.get("what", "text")
                content = self.browser.get_page_content(what)
                return {"success": True, "message": "Contenu extrait", "data": {"content": content}}
            
            elif action == "screenshot":
                path = params.get("path", "")
                self.browser.take_screenshot(path)
                return {"success": True, "message": "Screenshot pris"}
            
            else:
                return {"success": False, "message": f"Action browser inconnue: {action}"}
                
        except Exception as e:
            return {"success": False, "message": f"Erreur browser: {e}"}

    def _execute_word_step(self, params: dict) -> dict:
        """Exécute une étape Word."""
        if not self.word:
            return {"success": False, "message": "Word manager non disponible"}
        
        action = params.get("action", "")
        
        try:
            if action == "create_cv":
                info = params.get("info", {})
                result = self.word.create_cv(info, open_after=False)
                return result
            
            elif action == "create_document":
                title = params.get("title", "Document")
                sections = params.get("sections", [])
                result = self.word.create_document(title, sections, open_after=False)
                return result
            
            elif action == "export_pdf":
                path = params.get("path", "")
                result = self.word.export_to_pdf(path)
                return result
            
            else:
                return {"success": False, "message": f"Action Word inconnue: {action}"}
                
        except Exception as e:
            return {"success": False, "message": f"Erreur Word: {e}"}

    def _execute_email_step(self, params: dict) -> dict:
        """Exécute une étape email."""
        if not self.email:
            return {"success": False, "message": "Email client non disponible"}
        
        action = params.get("action", "")
        
        try:
            if action == "send":
                to = params.get("to", "")
                subject = params.get("subject", "")
                body = params.get("body", "")
                attachments = params.get("attachments", [])
                result = self.email.send_email(to, subject, body, attachments=attachments)
                return result
            
            elif action == "send_cv":
                to = params.get("to", "")
                cv_path = params.get("cv_path", "")
                subject = params.get("subject", "Candidature")
                body = params.get("body", "Veuillez trouver ci-joint ma candidature.")
                result = self.email.send_email(to, subject, body, attachments=[cv_path])
                return result
            
            else:
                return {"success": False, "message": f"Action email inconnue: {action}"}
                
        except Exception as e:
            return {"success": False, "message": f"Erreur email: {e}"}

    def _execute_macro_step(self, params: dict, agent) -> dict:
        """Exécute une macro existante."""
        if not agent:
            return {"success": False, "message": "Agent non disponible"}
        
        from core.macros import MacroManager
        macro_name = params.get("macro", "")
        
        try:
            mm = MacroManager()
            result = mm.run(macro_name, agent)
            return result
        except Exception as e:
            return {"success": False, "message": f"Erreur macro: {e}"}

    def _interpolate_params(self, params: dict, context: dict) -> dict:
        """Remplace les {{variables}} par les valeurs du contexte."""
        result = {}
        for key, value in params.items():
            if isinstance(value, str) and "{{" in value and "}}" in value:
                for var_name, var_value in context.items():
                    placeholder = f"{{{{{var_name}}}}}"
                    if placeholder in value:
                        value = value.replace(placeholder, str(var_value))
            result[key] = value
        return result

    # ═══════════════════════════════════════════════════════════════════════════
    #  GESTION DES WORKFLOWS
    # ═══════════════════════════════════════════════════════════════════════════

    def register_workflow(self, name: str, steps: list, description: str = "",
                          stop_on_error: bool = True) -> dict:
        """Enregistre un nouveau workflow."""
        name = name.strip().lower()
        if not name:
            return {"success": False, "message": "Nom de workflow vide."}
        if not steps:
            return {"success": False, "message": "Liste d'étapes vide."}

        workflow = {
            "name": name,
            "description": description,
            "steps": steps,
            "stop_on_error": stop_on_error,
            "created_at": int(time.time()),
        }

        self._workflows[name] = workflow
        self._save_workflows()

        return {
            "success": True,
            "message": f"Workflow '{name}' enregistré ({len(steps)} étapes)",
            "workflow": workflow,
        }

    def list_workflows(self) -> dict:
        """Liste tous les workflows."""
        result = []
        for name, wf in self._workflows.items():
            result.append({
                "name": name,
                "description": wf.get("description", ""),
                "steps": len(wf.get("steps", [])),
            })
        
        names = ", ".join(f"'{w['name']}'" for w in result[:5])
        if len(result) > 5:
            names += f" et {len(result) - 5} autre(s)"
        
        return {
            "success": True,
            "message": f"{len(result)} workflow(s) : {names}",
            "data": {"workflows": result, "count": len(result)},
        }

    def get_workflow(self, name: str) -> dict:
        """Retourne un workflow par son nom."""
        wf = self._workflows.get(name.lower())
        if wf:
            return {"success": True, "data": wf}
        return {"success": False, "message": f"Workflow '{name}' introuvable."}

    def delete_workflow(self, name: str) -> dict:
        """Supprime un workflow."""
        name = name.strip().lower()
        if name in self._workflows:
            del self._workflows[name]
            self._save_workflows()
            return {"success": True, "message": f"Workflow '{name}' supprimé."}
        return {"success": False, "message": f"Workflow '{name}' introuvable."}

    # ═══════════════════════════════════════════════════════════════════════════
    #  WORKFLOWS PRÉDÉFINIS
    # ═══════════════════════════════════════════════════════════════════════════

    def _register_default_workflows(self):
        """Enregistre les workflows par défaut."""
        
        # ── Workflow: Postuler à un emploi ─────────────────────────────────────
        self._workflows["postule emploi"] = {
            "description": "Ouvre l'offre, crée le CV针对性, envoie par email",
            "steps": [
                {
                    "type": "browser",
                    "params": {"action": "open_url", "url": "{{job_url}}"},
                    "description": "Ouvrir l'offre d'emploi",
                    "delay_after": 2,
                },
                {
                    "type": "browser",
                    "params": {"action": "extract", "what": "text"},
                    "description": "Extraire les infos du poste",
                    "delay_after": 1,
                },
                {
                    "type": "word",
                    "params": {
                        "action": "create_cv",
                        "info": {
                            "name": "{{candidate_name}}",
                            "title": "{{job_title}}",
                            "email": "{{candidate_email}}",
                            "phone": "{{candidate_phone}}",
                            "summary": "{{candidate_summary}}",
                            "experience": [],
                            "education": [],
                            "skills": {},
                        }
                    },
                    "description": "Créer le CV针对性",
                    "delay_after": 2,
                },
                {
                    "type": "email",
                    "params": {
                        "action": "send_cv",
                        "to": "{{hr_email}}",
                        "subject": "Candidature - {{job_title}} - {{candidate_name}}",
                        "body": "Madame, Monsieur,\n\nJe suis très interessé(e) par le poste de {{job_title}} chez {{company_name}}.\n\nVeuillez trouver ci-joint mon CV.\n\nCordialement,\n{{candidate_name}}",
                        "cv_path": "{{cv_path}}",
                    },
                    "description": "Envoyer le CV par email",
                },
            ],
            "stop_on_error": True,
        }

        # ── Workflow: Recherche d'emploi ────────────────────────────────────────
        self._workflows["recherche emploi"] = {
            "description": "Ouvre plusieurs sites d'offres d'emploi",
            "steps": [
                {
                    "type": "browser",
                    "params": {"action": "open_url", "url": "https://www.linkedin.com/jobs/"},
                    "description": "Ouvrir LinkedIn Jobs",
                    "delay_after": 2,
                },
                {
                    "type": "browser",
                    "params": {"action": "search", "query": "{{job_keywords}}", "engine": "google"},
                    "description": "Rechercher sur Google",
                    "delay_after": 1,
                },
                {
                    "type": "browser",
                    "params": {"action": "new_tab"},
                    "description": "Nouvel onglet",
                    "delay_after": 1,
                },
                {
                    "type": "browser",
                    "params": {"action": "open_url", "url": "https://www.indeed.com"},
                    "description": "Ouvrir Indeed",
                },
            ],
            "stop_on_error": False,
        }

        # ── Workflow: Veille technologique ───────────────────────────────────────
        self._workflows["veille tech"] = {
            "description": "Ouvre les sites tech habituels",
            "steps": [
                {
                    "type": "browser",
                    "params": {"action": "open_url", "url": "https://news.ycombinator.com"},
                    "description": "Hacker News",
                    "delay_after": 1,
                },
                {
                    "type": "browser",
                    "params": {"action": "new_tab"},
                    "description": "Nouvel onglet",
                    "delay_after": 1,
                },
                {
                    "type": "browser",
                    "params": {"action": "open_url", "url": "https://techcrunch.com"},
                    "description": "TechCrunch",
                    "delay_after": 1,
                },
                {
                    "type": "browser",
                    "params": {"action": "new_tab"},
                    "description": "Nouvel onglet",
                    "delay_after": 1,
                },
                {
                    "type": "browser",
                    "params": {"action": "open_url", "url": "https://www.lemonde.fr"},
                    "description": "Le Monde",
                },
            ],
            "stop_on_error": False,
        }

        self._save_workflows()
        logger.info("Workflows par défaut enregistrés")

    # ═══════════════════════════════════════════════════════════════════════════
    #  PERSISTANCE
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_workflows(self):
        """Charge les workflows depuis le fichier."""
        try:
            WORKFLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
            if WORKFLOWS_FILE.exists():
                self._workflows = json.loads(WORKFLOWS_FILE.read_text(encoding="utf-8"))
                logger.info(f"{len(self._workflows)} workflow(s) chargé(s)")
            else:
                self._workflows = {}
                self._register_default_workflows()
        except Exception as e:
            logger.error(f"Erreur chargement workflows: {e}")
            self._workflows = {}
            self._register_default_workflows()

    def _save_workflows(self):
        """Sauvegarde les workflows dans le fichier."""
        try:
            WORKFLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
            WORKFLOWS_FILE.write_text(
                json.dumps(self._workflows, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Erreur sauvegarde workflows: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  ENREGISTREUR DE MACROS (Recording + Replay)
# ═══════════════════════════════════════════════════════════════════════════

class MacroRecorder:
    """
    Enregistre une séquence d'actions pour créer une macro.
    Usage:
        recorder = MacroRecorder()
        recorder.start("ma_macro")
        # ... effectuer des actions ...
        recorder.stop()  # → crée la macro automatiquement
    """

    def __init__(self, macro_manager=None):
        self.macro_manager = macro_manager
        self._recording = False
        self._actions = []
        self._start_time = None

    def start(self, name: str):
        """Démarre l'enregistrement."""
        self._recording = True
        self._actions = []
        self._start_time = time.time()
        logger.info(f"Recording started: {name}")

    def record_action(self, intent: str, params: dict, label: str = ""):
        """Enregistre une action."""
        if not self._recording:
            return
        
        self._actions.append({
            "intent": intent,
            "params": params,
            "label": label or f"{intent}({params})",
        })
        logger.debug(f"Recorded: {intent}")

    def stop(self, name: str = None, description: str = "") -> dict:
        """Arrête l'enregistrement et crée la macro."""
        if not self._recording:
            return {"success": False, "message": "Pas d'enregistrement en cours"}
        
        self._recording = False
        duration = int(time.time() - self._start_time)
        
        if not self._actions:
            return {"success": False, "message": "Aucune action enregistrée"}
        
        if not name:
            name = f"recording_{int(time.time())}"
        
        # Créer la macro avec le MacroManager
        if self.macro_manager:
            result = self.macro_manager.save_macro(
                name=name,
                commands=self._actions,
                description=description or f"Macro enregistrée ({len(self._actions)} actions, {duration}s)",
                delay_between=0.5,
            )
            return result
        
        return {
            "success": True,
            "message": f"Macro '{name}' enregistrée ({len(self._actions)} actions)",
            "data": {"name": name, "actions": self._actions},
        }

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def action_count(self) -> int:
        return len(self._actions)


# ═══════════════════════════════════════════════════════════════════════════
#  FACADE API
# ═══════════════════════════════════════════════════════════════════════════

def create_workflow_engine(executor=None, browser=None, word=None, email_client=None) -> WorkflowEngine:
    """Crée une instance du moteur de workflows."""
    return WorkflowEngine(executor, browser, word, email_client)
