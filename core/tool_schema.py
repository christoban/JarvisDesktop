"""
core/tool_schema.py — Définition des Tools JSON Schema pour Groq
===================================================================

Convertit le catalogue INTENTS en schemas JSON natifs que Groq peut appeler
directement via tool_calls, plutôt que de parser du JSON textuel.

Format : https://platform.openai.com/docs/guides/function-calling
Groq supporte le même format pour une meilleure stabilité et précision.

Exemple :
  {
    "type": "function",
    "function": {
      "name": "APP_OPEN",
      "description": "Ouvrir une application",
      "parameters": {
        "type": "object",
        "properties": {
          "app_name": {"type": "string", "description": "Nom de l'app"},
          "args": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["app_name"]
      }
    }
  }
"""

from typing import Any, Dict, List
import json

def build_tool_schemas() -> List[Dict[str, Any]]:
    """
    Construit la liste complète des tool schemas à partir de INTENTS.
    """
    from core.command_parser import INTENTS
    
    tools = []
    
    for intent_name, intent_config in INTENTS.items():
        tool = {
            "type": "function",
            "function": {
                "name": intent_name,
                "description": intent_config.get("desc", ""),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
        
        # Construire les propriétés depuis les params
        params = intent_config.get("params", {})
        for param_name, param_type_str in params.items():
            # Parser le type (ex: "str", "int", "list optionnel", etc.)
            param_type_str_lower = str(param_type_str).lower().strip()
            is_optional = "optionnel" in param_type_str_lower or "optional" in param_type_str_lower
            
            # Déterminer le type JSON Schema
            json_type = "string"  # Default
            if "int" in param_type_str_lower:
                json_type = "integer"
            elif "float" in param_type_str_lower or "number" in param_type_str_lower:
                json_type = "number"
            elif "bool" in param_type_str_lower:
                json_type = "boolean"
            elif "list" in param_type_str_lower or "array" in param_type_str_lower:
                json_type = "array"
            
            prop = {
                "type": json_type,
                "description": f"Parameter {param_name}"
            }
            
            # Pour les arrays, préciser le type des items
            if json_type == "array":
                if "int" in param_type_str_lower:
                    prop["items"] = {"type": "integer"}
                else:
                    prop["items"] = {"type": "string"}
            
            tool["function"]["parameters"]["properties"][param_name] = prop
            
            # Ajouter aux required si pas optionnel
            if not is_optional:
                tool["function"]["parameters"]["required"].append(param_name)
        
        tools.append(tool)
    
    return tools


def get_tool_schemas_for_groq() -> List[Dict[str, Any]]:
    """Retourne la liste des tools pour Groq.

    Groq impose une limite stricte de 128 outils.
    Nous utilisons le sous-ensemble critique défini dans command_parser.
    """
    from core.command_parser import convert_intents_to_tools

    tools = convert_intents_to_tools(enabled=True)
    max_tools = 120
    if len(tools) > max_tools:
        tools = tools[:max_tools]
    return tools


def format_tools_for_system_prompt(tools: List[Dict[str, Any]]) -> str:
    """
    Formate les tools pour le system prompt.
    """
    lines = ["Tu as accès aux tools suivants:\n"]
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "UNKNOWN")
        desc = func.get("description", "")
        lines.append(f"- {name}: {desc}")
    
    return "\n".join(lines)


class ToolCallExtractor:
    """
    Extrait et valide les tool_calls depuis la réponse de Groq.
    """
    
    @staticmethod
    def extract_from_response(response: Any) -> List[Dict[str, Any]]:
        """
        Extrait les tool_calls du message de réponse Groq.
        
        Groq retourne un objet avec:
          - message.tool_calls si tool_calling est activé
          - message.content sinon
        """
        tool_calls = []
        
        # Si response a tool_calls, c'est nouvellement commandé
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tc in response.tool_calls:
                tool_calls.append({
                    "id": tc.id if hasattr(tc, 'id') else "",
                    "type": "function",
                    "function": {
                        "name": tc.function.name if hasattr(tc.function, 'name') else "",
                        "arguments": tc.function.arguments if hasattr(tc.function, 'arguments') else "{}"
                    }
                })
        
        return tool_calls
    
    @staticmethod
    def parse_tool_call(tool_call: Dict[str, Any]) -> tuple[str, dict]:
        """
        Parse un tool_call et retourne (intent, params).
        
        Returns:
            (intent_name, params_dict)
        """
        if tool_call.get("type") != "function":
            raise ValueError("Unsupported tool_call type")
        
        func = tool_call.get("function", {})
        intent = func.get("name", "UNKNOWN")
        
        # Parser les arguments (peuvent être JSON string ou dict)
        args_str = func.get("arguments", "{}")
        if isinstance(args_str, str):
            try:
                params = json.loads(args_str)
            except json.JSONDecodeError:
                params = {}
        else:
            params = args_str if isinstance(args_str, dict) else {}
        
        return intent, params
