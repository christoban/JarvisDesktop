"""
core/sensory.py — Contexte sensoriel de Jarvis
===================================================

Avant chaque commande, Jarvis reçoit un rapport en temps réel sur :
  - État du système (CPU, RAM, température)
  - Fenêtre active actuellement
  - Applications ouvertes
  - État du réseau

Ce contexte est injecté dans le prompt Groq pour une meilleure compréhension
et des décisions plus intelligentes.

Exemple de contexte généré :
  {
    "timestamp": 1711270000,
    "system": {
      "cpu_percent": 15.2,
      "ram_percent": 62.5,
      "ram_used_gb": 8.1,
      "ram_total_gb": 16,
      "temperature_c": 45.0
    },
    "window": {
      "title": "JarvisDesktop - main.py - VS Code",
      "process": "code.exe",
      "class": "Code",
      "focused": True
    },
    "apps": ["chrome.exe", "code.exe", "explorer.exe", "spotify.exe"],
    "network": {
      "ipv4": "192.168.1.100",
      "hostname": "PC-CHRISTIAN",
      "connected": True
    }
  }
"""

import psutil
import platform
from pathlib import Path
from datetime import datetime
from config.logger import get_logger

logger = get_logger(__name__)

class SensoryCapteur:
    """Capture l'état actuel du système PC."""

    @staticmethod
    def get_system_state() -> dict:
        """Retourne CPU, RAM, température."""
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory()
            
            # Température (peut être None sur certains OS)
            temp = None
            try:
                temp = psutil.sensors_temperatures().get("coretemp", [{}])[0].current if psutil.sensors_temperatures() else None
            except:
                pass

            return {
                "cpu_percent": round(cpu, 1),
                "ram_percent": round(ram.percent, 1),
                "ram_used_gb": round(ram.used / (1024**3), 1),
                "ram_total_gb": round(ram.total / (1024**3), 1),
                "temperature_c": round(temp, 1) if temp else None,
                "disk_percent": round(psutil.disk_usage("/").percent, 1),
            }
        except Exception as e:
            logger.warning(f"Erreur capture système : {e}")
            return {}

    @staticmethod
    def get_active_window() -> dict:
        """Retourne la fenêtre active actuellement."""
        try:
            import pygetwindow as gw
            try:
                window = gw.getActiveWindow()
                if window:
                    return {
                        "title": window.title or "Unknown",
                        "process": window.title.split(" - ")[-1] if " - " in window.title else window.title,
                        "focused": True,
                    }
            except Exception:
                pass
            
            # Fallback sur Windows si pygetwindow échoue
            try:
                import ctypes
                MAX_PATH = 256
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                buf = ctypes.create_unicode_buffer(MAX_PATH)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, MAX_PATH)
                title = buf.value
                if title:
                    return {"title": title, "process": title.split(" - ")[-1], "focused": True}
            except:
                pass
                
        except ImportError:
            logger.debug("pygetwindow non installé — pas de capture fenêtre")
        except Exception as e:
            logger.warning(f"Erreur fenêtre active : {e}")
        
        return {"title": "Unknown", "process": "unknown", "focused": False}

    @staticmethod
    def get_running_apps() -> list:
        """Retourne list of processus en cours d'exécution."""
        try:
            apps = set()
            for proc in psutil.process_iter(["name"]):
                try:
                    name = proc.info.get("name", "")
                    if name and not name.startswith("System"):
                        apps.add(name)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return sorted(list(apps))[:50]  # Top 50
        except Exception as e:
            logger.warning(f"Erreur liste apps : {e}")
            return []

    @staticmethod
    def get_network_info() -> dict:
        """Retourne IP, hostname, statut connectivité."""
        try:
            import socket
            hostname = socket.gethostname()
            ipv4 = socket.gethostbyname(hostname)
            
            # Check internet connectivity par ping
            connected = psutil.net_if_stats().get("Ethernet", psutil.net_if_stats().get("Wi-Fi", None))
            
            return {
                "ipv4": ipv4,
                "hostname": hostname,
                "connected": connected is not None and connected.isup if connected else False,
                "platform": platform.system(),
            }
        except Exception as e:
            logger.warning(f"Erreur réseau : {e}")
            return {}

    @staticmethod
    def capture_full_context() -> dict:
        """Capture l'état complet du système — appelé avant chaque commande."""
        return {
            "timestamp": int(datetime.now().timestamp()),
            "system": SensoryCapteur.get_system_state(),
            "window": SensoryCapteur.get_active_window(),
            "apps": SensoryCapteur.get_running_apps(),
            "network": SensoryCapteur.get_network_info(),
        }

    @staticmethod
    def format_for_groq(context: dict) -> str:
        """
        Formate le contexte sensoriel en texte lisible pour Groq.
        """
        if not context:
            return ""
        
        lines = ["=== CONTEXTE SENSORIEL ACTUEL ==="]
        
        # Système
        if ctx_sys := context.get("system"):
            lines.append(f"💻 Système : CPU {ctx_sys.get('cpu_percent')}%, RAM {ctx_sys.get('ram_percent')}% ({ctx_sys.get('ram_used_gb')}GB/{ctx_sys.get('ram_total_gb')}GB)")
            if temp := ctx_sys.get("temperature_c"):
                lines.append(f"🌡️  Température : {temp}°C")
        
        # Fenêtre active
        if window := context.get("window"):
            lines.append(f"🪟 Fenêtre active : {window.get('title')} ({window.get('process')})")
        
        # Apps ouvertes
        if apps := context.get("apps"):
            important = [a for a in apps if any(s in a.lower() for s in ["chrome", "code", "vscode", "spotify", "excel", "word"])]
            if important:
                lines.append(f"📱 Apps importantes ouvertes : {', '.join(important[:5])}")
        
        # Réseau
        if net := context.get("network"):
            lines.append(f"🌐 Réseau : {net.get('ipv4')} ({net.get('hostname')})")
        
        return "\n".join(lines)
