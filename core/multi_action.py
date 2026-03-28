"""
core/multi_action.py — Multi-Action Loop pour TONY STARK V2
=============================================================

Agent autonome : Groq peut retourner plusieurs tool_calls à exécuter en séquence.
Chaque résultat alimente le contexte pour la décision suivante.

Exemple :
  Utilisateur : "Ouvre Chrome, cherche Python, puis joue une musique de travail"
  
  Groq retourne [
    {"name": "APP_OPEN", "arguments": {"app_name": "chrome"}},
    {"name": "BROWSER_SEARCH", "arguments": {"query": "Python"}},
    {"name": "MUSIC_PLAYLIST_PLAY", "arguments": {"name": "ma playlist de travail"}}
  ]
  
  Agent exécute en séquence :
    1. Ouvre Chrome
    2. Cherche Python 
    3. Joue la musique
    
  Chaque résultat → mémoire context → réponse finale agrégée
"""

from typing import List, Dict, Any
from config.logger import get_logger

logger = get_logger(__name__)


class MultiActionExecutor:
    """Exécute plusieurs actions en séquence et agrège les résultats."""

    def __init__(self, executor, agent):
        """
        Args:
            executor: IntentExecutor
            agent: Agent instance
        """
        self.executor = executor
        self.agent = agent

    def execute_tool_calls(self, tool_calls: List[Dict[str, Any]], max_steps: int = 4) -> Dict[str, Any]:
        """
        Exécute une liste de tool_calls en séquence.
        
        Args:
            tool_calls: Liste de tool_calls au format Groq
            max_steps: Nombre max d'étapes (sécurité contre boucles infinies)
        
        Returns:
            Résultat agrégé avec message final et résultats intermédiaires
        """
        if not tool_calls or len(tool_calls) == 0:
            return {
                "success": False,
                "message": "Aucune action à exécuter.",
                "steps": [],
                "final_step": 0,
            }

        steps = []
        final_intent = "UNKNOWN"
        final_message = ""
        
        # Limiter le nombre d'étapes
        tool_calls_to_process = tool_calls[:max_steps]
        
        for idx, tool_call in enumerate(tool_calls_to_process):
            logger.info(f"Multi-Action Step {idx + 1}/{len(tool_calls_to_process)}")
            
            # Parser le tool_call
            try:
                from core.tool_schema import ToolCallExtractor
                intent, params = ToolCallExtractor.parse_tool_call(tool_call)
            except Exception as e:
                logger.error(f"Erreur parsing tool_call : {e}")
                steps.append({
                    "step": idx + 1,
                    "intent": "UNKNOWN",
                    "success": False,
                    "error": str(e),
                })
                continue
            
            # Exécuter l'action
            try:
                result = self.executor.execute(intent, params, agent=self.agent)
                if not isinstance(result, dict):
                    result = {"success": bool(result), "message": str(result)}
            except Exception as e:
                logger.error(f"Erreur exécution step {idx + 1} : {e}")
                result = {"success": False, "message": str(e)}
            
            # Mémoriser l'étape
            step_summary = {
                "step": idx + 1,
                "intent": intent,
                "params": params,
                "success": result.get("success", False),
                "message": result.get("message", ""),
            }
            steps.append(step_summary)
            
            # Mémoriser dans le contexte pour les décisions suivantes
            if self.agent and hasattr(self.agent, "context"):
                self.agent.context.remember(intent.lower(), {
                    "action_step": idx + 1,
                    "intent": intent,
                    "success": result.get("success", False),
                    "result": result.get("message", ""),
                })
            
            # Garder en tête la dernière action
            final_intent = intent
            final_message = result.get("message", "")
            
            # Si une étape échoue, s'arrêter (sauf si c'est pas critique)
            if not result.get("success", False) and idx < len(tool_calls_to_process) - 1:
                logger.warning(f"Step {idx + 1} a échoué, arrêt de la séquence.")
                break
        
        # Résultat agrégé
        all_success = all(s.get("success", False) for s in steps)
        
        return {
            "success": all_success,
            "message": final_message,
            "intent": final_intent,
            "steps": steps,
            "step_count": len(steps),
            "final_step": len(steps),
            "data": {
                "multi_action": True,
                "steps": steps,
            }
        }

    def format_multi_action_response(self, result: Dict[str, Any]) -> str:
        """
        Formatte la réponse pour une séquence multi-action.
        
        Exemples :
          - 1 action : "Chrome est lancé."
          - 2 actions : "Chrome ouvert, recherche lancée."
          - 3+ actions : "3 actions exécutées : Chrome, recherche web, musique lancée."
        """
        steps = result.get("steps", [])
        if not steps:
            return result.get("message", "Aucune action complétée.")

        if len(steps) == 1:
            # Une seule action → réponse simple
            return result.get("message", "Action complétée.")

        # Plusieurs actions → récapitulatif
        action_names = []
        for step in steps:
            intent = step.get("intent", "?").replace("_", " ").lower()
            action_names.append(intent)

        if len(steps) <= 3:
            actions_str = ", ".join(action_names)
            return f"{actions_str} — c'est fait."
        else:
            actions_str = ", ".join(action_names[:3])
            return f"{len(steps)} actions lancées ({actions_str}...)."
