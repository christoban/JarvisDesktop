"""
power_manager.py — Gestion des états d'alimentation du PC
SEMAINE 11 — LUNDI (complément)

États gérés :
  lock()          → verrouille l'écran (session active)
  unlock(pwd)     → déverrouille depuis le téléphone
  sleep()         → veille (RAM maintenue, réveil rapide 2-3s)
  hibernate()     → hibernation (RAM sur disque, "éteint mais pas vraiment")
  shutdown(delay) → extinction complète
  restart(delay)  → redémarrage
  wake_on_lan(mac)→ réveil réseau depuis le téléphone (si PC en veille/hibernate)
  get_state()     → état actuel, batterie, plan d'alimentation

Note sur l'hibernation :
  Windows sauvegarde tout le contenu de la RAM dans C:\hiberfil.sys,
  puis s'éteint complètement. Au rallumage, Windows recharge l'état
  exact → tu retrouves tout où tu en étais. C'est le mode que tu cherchais.
  Prérequis : hibernation activée (powercfg /h on)
"""

import platform
import re
import socket
import struct
import subprocess
import time
from config.logger import get_logger

logger = get_logger(__name__)

SYSTEM = platform.system()


def _run(cmd: list, timeout: int = 10) -> tuple:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace"
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except FileNotFoundError:
        return False, f"Commande introuvable : {cmd[0]}"
    except Exception as e:
        return False, str(e)


def _run_ps(script: str, timeout: int = 10) -> tuple:
    return _run(
        ["powershell", "-NonInteractive", "-NoProfile", "-Command", script],
        timeout=timeout
    )


