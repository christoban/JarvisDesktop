"""
app_manager.py — Gestion des applications Windows
Ouvrir, fermer, redémarrer, vérifier les applications.

SEMAINE 3 — IMPLÉMENTATION COMPLÈTE
  Lundi  : open_app, close_app, is_running, check_app
  Mardi  : restart_app, mapping complet, find_exe_path, list_running_apps
"""

import os
import time
import subprocess
import psutil
from pathlib import Path
from config.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  MAPPING NOMS NATURELS → EXÉCUTABLES
#  Clé   : nom que l'utilisateur peut taper (en minuscules)
#  Valeur: (exe_name, launch_cmd_windows, launch_cmd_fallback)
# ══════════════════════════════════════════════════════════════════════════════

APP_MAP = {
    # ── Navigateurs ───────────────────────────────────────────────────────────
    "chrome":           ("chrome.exe",      "chrome",           "google-chrome"),
    "google chrome":    ("chrome.exe",      "chrome",           "google-chrome"),
    "firefox":          ("firefox.exe",     "firefox",          "firefox"),
    "edge":             ("msedge.exe",      "msedge",           "microsoft-edge"),
    "microsoft edge":   ("msedge.exe",      "msedge",           "microsoft-edge"),
    "opera":            ("opera.exe",       "opera",            "opera"),
    "brave":            ("brave.exe",       "brave",            "brave-browser"),

    # ── Suite Office ──────────────────────────────────────────────────────────
    "word":             ("WINWORD.EXE",     "WINWORD",          None),
    "microsoft word":   ("WINWORD.EXE",     "WINWORD",          None),
    "excel":            ("EXCEL.EXE",       "EXCEL",            None),
    "microsoft excel":  ("EXCEL.EXE",       "EXCEL",            None),
    "powerpoint":       ("POWERPNT.EXE",    "POWERPNT",         None),
    "outlook":          ("OUTLOOK.EXE",     "OUTLOOK",          None),
    "teams":            ("Teams.exe",       "Teams",            None),
    "onenote":          ("ONENOTE.EXE",     "ONENOTE",          None),

    # ── Éditeurs de code ──────────────────────────────────────────────────────
    "vscode":           ("Code.exe",        "code",             "code"),
    "vs code":          ("Code.exe",        "code",             "code"),
    "visual studio code":("Code.exe",       "code",             "code"),
    "sublime":          ("sublime_text.exe","subl",             "subl"),
    "notepad++":        ("notepad++.exe",   "notepad++",        None),
    "notepad":          ("notepad.exe",     "notepad",          None),
    "bloc-notes":       ("notepad.exe",     "notepad",          None),

    # ── Utilitaires Windows ───────────────────────────────────────────────────
    "explorateur":      ("explorer.exe",    "explorer",         None),
    "explorer":         ("explorer.exe",    "explorer",         None),
    "calculatrice":     ("calc.exe",        "calc",             None),
    "calculator":       ("calc.exe",        "calc",             None),
    "paint":            ("mspaint.exe",     "mspaint",          None),
    "terminal":         ("wt.exe",          "wt",               "xterm"),
    "powershell":       ("powershell.exe",  "powershell",       None),
    "cmd":              ("cmd.exe",         "cmd",              None),
    "invite de commandes": ("cmd.exe",      "cmd",              None),
    "regedit":          ("regedit.exe",     "regedit",          None),
    "msconfig":         ("msconfig.exe",    "msconfig",         None),
    "services":         ("services.msc",    "services.msc",     None),

    # ── Multimédia ────────────────────────────────────────────────────────────
    "spotify":          ("Spotify.exe",     "spotify",          "spotify"),
    "vlc":              ("vlc.exe",         "vlc",              "vlc"),
    "media player":     ("wmplayer.exe",    "wmplayer",         None),
    "groove":           ("Music.UI.exe",    None,               None),

    # ── Communication ─────────────────────────────────────────────────────────
    "discord":          ("Discord.exe",     "discord",          "discord"),
    "slack":            ("slack.exe",       "slack",            "slack"),
    "zoom":             ("Zoom.exe",        "zoom",             "zoom"),
    "skype":            ("Skype.exe",       "skype",            "skype"),
    "telegram":         ("Telegram.exe",    "telegram-desktop", "telegram-desktop"),
    "whatsapp":         ("WhatsApp.exe",    None,               None),

    # ── Autres outils ─────────────────────────────────────────────────────────
    "7zip":             ("7zFM.exe",        "7zFM",             None),
    "winrar":           ("WinRAR.exe",      "WinRAR",           None),
    "postman":          ("Postman.exe",     "postman",          "postman"),
    "docker":           ("Docker Desktop.exe", "docker desktop", None),
    "git":              ("git.exe",         "git",              "git"),
}

