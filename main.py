# Jarvis Desktop — Assistant Intelligent de Contrôle PC
# Auteur : christoban
# Description : Point d'entrée principal de l'application Jarvis Windows.
#               Lance l'agent en mode interactif (terminal) et, à terme,
#               le client WebSocket vers Azure (semaine 6).

"""
main.py — Point d'entrée de l'agent Jarvis Windows
Lance l'agent en mode interactif (terminal).
En semaine 6 : lancera aussi le WebSocket vers Azure.
"""

import sys
from pathlib import Path

# Ajouter le dossier racine au PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import check_config
from config.logger import get_logger
from core.agent import Agent

logger = get_logger("main")


def main():
    print("""
╔══════════════════════════════════════════════════╗
║          🤖  JARVIS WINDOWS  v0.1.0              ║
║     Assistant Intelligent de Contrôle PC         ║
╚══════════════════════════════════════════════════╝
    """)

    # Vérification configuration
    config_ok = check_config()
    if not config_ok:
        print("⚠️  Certaines clés Azure sont manquantes.")
        print("   → Remplis le fichier config/.env")
        print("   → Le mode terminal fonctionne quand même.\n")

    # Démarrage de l'agent
    agent = Agent()

    # TODO Semaine 6 : lancer aussi le WebSocket en parallèle
    # from communication.websocket_client import WebSocketClient
    # ws = WebSocketClient(agent)
    # ws.start()  # dans un thread séparé

    # Mode terminal interactif (actif dès maintenant)
    agent.start()


if __name__ == "__main__":
    main()