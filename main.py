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
from core.telegram_bot import get_telegram_bot

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

    # Intégration Telegram (start/polling en production)
    telegram_bot = get_telegram_bot()
    if telegram_bot and telegram_bot.is_connected:
        telegram_bot.set_command_handler(lambda text: agent.handle_command(text, source="telegram"))
        telegram_bot.start_polling()
        logger.info("Telegram : daemon poll démarre")
        print("📡 Telegram : démarré (mode commande distante).")
    else:
        logger.info("Telegram : non configuré ou erreur de connexion")

    # TODO Semaine 6 : lancer aussi le WebSocket en parallèle
    # from communication.websocket_client import WebSocketClient
    # ws = WebSocketClient(agent)
    # ws.start()  # dans un thread séparé

    try:
        # Mode terminal interactif (actif dès maintenant)
        agent.start()
    except KeyboardInterrupt:
        logger.info("Interruption clavier reçue, arrêt en cours...")
    finally:
        if telegram_bot:
            telegram_bot.stop_polling()
            logger.info("Telegram : polling arrêté")


if __name__ == "__main__":
    main()