# Dossiers standards où chercher les exécutables sur Windows
WINDOWS_SEARCH_PATHS = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\Users\{user}\AppData\Local\Programs",
    r"C:\Users\{user}\AppData\Roaming",
    r"C:\Windows\System32",
    r"C:\Windows",
]


class AppManager:
    """
    Gestion complète des applications Windows.
    Toutes les méthodes retournent le format standard :
        { "success": bool, "message": str, "data": dict | None }
    """

    # ══════════════════════════════════════════════════════════════════════════
    #  LUNDI — Ouvrir, Fermer, Vérifier
    # ══════════════════════════════════════════════════════════════════════════

    def open_app(self, app_name: str, args: list = None) -> dict:
        """
        Ouvre une application par son nom naturel ou son chemin complet.

        Args:
            app_name : nom naturel ("chrome", "word") ou chemin complet
            args     : arguments optionnels passés à l'application

        Exemples :
            open_app("chrome")
            open_app("chrome", ["https://google.com"])
            open_app("vscode", ["C:/mon/projet"])
        """
        logger.info(f"Ouverture application : '{app_name}'")
        name_lower = app_name.lower().strip()
        args = args or []

        # ── Cas 1 : chemin absolu fourni directement ──────────────────────────
        path_obj = Path(app_name)
        if path_obj.is_absolute() and path_obj.exists():
            return self._launch(str(path_obj), args, app_name)

        # ── Cas 2 : nom dans le mapping ───────────────────────────────────────
        if name_lower in APP_MAP:
            exe_name, win_cmd, fallback_cmd = APP_MAP[name_lower]

            # Chercher le chemin réel de l'exe
            exe_path = self._find_exe_path(exe_name)
            if exe_path:
                return self._launch(exe_path, args, app_name)

            # Essayer la commande Windows directement (elle est dans le PATH)
            if win_cmd:
                result = self._launch(win_cmd, args, app_name)
                if result["success"]:
                    return result

            # Fallback Linux/cross-platform
            if fallback_cmd:
                result = self._launch(fallback_cmd, args, app_name)
                if result["success"]:
                    return result

            return self._err(
                f"Application '{app_name}' reconnue mais introuvable sur ce PC. "
                f"Est-elle installée ?"
            )

        # ── Cas 3 : essayer directement comme commande système ────────────────
        result = self._launch(name_lower, args, app_name)
        if result["success"]:
            return result

        # ── Cas 4 : recherche fuzzy dans le mapping ───────────────────────────
        suggestions = self._fuzzy_match(name_lower)
        if suggestions:
            return self._err(
                f"Application '{app_name}' non trouvée. "
                f"Vouliez-vous dire : {', '.join(suggestions)} ?",
                {"suggestions": suggestions}
            )

        return self._err(
            f"Application '{app_name}' non reconnue. "
            f"Tape 'applications' pour voir la liste complète."
        )

    def close_app(self, app_name: str, force: bool = False) -> dict:
        """
        Ferme une application par son nom.
        Ferme toutes les instances du processus trouvé.

        Args:
            app_name : nom naturel ou nom d'exécutable
            force    : si True, kill immédiat (SIGKILL) sans attendre
        """
        logger.info(f"Fermeture application : '{app_name}' (force={force})")
        name_lower = app_name.lower().strip()

        # Résoudre le nom d'exe cible
        exe_targets = self._resolve_exe_names(name_lower)
        logger.debug(f"Cibles EXE : {exe_targets}")

        killed = []
        errors = []
        found  = False

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc_name_lower = proc.info["name"].lower()
                if proc_name_lower in exe_targets:
                    found = True
                    pid   = proc.info["pid"]
                    pname = proc.info["name"]
                    try:
                        if force:
                            proc.kill()
                        else:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except psutil.TimeoutExpired:
                                proc.kill()
                        killed.append(f"{pname} (PID {pid})")
                        logger.info(f"Application fermée : {pname} PID {pid}")
                    except psutil.AccessDenied:
                        errors.append(f"Accès refusé pour {pname} (PID {pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not found:
            return self._err(
                f"Application '{app_name}' n'est pas en cours d'exécution.",
                {"was_running": False}
            )

        if killed:
            return self._ok(
                f"{len(killed)} instance(s) de '{app_name}' fermée(s) : {', '.join(killed)}",
                {"killed": killed, "errors": errors}
            )

        return self._err(
            f"Impossible de fermer '{app_name}' : {'; '.join(errors)}",
            {"errors": errors}
        )

    def is_running(self, app_name: str) -> bool:
        """
        Vérifie si une application est en cours d'exécution.

        Returns:
            True si au moins une instance tourne, False sinon.
        """
        exe_targets = self._resolve_exe_names(app_name.lower().strip())
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"].lower() in exe_targets:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def check_app(self, app_name: str) -> dict:
        """
        Vérifie le statut d'une application et retourne ses instances.

        Returns un dict complet avec les PIDs et usage mémoire.
        """
        logger.info(f"Vérification statut : '{app_name}'")
        exe_targets = self._resolve_exe_names(app_name.lower().strip())
        instances   = []

        for proc in psutil.process_iter(["pid", "name", "memory_info", "status", "create_time"]):
            try:
                if proc.info["name"].lower() in exe_targets:
                    ram_mb = round(proc.info["memory_info"].rss / 1024**2, 1) \
                             if proc.info["memory_info"] else 0
                    instances.append({
                        "pid":        proc.info["pid"],
                        "name":       proc.info["name"],
                        "ram_mb":     ram_mb,
                        "status":     proc.info["status"],
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        running = len(instances) > 0
        if running:
            total_ram = sum(i["ram_mb"] for i in instances)
            return self._ok(
                f"'{app_name}' est en cours d'exécution — "
                f"{len(instances)} instance(s), {total_ram:.1f} MB RAM",
                {"running": True, "instances": instances, "count": len(instances)}
            )
        else:
            return self._ok(
                f"'{app_name}' n'est pas en cours d'exécution.",
                {"running": False, "instances": [], "count": 0}
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  MARDI — Redémarrer, Lister, Trouver
    # ══════════════════════════════════════════════════════════════════════════

    def restart_app(self, app_name: str) -> dict:
        """
        Redémarre une application : ferme puis relance.

        Args:
            app_name : nom naturel de l'application
        """
        logger.info(f"Redémarrage application : '{app_name}'")

        # 1. Vérifier si elle tourne
        was_running = self.is_running(app_name)

        # 2. Fermer si elle tourne
        if was_running:
            close_result = self.close_app(app_name)
            if not close_result["success"]:
                return self._err(
                    f"Impossible de fermer '{app_name}' pour le redémarrage : "
                    f"{close_result['message']}"
                )
            # Petit délai pour laisser le temps au processus de se terminer
            time.sleep(1.5)

        # 3. Rouvrir
        open_result = self.open_app(app_name)
        if not open_result["success"]:
            return self._err(
                f"'{app_name}' fermée mais impossible de la relancer : "
                f"{open_result['message']}"
            )

        action = "redémarrée" if was_running else "lancée (n'était pas ouverte)"
        return self._ok(
            f"'{app_name}' {action} avec succès.",
            {"was_running": was_running, "restarted": True}
        )

    def list_running_apps(self) -> dict:
        """
        Liste toutes les applications connues actuellement en cours d'exécution.
        Ne retourne que les apps présentes dans APP_MAP (filtre les processus système).
        """
        logger.info("Listage applications ouvertes...")

        # Construire un index inversé : exe_lower → app_name
        exe_to_name = {}
        for app_name, (exe_name, _, _) in APP_MAP.items():
            exe_lower = exe_name.lower()
            if exe_lower not in exe_to_name:
                exe_to_name[exe_lower] = app_name

        running_apps = {}
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                proc_exe = proc.info["name"].lower()
                if proc_exe in exe_to_name:
                    app_name = exe_to_name[proc_exe]
                    ram_mb = round(proc.info["memory_info"].rss / 1024**2, 1) \
                             if proc.info["memory_info"] else 0
                    if app_name not in running_apps:
                        running_apps[app_name] = {
                            "name":      app_name,
                            "exe":       proc.info["name"],
                            "instances": 0,
                            "total_ram_mb": 0
                        }
                    running_apps[app_name]["instances"]    += 1
                    running_apps[app_name]["total_ram_mb"] += ram_mb
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        apps_list = sorted(running_apps.values(), key=lambda x: x["total_ram_mb"], reverse=True)

        lines = [f"{'APPLICATION':<25} {'INSTANCES':>10}  {'RAM (MB)':>10}"]
        lines.append("-" * 50)
        for app in apps_list:
            lines.append(
                f"{app['name']:<25} {app['instances']:>10}  {app['total_ram_mb']:>10.1f}"
            )

        return self._ok(
            f"{len(apps_list)} application(s) connue(s) en cours d'exécution.",
            {"apps": apps_list, "count": len(apps_list), "display": "\n".join(lines)}
        )

    def list_known_apps(self) -> dict:
        """Retourne la liste de toutes les applications connues dans le mapping."""
        unique = {}
        for name, (exe, _, _) in APP_MAP.items():
            if exe not in unique:
                unique[exe] = name

        apps = [{"name": name, "exe": exe} for exe, name in sorted(unique.items())]
        lines = [f"{'NOM':<25} {'EXÉCUTABLE'}"]
        lines.append("-" * 50)
        for app in apps:
            lines.append(f"{app['name']:<25} {app['exe']}")

        return self._ok(
            f"{len(apps)} applications connues dans le mapping.",
            {"apps": apps, "display": "\n".join(lines)}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  UTILITAIRES PRIVÉS
    # ══════════════════════════════════════════════════════════════════════════

    def _launch(self, cmd: str, args: list, display_name: str) -> dict:
        """Lance une commande et retourne le résultat."""
        try:
            full_cmd = [cmd] + args
            proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Sur Windows : pas de fenêtre console pour les apps GUI
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            )
            logger.info(f"Application lancée : '{display_name}' (PID {proc.pid})")
            return self._ok(
                f"'{display_name}' lancée avec succès.",
                {"pid": proc.pid, "cmd": cmd}
            )
        except FileNotFoundError:
            return self._err(f"Exécutable introuvable : '{cmd}'")
        except PermissionError:
            return self._err(f"Permission refusée pour lancer '{cmd}'.")
        except Exception as e:
            return self._err(f"Erreur lancement '{cmd}' : {str(e)}")

    def _find_exe_path(self, exe_name: str) -> str | None:
        """
        Cherche le chemin complet d'un exécutable.
        1. Dans le PATH système
        2. Dans les dossiers Programs standard
        """
        import shutil as sh
        # Chercher dans le PATH
        found = sh.which(exe_name)
        if found:
            return found

        # Chercher dans les dossiers Windows standard
        username = os.environ.get("USERNAME", "user")
        for search_dir in WINDOWS_SEARCH_PATHS:
            search_dir = search_dir.replace("{user}", username)
            for root, dirs, files in os.walk(search_dir):
                if exe_name.lower() in [f.lower() for f in files]:
                    return os.path.join(root, exe_name)
                # Limiter la profondeur pour ne pas trop chercher
                depth = root[len(search_dir):].count(os.sep)
                if depth >= 3:
                    dirs.clear()

        return None

    def _resolve_exe_names(self, app_name: str) -> set:
        """
        Retourne l'ensemble des noms d'exécutables associés à un nom d'application.
        Inclut le nom brut, avec et sans .exe, en minuscules.
        """
        targets = {app_name.lower(), (app_name + ".exe").lower()}

        if app_name in APP_MAP:
            exe_name = APP_MAP[app_name][0]
            targets.add(exe_name.lower())
            targets.add(exe_name.lower().replace(".exe", ""))

        return targets

    def _fuzzy_match(self, query: str, max_results: int = 3) -> list:
        """Trouve les noms d'applications proches du terme recherché."""
        results = []
        for name in APP_MAP:
            if query in name or name in query:
                results.append(name)
                if len(results) >= max_results:
                    break
        return results

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}