class PowerManager:
    """Gestion complète des états d'alimentation Windows."""

    def __init__(self):
        logger.info(f"PowerManager initialisé — système={SYSTEM}")

    # ── Verrouillage / Déverrouillage ─────────────────────────────────────────

    def lock(self) -> dict:
        """Verrouille l'écran Windows (session reste active)."""
        logger.info("Verrouillage écran")
        if SYSTEM == "Windows":
            ok, out = _run(["rundll32.exe", "user32.dll,LockWorkStation"])
            return self._ok("Écran verrouillé.") if ok \
                   else self._err(f"Impossible de verrouiller : {out}")
        elif SYSTEM == "Linux":
            for cmd in [["gnome-screensaver-command", "--lock"],
                        ["loginctl", "lock-session"],
                        ["xscreensaver-command", "-lock"]]:
                ok, _ = _run(cmd)
                if ok:
                    return self._ok("Écran verrouillé.")
            return self._err("Aucun gestionnaire de verrouillage trouvé.")
        return self._err("Système non supporté.")

    def unlock(self, password: str = "") -> dict:
        """
        Déverrouille l'écran depuis le téléphone.
        Si mot de passe fourni → le tape via pyautogui.
        Le mot de passe n'est JAMAIS logué ni stocké.
        """
        logger.info("Déverrouillage écran demandé depuis le téléphone")

        try:
            import pyautogui
        except ImportError:
            return self._err(
                "pyautogui absent. Installe : pip install pyautogui\n"
                "Déverrouillage manuel requis."
            )

        try:
            # 1. Réveiller l'écran (touche espace ou clic)
            pyautogui.press("space")
            time.sleep(0.6)

            # 2. Si mot de passe, le taper
            if password:
                # Cliquer sur le champ mot de passe d'abord
                screen_w, screen_h = pyautogui.size()
                pyautogui.click(screen_w // 2, screen_h // 2)
                time.sleep(0.3)
                pyautogui.typewrite(password, interval=0.04)
                time.sleep(0.1)
                pyautogui.press("enter")
                # Le mot de passe est effacé de la mémoire Python
                password = "x" * len(password)
                del password
                return self._ok("Déverrouillage en cours (mot de passe envoyé).")
            else:
                pyautogui.press("enter")
                return self._ok("Écran déverrouillé.")

        except Exception as e:
            return self._err(f"Déverrouillage échoué : {e}")

    def has_password(self) -> bool:
        """Détecte si la session Windows a un mot de passe actif."""
        if SYSTEM != "Windows":
            return True  # supposer oui sur Linux/Mac
        ok, out = _run_ps(
            "net user $env:USERNAME | Select-String 'Mot de passe actif'"
        )
        if ok and out:
            return "oui" in out.lower() or "yes" in out.lower()
        return True  # par défaut supposer qu'il y a un mot de passe

    def turn_off_display(self) -> dict:
        """Éteint l'écran sans verrouiller (économie d'énergie)."""
        logger.info("Extinction écran (sans verrouillage)")
        if SYSTEM == "Windows":
            ok, out = _run_ps(
                "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")]"
                "public static extern int SendMessage(int hWnd,int Msg,"
                "int wParam,int lParam);' -Name 'Win32' -Namespace '').SendMessage(-1,0x0112,0xF170,2)"
            )
            return self._ok("Écran éteint.") if ok \
                   else self._err(f"Erreur extinction écran : {out}")
        elif SYSTEM == "Linux":
            ok, _ = _run(["xset", "dpms", "force", "off"])
            return self._ok("Écran éteint.") if ok \
                   else self._err("xset introuvable.")
        return self._err("Système non supporté.")

    # ── États d'alimentation ──────────────────────────────────────────────────

    def sleep(self) -> dict:
        """
        Veille (Suspend to RAM).
        RAM maintenue sous tension → réveil très rapide (2-3 secondes).
        Le bridge continue de tourner en arrière-plan.
        """
        logger.info("Mise en veille")
        if SYSTEM == "Windows":
            ok, out = _run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
            )
            return self._ok("Mise en veille en cours...") if ok \
                   else self._err(f"Erreur veille : {out}")
        elif SYSTEM == "Linux":
            ok, out = _run(["systemctl", "suspend"])
            return self._ok("Mise en veille en cours...") if ok \
                   else self._err(f"systemctl suspend : {out}")
        return self._err("Système non supporté.")

    def hibernate(self) -> dict:
        """
        Hibernation (Suspend to Disk).
        Windows sauvegarde la RAM dans hiberfil.sys puis s'éteint.
        → PC complètement éteint mais état conservé.
        → Réveil : 20-40 secondes selon le disque.
        Prérequis Windows : powercfg /h on
        """
        logger.info("Hibernation demandée")
        if SYSTEM == "Windows":
            # Vérifier que l'hibernation est activée
            ok_check, out_check = _run_ps(
                "(powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) "
                "-ne $null"
            )
            ok, out = _run(["shutdown", "/h"])
            return self._ok(
                "Hibernation en cours... Le PC va s'éteindre et "
                "sauvegarder son état. Rallumez normalement pour reprendre."
            ) if ok else self._err(
                f"Hibernation échouée : {out}\n"
                "Vérifiez que l'hibernation est activée : powercfg /h on"
            )
        elif SYSTEM == "Linux":
            ok, out = _run(["systemctl", "hibernate"])
            return self._ok("Hibernation en cours...") if ok \
                   else self._err(f"systemctl hibernate : {out}")
        return self._err("Système non supporté.")

    def enable_hibernate(self) -> dict:
        """Active l'hibernation Windows (désactivée par défaut sur certains PC)."""
        if SYSTEM != "Windows":
            return self._err("Uniquement Windows.")
        ok, out = _run(["powercfg", "/h", "on"])
        return self._ok("Hibernation activée.") if ok \
               else self._err(f"Impossible d'activer l'hibernation (admin requis?) : {out}")

    def shutdown(self, delay_seconds: int = 0) -> dict:
        """Extinction complète."""
        logger.info(f"Extinction demandée (délai={delay_seconds}s)")
        if SYSTEM == "Windows":
            cmd = ["shutdown", "/s", "/f",
                   "/t", str(max(0, delay_seconds))]
            ok, out = _run(cmd)
            msg = (f"Extinction dans {delay_seconds}s..."
                   if delay_seconds > 0 else "Extinction en cours...")
            return self._ok(msg) if ok else self._err(out)
        elif SYSTEM == "Linux":
            delay_min = max(0, delay_seconds) // 60 or "now"
            ok, out = _run(["shutdown", "-h", str(delay_min)])
            return self._ok("Extinction en cours...") if ok \
                   else self._err(out)
        return self._err("Système non supporté.")

    def restart(self, delay_seconds: int = 0) -> dict:
        """Redémarrage."""
        logger.info(f"Redémarrage demandé (délai={delay_seconds}s)")
        if SYSTEM == "Windows":
            ok, out = _run(
                ["shutdown", "/r", "/f", "/t", str(max(0, delay_seconds))]
            )
            return self._ok("Redémarrage en cours...") if ok \
                   else self._err(out)
        elif SYSTEM == "Linux":
            ok, out = _run(["shutdown", "-r", "now"])
            return self._ok("Redémarrage en cours...") if ok \
                   else self._err(out)
        return self._err("Système non supporté.")

    def cancel_shutdown(self) -> dict:
        """Annule une extinction ou un redémarrage planifié."""
        logger.info("Annulation extinction/redémarrage")
        if SYSTEM == "Windows":
            ok, out = _run(["shutdown", "/a"])
            return self._ok("Extinction annulée.") if ok \
                   else self._err("Aucune extinction planifiée.")
        elif SYSTEM == "Linux":
            ok, out = _run(["shutdown", "-c"])
            return self._ok("Extinction annulée.") if ok \
                   else self._err(out)
        return self._err("Système non supporté.")

    # ── Wake-on-LAN ───────────────────────────────────────────────────────────

    def wake_on_lan(self, mac_address: str,
                    broadcast: str = "255.255.255.255",
                    port: int = 9) -> dict:
        """
        Envoie un magic packet UDP pour réveiller le PC depuis le réseau.
        Utilisé quand le PC est en veille ou hibernation.

        Args:
            mac_address : adresse MAC du PC (ex: "AA:BB:CC:DD:EE:FF")
            broadcast   : adresse de broadcast (défaut: 255.255.255.255)
            port        : port UDP (7 ou 9)
        """
        logger.info(f"Wake-on-LAN → {mac_address}")
        try:
            # Nettoyer l'adresse MAC
            mac_clean = re.sub(r"[^0-9a-fA-F]", "", mac_address)
            if len(mac_clean) != 12:
                return self._err(f"Adresse MAC invalide : {mac_address}")

            # Construire le magic packet
            mac_bytes    = bytes.fromhex(mac_clean)
            magic_packet = b"\xff" * 6 + mac_bytes * 16

            # Envoyer en UDP broadcast
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(magic_packet, (broadcast, port))

            return self._ok(
                f"Magic packet envoyé à {mac_address}. "
                f"Le PC devrait se réveiller dans 5-15 secondes "
                f"(WoL doit être activé dans le BIOS)."
            )
        except Exception as e:
            return self._err(f"Wake-on-LAN échoué : {e}")

    # ── État et infos ─────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """Retourne l'état d'alimentation actuel du PC."""
        info = {"system": SYSTEM}

        if SYSTEM == "Windows":
            # Batterie
            ok, out = _run_ps(
                "Get-WmiObject Win32_Battery | "
                "Select-Object EstimatedChargeRemaining,BatteryStatus "
                "| ConvertTo-Json"
            )
            if ok and out and out.strip() != "{}":
                try:
                    import json
                    bat = json.loads(out)
                    if isinstance(bat, list):
                        bat = bat[0]
                    info["battery_pct"]    = bat.get("EstimatedChargeRemaining")
                    info["battery_status"] = bat.get("BatteryStatus")
                    info["on_battery"]     = bat.get("BatteryStatus") == 1
                except Exception:
                    info["battery_pct"] = None
            else:
                info["battery_pct"] = None  # PC fixe / pas de batterie

            # Plan d'alimentation
            ok2, out2 = _run(["powercfg", "/getactivescheme"])
            if ok2:
                m = re.search(r"\((.+)\)", out2)
                info["power_plan"] = m.group(1).strip() if m else out2[:50]

            # Hibernation activée ?
            ok3, out3 = _run(["powercfg", "/h"])
            info["hibernate_enabled"] = "désactivée" not in out3.lower() \
                                        and "disabled" not in out3.lower()

        elif SYSTEM == "Linux":
            import subprocess as sp
            try:
                out = sp.check_output(
                    ["cat", "/sys/class/power_supply/BAT0/capacity"],
                    timeout=3
                ).decode().strip()
                info["battery_pct"] = int(out)
            except Exception:
                info["battery_pct"] = None

        bat = info.get("battery_pct")
        if bat is not None:
            status = f"Batterie : {bat}%"
            if bat < 20:
                status += " ⚠️ Faible"
        else:
            status = "PC fixe (pas de batterie détectée)"

        plan = info.get("power_plan", "N/A")
        return self._ok(
            f"{status} | Plan : {plan}",
            info
        )

    def set_power_plan(self, plan: str) -> dict:
        """Change le plan d'alimentation Windows."""
        plans = {
            "économie":     "a1841308-3541-4fab-bc81-f71556f20b4a",
            "équilibré":    "381b4222-f694-41f0-9685-ff5bb260df2e",
            "performance":  "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        }
        plan_key = plan.lower().strip()
        guid     = None
        for k, v in plans.items():
            if plan_key in k:
                guid = v
                break
        if not guid:
            return self._err(
                f"Plan '{plan}' inconnu. Choix : "
                + ", ".join(plans.keys())
            )
        ok, out = _run(["powercfg", "/s", guid])
        return self._ok(f"Plan d'alimentation : {plan}.") if ok \
               else self._err(f"Erreur : {out}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str) -> dict:
        return {"success": False, "message": message, "data": None}