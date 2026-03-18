"""
system_control.py — Contrôle du système Windows
Éteindre, redémarrer, veille, verrou, processus, CPU/RAM, disque, température.

SEMAINE 2 — IMPLÉMENTATION COMPLÈTE
  Lundi   : shutdown, restart, sleep, lock_screen, logout
  Mardi   : list_processes, kill_process, open_task_manager, system_info (CPU/RAM)
  Mercredi: disk_info, temperature_info, full_system_report
"""

import os
import subprocess
import platform
import datetime
import ctypes
import psutil
from config.logger import get_logger

logger = get_logger(__name__)


class SystemControl:
    """
    Contrôle complet du système Windows.
    Toutes les méthodes retournent un dict standard :
        { "success": bool, "message": str, "data": dict | None }
    """

    # ══════════════════════════════════════════════════════════════════════════
    #  LUNDI — Contrôle alimentation
    # ══════════════════════════════════════════════════════════════════════════

    def shutdown(self, delay: int = 10) -> dict:
        """
        Éteint l'ordinateur après un délai.
        Args:
            delay: délai en secondes avant extinction (défaut 10s)
        """
        try:
            logger.warning(f"EXTINCTION demandée dans {delay} secondes.")
            subprocess.run(
                ["shutdown", "/s", "/t", str(delay)],
                check=True, capture_output=True
            )
            return self._ok(
                f"Extinction dans {delay} secondes. Sauvegardez vos fichiers !",
                {"delay": delay, "action": "shutdown"}
            )
        except subprocess.CalledProcessError as e:
            return self._err(f"Erreur extinction : {e.stderr.decode()}")
        except Exception as e:
            return self._err(f"Erreur inattendue : {str(e)}")

    def cancel_shutdown(self) -> dict:
        """Annule une extinction ou un redémarrage programmé."""
        try:
            subprocess.run(["shutdown", "/a"], check=True, capture_output=True)
            logger.info("Extinction annulée.")
            return self._ok("Extinction annulée avec succès.")
        except subprocess.CalledProcessError:
            return self._err("Aucune extinction programmée à annuler.")
        except Exception as e:
            return self._err(str(e))

    def restart(self, delay: int = 10) -> dict:
        """
        Redémarre l'ordinateur après un délai.
        Args:
            delay: délai en secondes (défaut 10s)
        """
        try:
            logger.warning(f"REDÉMARRAGE demandé dans {delay} secondes.")
            subprocess.run(
                ["shutdown", "/r", "/t", str(delay)],
                check=True, capture_output=True
            )
            return self._ok(
                f"Redémarrage dans {delay} secondes.",
                {"delay": delay, "action": "restart"}
            )
        except subprocess.CalledProcessError as e:
            return self._err(f"Erreur redémarrage : {e.stderr.decode()}")
        except Exception as e:
            return self._err(str(e))

    def sleep(self) -> dict:
        """Met l'ordinateur en veille (suspend to RAM)."""
        try:
            logger.info("Mise en veille demandée.")
            subprocess.Popen(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return self._ok("Mise en veille en cours...")
        except Exception as e:
            return self._err(f"Erreur veille : {str(e)}")

    def hibernate(self) -> dict:
        """Met l'ordinateur en hibernation (suspend to disk)."""
        try:
            logger.info("Hibernation demandée.")
            subprocess.Popen(
                ["shutdown", "/h"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return self._ok("Hibernation en cours...")
        except Exception as e:
            return self._err(f"Erreur hibernation : {str(e)}")

    def lock_screen(self) -> dict:
        """Verrouille l'écran Windows."""
        try:
            logger.info("Verrouillage écran demandé.")
            subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"], check=True
            )
            return self._ok("Écran verrouillé.")
        except Exception as e:
            return self._err(f"Erreur verrouillage : {str(e)}")

    def logout(self) -> dict:
        """Déconnecte l'utilisateur courant."""
        try:
            logger.warning("DÉCONNEXION utilisateur demandée.")
            subprocess.run(
                ["shutdown", "/l"], check=True, capture_output=True
            )
            return self._ok("Déconnexion en cours...")
        except subprocess.CalledProcessError as e:
            return self._err(f"Erreur déconnexion : {e.stderr.decode()}")
        except Exception as e:
            return self._err(str(e))

    def open_task_manager(self) -> dict:
        """Ouvre le gestionnaire des tâches Windows."""
        try:
            subprocess.Popen(["taskmgr.exe"])
            logger.info("Gestionnaire des tâches ouvert.")
            return self._ok("Gestionnaire des tâches ouvert.")
        except OSError as e:
            # Some Windows setups require UAC elevation for taskmgr.exe.
            if os.name == "nt" and getattr(e, "winerror", None) == 740:
                try:
                    result = ctypes.windll.shell32.ShellExecuteW(
                        None,
                        "runas",
                        "taskmgr.exe",
                        None,
                        None,
                        1,
                    )
                    if result > 32:
                        logger.info("Gestionnaire des tâches ouvert avec élévation UAC.")
                        return self._ok("Gestionnaire des tâches ouvert avec demande d'autorisation Windows.")
                    return self._err("Ouverture annulée ou refusée par l'autorisation Windows.")
                except Exception as inner_e:
                    return self._err(f"Impossible d'ouvrir le gestionnaire avec élévation : {str(inner_e)}")
        except Exception as e:
            return self._err(f"Impossible d'ouvrir le gestionnaire : {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    #  MARDI — Processus & CPU/RAM
    # ══════════════════════════════════════════════════════════════════════════

    def list_processes(self, top: int = 15, sort_by: str = "cpu") -> dict:
        """
        Liste les processus en cours, triés par CPU ou RAM.
        Args:
            top     : nombre de processus à retourner (défaut 15)
            sort_by : "cpu" ou "ram"
        """
        try:
            logger.info(f"Listage processus (top={top}, sort={sort_by})")
            processes = []

            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
                try:
                    info = proc.info
                    ram_mb = round(info["memory_info"].rss / 1024 / 1024, 1) if info["memory_info"] else 0
                    processes.append({
                        "pid":         info["pid"],
                        "name":        info["name"],
                        "cpu_percent": round(float(info["cpu_percent"] or 0), 1),
                        "ram_mb":      ram_mb,
                        "status":      info["status"],
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Triage
            key = "cpu_percent" if sort_by == "cpu" else "ram_mb"
            processes.sort(key=lambda x: x[key], reverse=True)
            top_processes = processes[:top]

            # Affichage formaté
            lines = [
                f"{'PID':<8} {'NOM':<35} {'CPU%':<8} {'RAM(MB)':<10} STATUT",
                "-" * 70,
            ]
            for p in top_processes:
                lines.append(
                    f"{p['pid']:<8} {p['name'][:34]:<35} "
                    f"{p['cpu_percent']:<8} {p['ram_mb']:<10} {p['status']}"
                )
            lines.append(f"\n  Total processus actifs : {len(processes)}")

            return self._ok(
                f"Top {top} processus (triés par {sort_by.upper()})",
                {"processes": top_processes, "total": len(processes), "display": "\n".join(lines)}
            )
        except Exception as e:
            return self._err(f"Erreur listage processus : {str(e)}")

    def kill_process(self, name_or_pid) -> dict:
        """
        Ferme un processus par nom ou PID.
        Par nom : ferme TOUS les processus correspondants.
        Args:
            name_or_pid : str (nom, ex: "chrome") ou int/str numérique (PID)
        """
        killed = []
        errors = []
        try:
            # ── Cas PID ──────────────────────────────────────────
            if str(name_or_pid).isdigit():
                pid = int(name_or_pid)
                try:
                    proc = psutil.Process(pid)
                    proc_name = proc.name()
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    killed.append(f"{proc_name} (PID {pid})")
                    logger.info(f"Processus tué : {proc_name} PID {pid}")
                except psutil.NoSuchProcess:
                    return self._err(f"Aucun processus avec PID {pid}.")
                except psutil.AccessDenied:
                    return self._err(f"Accès refusé pour PID {pid} — droits insuffisants.")

            # ── Cas Nom ───────────────────────────────────────────
            else:
                target = str(name_or_pid).lower().strip()
                target_exe = target if target.endswith(".exe") else target + ".exe"
                found = False

                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        proc_name_lower = proc.info["name"].lower()
                        if proc_name_lower in (target, target_exe):
                            found = True
                            pid = proc.info["pid"]
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except psutil.TimeoutExpired:
                                proc.kill()
                            killed.append(f"{proc.info['name']} (PID {pid})")
                            logger.info(f"Processus tué : {proc.info['name']} PID {pid}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        errors.append(str(e))

                if not found:
                    return self._err(f"Aucun processus trouvé avec le nom '{name_or_pid}'.")

            if killed:
                return self._ok(
                    f"{len(killed)} processus fermé(s) : {', '.join(killed)}",
                    {"killed": killed, "errors": errors}
                )
            return self._err(f"Impossible de fermer '{name_or_pid}'.")

        except Exception as e:
            return self._err(f"Erreur kill_process : {str(e)}")

    def system_info(self) -> dict:
        """Retourne CPU, RAM, swap et informations système complètes."""
        try:
            logger.info("Récupération infos système (CPU/RAM)...")

            # CPU
            cpu_percent   = psutil.cpu_percent(interval=1)
            cpu_count_log = psutil.cpu_count(logical=True)
            cpu_count_phy = psutil.cpu_count(logical=False)
            cpu_freq      = psutil.cpu_freq()
            cpu_freq_mhz  = round(cpu_freq.current, 0) if cpu_freq else 0
            cpu_freq_max  = round(cpu_freq.max, 0) if cpu_freq else 0
            cpu_per_core  = psutil.cpu_percent(interval=0.5, percpu=True)

            # RAM
            ram          = psutil.virtual_memory()
            ram_total_gb = round(ram.total / 1024**3, 2)
            ram_used_gb  = round(ram.used  / 1024**3, 2)
            ram_free_gb  = round(ram.free  / 1024**3, 2)
            ram_percent  = ram.percent

            swap         = psutil.swap_memory()
            swap_total   = round(swap.total / 1024**3, 2)
            swap_used    = round(swap.used  / 1024**3, 2)

            # Uptime
            boot_dt    = datetime.datetime.fromtimestamp(psutil.boot_time())
            uptime_sec = (datetime.datetime.now() - boot_dt).total_seconds()
            uptime_str = self._format_uptime(uptime_sec)

            # OS
            os_name  = platform.system()
            os_ver   = platform.version()[:50]
            hostname = platform.node()
            machine  = platform.machine()

            # Affichage
            lines = [
                "┌─────────────────────────────────────────────┐",
                "│           INFORMATIONS SYSTÈME               │",
                "├─────────────────────────────────────────────┤",
                f"│  PC        : {hostname[:30]}".ljust(46) + "│",
                f"│  OS        : {os_name} ({machine})".ljust(46) + "│",
                f"│  Uptime    : {uptime_str}".ljust(46) + "│",
                "├─────────────────────────────────────────────┤",
                f"│  CPU       : {cpu_percent}%".ljust(46) + "│",
                f"│  Coeurs    : {cpu_count_phy} physiques / {cpu_count_log} logiques".ljust(46) + "│",
                f"│  Frequence : {cpu_freq_mhz} MHz (max {cpu_freq_max} MHz)".ljust(46) + "│",
                "├─────────────────────────────────────────────┤",
                f"│  RAM       : {ram_used_gb}/{ram_total_gb} GB ({ram_percent}%)".ljust(46) + "│",
                f"│  RAM libre : {ram_free_gb} GB".ljust(46) + "│",
                f"│  SWAP      : {swap_used}/{swap_total} GB".ljust(46) + "│",
                "└─────────────────────────────────────────────┘",
            ]

            data = {
                "cpu": {
                    "percent": cpu_percent,
                    "cores_physical": cpu_count_phy,
                    "cores_logical": cpu_count_log,
                    "freq_mhz": cpu_freq_mhz,
                    "freq_max_mhz": cpu_freq_max,
                    "per_core": cpu_per_core,
                },
                "ram": {
                    "total_gb": ram_total_gb,
                    "used_gb":  ram_used_gb,
                    "free_gb":  ram_free_gb,
                    "percent":  ram_percent,
                },
                "swap": {"total_gb": swap_total, "used_gb": swap_used},
                "system": {
                    "os":       os_name,
                    "version":  os_ver,
                    "machine":  machine,
                    "hostname": hostname,
                    "uptime":   uptime_str,
                    "boot_time": str(boot_dt),
                },
                "display": "\n".join(lines)
            }
            return self._ok(
                f"CPU: {cpu_percent}% | RAM: {ram_percent}% ({ram_used_gb}/{ram_total_gb} GB) | Uptime: {uptime_str}",
                data
            )
        except Exception as e:
            return self._err(f"Erreur system_info : {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    #  MERCREDI — Disque, Température, Rapport complet
    # ══════════════════════════════════════════════════════════════════════════

    def disk_info(self) -> dict:
        """Retourne l'espace disque pour toutes les partitions montées."""
        try:
            logger.info("Récupération infos disques...")
            partitions_data = []
            lines = [
                f"{'DISQUE':<10} {'TOTAL':>9} {'UTILISE':>9} {'LIBRE':>9} {'%':>6}  BARRE",
                "-" * 65,
            ]

            partition_list = psutil.disk_partitions(all=False)
            if not partition_list:
                partition_list = psutil.disk_partitions(all=True)

            for part in partition_list:
                # Fallback : si aucune partition physique, on inclut tout
                try:
                    usage    = psutil.disk_usage(part.mountpoint)
                    total_gb = round(usage.total / 1024**3, 1)
                    used_gb  = round(usage.used  / 1024**3, 1)
                    free_gb  = round(usage.free  / 1024**3, 1)
                    pct      = usage.percent
                    alert    = " ATTENTION" if pct > 85 else ""

                    bar_len = 20
                    filled  = int(bar_len * pct / 100)
                    bar     = "#" * filled + "." * (bar_len - filled)

                    partitions_data.append({
                        "device":     part.device,
                        "mountpoint": part.mountpoint,
                        "fstype":     part.fstype,
                        "total_gb":   total_gb,
                        "used_gb":    used_gb,
                        "free_gb":    free_gb,
                        "percent":    pct,
                        "alert":      pct > 85,
                    })
                    lines.append(
                        f"{part.device:<10} {total_gb:>7.1f}G {used_gb:>7.1f}G "
                        f"{free_gb:>7.1f}G {pct:>5.1f}%  [{bar}]{alert}"
                    )
                except (PermissionError, OSError):
                    lines.append(f"{part.device:<10} (acces refuse)")

            # Stats I/O globales
            try:
                io = psutil.disk_io_counters()
                read_mb  = round(io.read_bytes  / 1024**2, 1)
                write_mb = round(io.write_bytes / 1024**2, 1)
                lines.append(f"\n  I/O depuis demarrage -> Lu: {read_mb} MB | Ecrit: {write_mb} MB")
            except Exception:
                pass

            critical = [p for p in partitions_data if p["percent"] > 85]
            summary  = f"{len(partitions_data)} partition(s) detectee(s)"
            if critical:
                summary += f" — ATTENTION {len(critical)} partition(s) > 85%"

            return self._ok(summary, {
                "partitions": partitions_data,
                "display":    "\n".join(lines)
            })
        except Exception as e:
            return self._err(f"Erreur disk_info : {str(e)}")

    def temperature_info(self) -> dict:
        """
        Retourne les températures des composants.
        Windows : nécessite OpenHardwareMonitor ou LibreHardwareMonitor.
        Linux/macOS : natif via psutil.
        """
        try:
            logger.info("Récupération températures...")

            if not hasattr(psutil, "sensors_temperatures"):
                return self._get_temp_windows()

            temps = psutil.sensors_temperatures()
            if not temps:
                return self._ok(
                    "Temperatures non disponibles sur ce systeme.",
                    {"available": False}
                )

            temp_data = {}
            lines     = ["TEMPERATURES :"]
            for name, entries in temps.items():
                temp_data[name] = []
                for entry in entries:
                    label  = entry.label or name
                    crit   = entry.critical or 100
                    high   = entry.high or 80
                    status = "CRITIQUE" if entry.current >= crit \
                             else ("CHAUD" if entry.current >= high else "OK")
                    temp_data[name].append({
                        "label":    label,
                        "current":  entry.current,
                        "high":     entry.high,
                        "critical": entry.critical,
                        "status":   status,
                    })
                    lines.append(f"  {label:<25} {entry.current:>5.1f}C  [{status}]")

            return self._ok(
                f"Temperatures recuperees ({len(temps)} capteurs)",
                {"temperatures": temp_data, "display": "\n".join(lines)}
            )
        except Exception as e:
            return self._err(f"Erreur temperature : {str(e)}")

    def _get_temp_windows(self) -> dict:
        """Tente de récupérer les températures via OpenHardwareMonitor (WMI)."""
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    "Get-WmiObject -Namespace root/OpenHardwareMonitor "
                    "-Class Sensor | Where-Object {$_.SensorType -eq 'Temperature'} "
                    "| Select-Object Name, Value | Format-Table -AutoSize"
                ],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return self._ok(
                    "Temperatures (via OpenHardwareMonitor)",
                    {"display": result.stdout, "available": True}
                )
        except Exception:
            pass

        return self._ok(
            "Temperatures non disponibles. Installe LibreHardwareMonitor pour les activer.",
            {
                "available": False,
                "tip": "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor"
            }
        )

    def network_info(self) -> dict:
        """Retourne les informations réseau (IP, interfaces, I/O)."""
        try:
            logger.info("Récupération infos réseau...")
            interfaces = {}
            lines      = ["INTERFACES RESEAU :"]

            for name, addrs in psutil.net_if_addrs().items():
                interfaces[name] = []
                for addr in addrs:
                    if hasattr(addr, "family") and addr.family.name in ("AF_INET", "AF_INET6"):
                        interfaces[name].append({
                            "type":    addr.family.name,
                            "address": addr.address,
                            "netmask": addr.netmask,
                        })
                        lines.append(f"  {name:<22} {addr.family.name:<10} {addr.address}")

            try:
                net_io  = psutil.net_io_counters()
                sent_mb = round(net_io.bytes_sent / 1024**2, 1)
                recv_mb = round(net_io.bytes_recv / 1024**2, 1)
                lines.append(f"\n  Envoye: {sent_mb} MB | Recu: {recv_mb} MB")
            except Exception:
                pass

            return self._ok(
                f"{len(interfaces)} interface(s) reseau detectee(s)",
                {"interfaces": interfaces, "display": "\n".join(lines)}
            )
        except Exception as e:
            return self._err(f"Erreur network_info : {str(e)}")

    def full_system_report(self) -> dict:
        """Rapport complet : CPU + RAM + Disque + Réseau + Température."""
        try:
            logger.info("Génération rapport système complet...")

            sys_r  = self.system_info()
            disk_r = self.disk_info()
            temp_r = self.temperature_info()
            net_r  = self.network_info()

            sys_d  = sys_r.get("data",  {}) or {}
            disk_d = disk_r.get("data", {}) or {}
            temp_d = temp_r.get("data", {}) or {}
            net_d  = net_r.get("data",  {}) or {}

            now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            report = [
                "=" * 50,
                f"  RAPPORT SYSTEME COMPLET  —  {now}",
                "=" * 50,
            ]

            # CPU / RAM
            if sys_d:
                cpu    = sys_d.get("cpu", {})
                ram    = sys_d.get("ram", {})
                system = sys_d.get("system", {})
                report += [
                    f"  PC      : {system.get('hostname', 'N/A')}",
                    f"  OS      : {system.get('os', 'N/A')}",
                    f"  Uptime  : {system.get('uptime', 'N/A')}",
                    "-" * 50,
                    f"  CPU     : {cpu.get('percent', '?')}%  "
                    f"({cpu.get('cores_physical', '?')} coeurs @ {cpu.get('freq_mhz', '?')} MHz)",
                    f"  RAM     : {ram.get('used_gb', '?')}/{ram.get('total_gb', '?')} GB "
                    f"({ram.get('percent', '?')}%)",
                ]

            # Disques
            if disk_d:
                report.append("-" * 50)
                for part in disk_d.get("partitions", []):
                    alert = " !! PLEIN" if part["percent"] > 85 else ""
                    report.append(
                        f"  {part['device']:<8} {part['used_gb']}/{part['total_gb']} GB "
                        f"({part['percent']}%){alert}"
                    )

            # Réseau
            if net_d:
                report.append("-" * 50)
                report.append("  RESEAU :")
                for iface, addrs in net_d.get("interfaces", {}).items():
                    for addr in addrs:
                        if addr["type"] == "AF_INET":
                            report.append(f"    {iface:<20} {addr['address']}")

            report.append("=" * 50)

            return self._ok("Rapport systeme complet genere.", {
                "system":      sys_d,
                "disk":        disk_d,
                "temperature": temp_d,
                "network":     net_d,
                "display":     "\n".join(report)
            })
        except Exception as e:
            return self._err(f"Erreur rapport complet : {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    #  UTILITAIRES PRIVÉS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Convertit des secondes en string lisible (ex: '2j 4h 35m')."""
        seconds = int(seconds)
        days    = seconds // 86400
        hours   = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        parts   = []
        if days:    parts.append(f"{days}j")
        if hours:   parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        return " ".join(parts) if parts else "< 1m"

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}