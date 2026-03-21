"""
intent_executor.py — Exécuteur d'intentions
Reçoit un intent + params structurés depuis CommandParser
et appelle la bonne fonction du bon module.

SEMAINE 4 — MERCREDI
  Mapping COMPLET : 50+ intentions → modules système, apps, fichiers, navigateur, audio
"""

import os
from pathlib import Path

from config.logger import get_logger

logger = get_logger(__name__)


class IntentExecutor:
    """
    Exécute une intention structurée issue de CommandParser.

    Usage :
        executor = IntentExecutor()
        result = executor.execute("APP_OPEN", {"app_name": "chrome", "args": []})
        # → {"success": True, "message": "'chrome' lancée.", "data": {...}}

    Toutes les méthodes retournent le format standard :
        { "success": bool, "message": str, "data": dict | None }
    """

    def __init__(self):
        # Lazy-init des modules pour éviter les imports circulaires
        self._sc = None   # SystemControl
        self._am = None   # AppManager
        self._fm = None   # FileManager
        self._bc = None   # BrowserControl
        self._au = None   # AudioManager
        self._nm = None   # NetworkManager
        self._dr = None   # DocReader
        self._history = None
        self._macros = None
        self._power = None
        self._window = None
        self._music = None  
        self._raw_command_agent = None

        # Table de dispatch : intent → méthode
        self._handlers = {
            # ── Système ───────────────────────────────────────────────────────
            "SYSTEM_SHUTDOWN":        self._system_shutdown,
            "SYSTEM_RESTART":         self._system_restart,
            "SYSTEM_SLEEP":           self._system_sleep,
            "SYSTEM_HIBERNATE":       self._system_hibernate,
            "POWER_SLEEP":            self._power_sleep,
            "POWER_HIBERNATE":        self._power_hibernate,
            "POWER_CANCEL":           self._power_cancel,
            "POWER_STATE":            self._power_state,
            "SYSTEM_TIME":            self._system_time,
            "SYSTEM_LOCK":            self._system_lock,
            "SYSTEM_UNLOCK":          self._system_unlock,
            "SYSTEM_LOGOUT":          self._system_logout,
            "SYSTEM_INFO":            self._system_info,
            "SYSTEM_DISK":            self._system_disk,
            "SYSTEM_PROCESSES":       self._system_processes,
            "SYSTEM_KILL_PROCESS":    self._system_kill,
            "SYSTEM_NETWORK":         self._system_network,
            "SYSTEM_TEMPERATURE":     self._system_temperature,
            "SYSTEM_FULL_REPORT":     self._system_full_report,
            "SYSTEM_TASK_MANAGER":    self._system_task_manager,
            "SYSTEM_CANCEL_SHUTDOWN": self._system_cancel_shutdown,
            "SCREEN_UNLOCK":          self._screen_unlock,
            "SCREEN_OFF":             self._screen_off,
            "WAKE_ON_LAN":            self._wake_on_lan,
            "MEMORY_SHOW":            self._memory_show,
            # ── Réseau (Semaine 9) ───────────────────────────────────────────
            "WIFI_LIST":         self._wifi_list,
            "WIFI_CONNECT":      self._wifi_connect,
            "WIFI_DISCONNECT":   self._wifi_disconnect,
            "WIFI_ENABLE":       self._wifi_enable,
            "WIFI_DISABLE":      self._wifi_disable,
            "BLUETOOTH_ENABLE":  self._bluetooth_enable,
            "BLUETOOTH_DISABLE": self._bluetooth_disable,
            "BLUETOOTH_LIST":    self._bluetooth_list,
            "NETWORK_INFO":      self._network_info,
            # ── Applications ──────────────────────────────────────────────────
            "APP_OPEN":         self._app_open,
            "APP_CLOSE":        self._app_close,
            "APP_RESTART":      self._app_restart,
            "APP_CHECK":        self._app_check,
            "APP_LIST_RUNNING": self._app_list_running,
            "APP_LIST_KNOWN":   self._app_list_known,
            # ── Fichiers ──────────────────────────────────────────────────────
            "FILE_SEARCH":         self._file_search,
            "FILE_SEARCH_TYPE":    self._file_search_type,
            "FILE_SEARCH_CONTENT": self._file_search_content,
            "FILE_OPEN":           self._file_open,
            "FILE_CLOSE":          self._file_close,
            "WINDOW_CLOSE":        self._window_close,
            "FILE_COPY":           self._file_copy,
            "FILE_MOVE":           self._file_move,
            "FILE_RENAME":         self._file_rename,
            "FILE_DELETE":         self._file_delete,
            "FILE_INFO":           self._file_info,
            "FOLDER_LIST":         self._folder_list,
            "FOLDER_CREATE":       self._folder_create,
            # ── Navigateur ────────────────────────────────────────────────────────────────
            "BROWSER_OPEN":           self._browser_open,
            "BROWSER_CLOSE":          self._browser_close,
            "BROWSER_URL":            self._browser_url,
            "BROWSER_NEW_TAB":        self._browser_new_tab,
            "BROWSER_BACK":           self._browser_back,
            "BROWSER_FORWARD":        self._browser_forward,
            "BROWSER_RELOAD":         self._browser_reload,
            "BROWSER_CLOSE_TAB":      self._browser_close_tab,
            "BROWSER_SEARCH":         self._browser_search,
            "BROWSER_SEARCH_YOUTUBE": self._browser_search_youtube,
            "BROWSER_SEARCH_GITHUB":  self._browser_search_github,
            "BROWSER_OPEN_RESULT":    self._browser_open_result,
            "BROWSER_LIST_RESULTS":   self._browser_list_results,
            "BROWSER_GO_TO_SITE":     self._browser_go_to_site,
            "BROWSER_NAVIGATE":       self._browser_navigate,
            "BROWSER_READ":           self._browser_read,
            "BROWSER_PAGE_INFO":      self._browser_page_info,
            "BROWSER_EXTRACT_LINKS":  self._browser_extract_links,
            "BROWSER_SUMMARIZE":      self._browser_summarize,
            "BROWSER_SCROLL":         self._browser_scroll,
            "BROWSER_CLICK_TEXT":     self._browser_click_text,
            "BROWSER_FILL_FIELD":     self._browser_fill_field,
            "BROWSER_TYPE":           self._browser_type,
            "BROWSER_DOWNLOAD":       self._browser_download,
            "BROWSER_LIST_TABS":      self._browser_list_tabs,
            "BROWSER_SWITCH_TAB":     self._browser_switch_tab,
            "BROWSER_FIND_AND_OPEN":  self._browser_find_and_open,
            "BROWSER_CONTEXT":        self._browser_context,
            # ── Audio ─────────────────────────────────────────────────────────
            "AUDIO_VOLUME_UP":   self._audio_volume_up,
            "AUDIO_VOLUME_DOWN": self._audio_volume_down,
            "AUDIO_VOLUME_SET":  self._audio_volume_set,
            "AUDIO_MUTE":        self._audio_mute,
            "AUDIO_PLAY":        self._audio_play,
            # ── Musique (module complet semaine 3) ─────────────────────────
            "MUSIC_PLAY":             self._music_play,
            "MUSIC_PAUSE":            self._music_pause,
            "MUSIC_RESUME":           self._music_resume,
            "MUSIC_STOP":             self._music_stop,
            "MUSIC_NEXT":             self._music_next,
            "MUSIC_PREV":             self._music_prev,
            "MUSIC_VOLUME":           self._music_volume,
            "MUSIC_SHUFFLE":          self._music_shuffle,
            "MUSIC_REPEAT":           self._music_repeat,
            "MUSIC_CURRENT":          self._music_current,
            "MUSIC_PLAYLIST_CREATE":  self._music_playlist_create,
            "MUSIC_PLAYLIST_PLAY":    self._music_playlist_play,
            "MUSIC_PLAYLIST_LIST":    self._music_playlist_list,
            "MUSIC_LIBRARY_SCAN":     self._music_library_scan,
            # ── Documents ─────────────────────────────────────────────────────
            "DOC_READ":        self._doc_read,
            "DOC_SUMMARIZE":   self._doc_summarize,
            "DOC_SEARCH_WORD": self._doc_search_word,
            # ── Écran ─────────────────────────────────────────────────────────
            "SCREEN_CAPTURE":        self._screen_capture,
            "SCREENSHOT_TO_PHONE":   self._screenshot_to_phone,
            "SCREEN_BRIGHTNESS":     self._screen_brightness,
            "SCREEN_INFO":           self._screen_info,
            "SCREEN_RECORD":         self._screen_record,
            # ── Historique / Macros (Semaine 11) ───────────────────────────
            "REPEAT_LAST":           self._repeat_last,
            "HISTORY_SHOW":          self._history_show,
            "HISTORY_CLEAR":         self._history_clear,
            "HISTORY_SEARCH":        self._history_search,
            "MACRO_RUN":             self._macro_run,
            "MACRO_LIST":            self._macro_list,
            "MACRO_SAVE":            self._macro_save,
            "MACRO_DELETE":          self._macro_delete,

            "GREETING": self._greeting,
            "INCOMPLETE": self._incomplete,
            "KNOWLEDGE_QA": self._knowledge_qa,

            # ── Aide / Inconnu ────────────────────────────────────────────────
            "HELP":    self._help,
            "UNKNOWN": self._unknown,
        }

        logger.info(f"IntentExecutor initialisé — {len(self._handlers)} intentions mappées.")

    # ══════════════════════════════════════════════════════════════════════════
    #  POINT D'ENTRÉE PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════════

    def execute(self, intent: str, params: dict, raw_command: str = "", agent=None) -> dict:
        """
        Exécute une intention avec ses paramètres.

        Args:
            intent      : ex "APP_OPEN"
            params      : ex {"app_name": "chrome", "args": []}
            raw_command : commande originale (pour les messages d'erreur)

        Returns:
            { "success": bool, "message": str, "data": dict | None }
        """
        logger.info(f"Exécution → intent={intent}, params={params}")
        self._raw_command_agent = agent

        handler = self._handlers.get(intent)
        if handler is None:
            return self._err(
                f"Intention inconnue : '{intent}'. "
                f"({len(self._handlers)} intentions supportées)"
            )

        try:
            result = handler(params)
            
            # Détecter si l'utilisateur demande explicitement les détails/tableau
            should_show_display = self._user_asked_for_details(raw_command)
            if result and isinstance(result, dict):
                result["_show_display"] = should_show_display

            # Robustesse: certains handlers legacy peuvent renvoyer autre chose qu'un dict.
            if isinstance(result, dict):
                # Flag déjà ajouté plus haut si présent
                return result
            if result is None:
                return self._err(f"{intent}: aucun resultat renvoye.")
            if isinstance(result, str):
                return self._err(result or f"{intent}: resultat texte invalide")

            return self._err(
                f"{intent}: type de retour invalide ({type(result).__name__})"
            )
        except Exception as e:
            logger.error(f"Erreur exécution intent={intent} : {e}", exc_info=True)
            return self._err(f"Erreur lors de l'exécution de '{intent}' : {str(e)}")

    def _user_asked_for_details(self, raw_command: str) -> bool:
        """Detecte si user demande explicitement les details/tableau."""
        if not raw_command:
            return False
        lower = raw_command.lower()
        keywords = ["affiche", "tableau", "detail", "montre", "full", "complet", "exhaustif"]
        return any(kw in lower for kw in keywords)

    # ══════════════════════════════════════════════════════════════════════════
    #  SYSTÈME
    # ══════════════════════════════════════════════════════════════════════════

    def _system_time(self, p):
        from datetime import datetime
        import time

        # Fuseau horaire de l'utilisateur si précisé, sinon heure locale du PC
        tz_name = p.get("timezone", "")

        try:
            if tz_name:
                # Avec pytz si dispo
                try:
                    import pytz
                    tz = pytz.timezone(tz_name)
                    now = datetime.now(tz)
                except Exception:
                    now = datetime.now()
            else:
                now = datetime.now()

            heure    = now.strftime("%H:%M")
            date_str = now.strftime("%A %d %B %Y")
            ts       = int(now.timestamp())

            msg = f"Il est {heure}, le {date_str}."
            return self._ok(msg, {
                "time":    heure,
                "date":    date_str,
                "timestamp": ts,
            })

        except Exception as e:
            return self._err(f"Impossible de lire l'heure : {e}")

    def _system_shutdown(self, p):
        return self.sc.shutdown(delay=p.get("delay_seconds", 10))

    def _system_restart(self, p):
        return self.sc.restart(delay=p.get("delay_seconds", 10))

    def _system_sleep(self, p):
        return self._power_sleep(p)

    def _system_hibernate(self, p):
        return self._power_hibernate(p)

    def _system_lock(self, p):
        return self.sc.lock_screen()

    def _system_unlock(self, p):
        return self._err(
            "Déverrouillage d'ecran non supporte pour des raisons de securite. "
            "Jarvis peut verrouiller la session, mais pas la deverrouiller automatiquement."
        )

    def _system_logout(self, p):
        return self.sc.logout()

    def _system_info(self, p):
        return self.sc.system_info()

    def _system_disk(self, p):
        return self.sc.disk_info()

    def _system_processes(self, p):
        return self.sc.list_processes(sort_by=p.get("sort_by", "cpu"))

    def _system_kill(self, p):
        target = p.get("target") or p.get("name") or p.get("pid")
        if not target:
            return self._err("Précise le nom ou PID du processus à fermer.")
        return self.sc.kill_process(target)

    def _system_network(self, p):
        return self.sc.network_info()

    def _system_temperature(self, p):
        return self.sc.temperature_info()

    def _system_full_report(self, p):
        return self.sc.full_system_report()

    def _system_task_manager(self, p):
        return self.sc.open_task_manager()

    def _system_cancel_shutdown(self, p):
        return self._power_cancel(p)

    def _power_sleep(self, p):
        return self.power.sleep()

    def _power_hibernate(self, p):
        return self.power.hibernate()

    def _power_cancel(self, p):
        return self.power.cancel_shutdown()

    def _power_state(self, p):
        return self.power.get_state()

    def _wake_on_lan(self, p):
        mac = p.get("mac_address") or p.get("mac") or ""
        broadcast = p.get("broadcast") or "255.255.255.255"
        port = int(p.get("port", 9))
        if not mac:
            return self._err("Precise l'adresse MAC a reveiller.")
        return self.power.wake_on_lan(mac_address=mac, broadcast=broadcast, port=port)

    def _screen_unlock(self, p):
        password = p.get("password") or ""
        return self.power.unlock(password=password)

    def _screen_off(self, p):
        return self.power.turn_off_display()

    # ══════════════════════════════════════════════════════════════════════════
    #  RESEAU (Semaine 9)
    # ══════════════════════════════════════════════════════════════════════════

    def _wifi_list(self, p):
        return self.nm.list_wifi_networks()

    def _wifi_connect(self, p):
        ssid = p.get("ssid") or p.get("name") or ""
        password = p.get("password") or p.get("pass") or ""
        if not ssid:
            return self._err("Precise le SSID du reseau Wi-Fi.")
        return self.nm.connect_wifi(ssid=ssid, password=password)

    def _wifi_disconnect(self, p):
        return self.nm.disconnect_wifi()

    def _wifi_enable(self, p):
        return self.nm.enable_wifi()

    def _wifi_disable(self, p):
        return self.nm.disable_wifi()

    def _bluetooth_enable(self, p):
        return self.nm.enable_bluetooth()

    def _bluetooth_disable(self, p):
        return self.nm.disable_bluetooth()

    def _bluetooth_list(self, p):
        return self.nm.list_bluetooth_devices()

    def _network_info(self, p):
        return self.nm.get_network_info()

    # ══════════════════════════════════════════════════════════════════════════
    #  APPLICATIONS
    # ══════════════════════════════════════════════════════════════════════════
    
    def _app_open(self, p):
        app  = p.get("app_name") or p.get("name") or ""
        args = p.get("args", [])
        force = bool(p.get("force", False))

        if not app:
            return self._err("Précise le nom de l'application à ouvrir.")

        # Si force=True (l'utilisateur a confirmé), on ouvre directement
        if force:
            return self.am.open_app(app, args=args)

        # Vérifier si déjà ouverte
        check = self.am.check_app(app)
        already_open = (
            check.get("data", {}).get("running", False)
            if check.get("success") else False
        )

        if already_open:
            return self._ok(
                f"'{app}' est déjà ouverte. Tu veux quand même en ouvrir une nouvelle fenêtre ?",
                {
                    "awaiting_choice": True,
                    "pending_intent":  "APP_OPEN",
                    "pending_params":  {"app_name": app, "args": args, "force": True},
                    "app_name":        app,
                    "already_open":    True,
                    "choices":         ["oui", "non"],
                }
            )

        # Chrome fermé → on l'ouvre directement
        return self.am.open_app(app, args=args)

    def _app_close(self, p):
        app = p.get("app_name") or p.get("name") or ""
        if not app:
            return self._err("Précise le nom de l'application à fermer.")
        return self.am.close_app(app)

    def _app_restart(self, p):
        app = p.get("app_name") or p.get("name") or ""
        if not app:
            return self._err("Précise le nom de l'application à redémarrer.")
        return self.am.restart_app(app)

    def _app_check(self, p):
        app = p.get("app_name") or p.get("name") or ""
        if not app:
            return self._err("Précise le nom de l'application à vérifier.")
        return self.am.check_app(app)

    def _app_list_running(self, p):
        return self.am.list_running_apps()

    def _app_list_known(self, p):
        return self.am.list_known_apps()

    # ══════════════════════════════════════════════════════════════════════════
    #  FICHIERS
    # ══════════════════════════════════════════════════════════════════════════

    def _file_search(self, p):
        query = p.get("query") or p.get("name") or ""
        if not query:
            return self._err("Précise le nom du fichier à chercher.")
        result = self.fm.search_file(
            query,
            search_dirs=p.get("search_dirs"),
            max_results=int(p.get("max_results", 20)),
        )
        return self._normalize_file_search_result(result)

    def _file_search_type(self, p):
        ext = p.get("extension") or p.get("type") or ""
        if not ext:
            return self._err("Précise le type de fichier (ex: .pdf, documents).")
        result = self.fm.search_by_type(
            ext,
            search_dirs=p.get("search_dirs"),
            max_results=int(p.get("max_results", 50)),
        )
        return self._normalize_file_search_result(result)

    def _file_search_content(self, p):
        kw = p.get("keyword") or p.get("word") or p.get("query") or ""
        if not kw:
            return self._err("Précise le mot à chercher dans les fichiers.")
        result = self.fm.search_by_content(
            kw,
            search_dirs=p.get("search_dirs"),
            max_results=int(p.get("max_results", 20)),
        )
        return self._normalize_file_search_result(result)

    def _file_open(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le chemin ou nom du fichier à ouvrir.")

        # ── PRIORITÉ 1 : mémoire persistante ─────────────────────────────────
        # Si on a un chemin mémorisé récemment qui correspond, on l'utilise
        # avant de faire confiance à un chemin reconstruit par Groq.
        if self._raw_command_agent is not None:
            memory = self._raw_command_agent._memory
            path_name = Path(path).name.lower() if path else ""

            for category in ("folder", "file"):
                last = memory.recall_last(category)
                if not isinstance(last, dict) or not last:
                    continue

                last_path = str(last.get("resolved_path") or last.get("path") or "")
                last_name = str(last.get("name") or "").lower()
                if not last_path:
                    continue

                if (
                    last_name and path_name and
                    (
                        last_name == path_name or
                        last_name in path_name or
                        path_name in last_name
                    )
                ):
                    real_path = Path(last_path)
                    if real_path.exists():
                        logger.info(f"FILE_OPEN depuis mémoire persistante : {last_path}")
                        p = dict(p)
                        p["path"] = last_path
                        path = last_path
                        break

        is_just_name = (
            os.sep not in path and
            "/" not in path and
            "\\" not in path
        )

        # Si c'est juste un nom → chercher dans la mémoire (compat legacy)
        if is_just_name and self._raw_command_agent is not None:
            memory = self._raw_command_agent._memory
            for category in ("folder", "file"):
                last = memory.recall_last(category)
                if not isinstance(last, dict) or not last:
                    continue
                last_name = str(last.get("name", "")).lower()
                last_path = str(last.get("resolved_path") or last.get("path") or "")
                query_name = path.lower()
                if (
                    (last_name and query_name == last_name)
                    or (last_name and query_name in last_name)
                    or (last_name and last_name in query_name)
                ) and last_path and Path(last_path).exists():
                    logger.info(f"FILE_OPEN depuis mémoire : {last_path}")
                    return self.fm.open_file(
                        last_path,
                        target_type=p.get("target_type", "any"),
                        current_dir=p.get("current_dir"),
                    )

        # Chemin incomplet → chercher sur disque et ouvrir directement si un seul match
        if is_just_name:
            search_result = self._normalize_file_search_result(
                self.fm.search_file(
                    path,
                    search_dirs=p.get("search_dirs"),
                    max_results=5,
                )
            )
            if search_result.get("success"):
                results = (search_result.get("data") or {}).get("results") or []
                target_type = p.get("target_type", "any")
                if target_type == "directory":
                    results = [r for r in results if r.get("is_dir")]
                elif target_type == "file":
                    results = [r for r in results if not r.get("is_dir")]

                if len(results) == 1:
                    found_path = results[0].get("path", path)
                    return self.fm.open_file(
                        found_path,
                        target_type=target_type,
                        current_dir=p.get("current_dir"),
                    )
                if len(results) > 1:
                    choices = [
                        {
                            "path": r.get("path", ""),
                            "name": r.get("name", ""),
                            "is_dir": r.get("is_dir", False),
                        }
                        for r in results[:5]
                    ]
                    lines = [
                        f"J'ai trouvé {len(results)} résultats pour '{path}'. Lequel ?"
                    ]
                    for i, r in enumerate(results[:5], 1):
                        icon = "📁" if r.get("is_dir") else "📄"
                        lines.append(f"  {i}. {icon} {r.get('name')} — {r.get('path')}")
                    return self._ok(
                        "\n".join(lines),
                        {
                            "awaiting_choice": True,
                            "pending_intent": "FILE_OPEN",
                            "choices": choices,
                        }
                    )

        # Chemin direct
        return self.fm.open_file(
            path,
            search_dirs=p.get("search_dirs"),
            target_type=p.get("target_type", "any"),
            current_dir=p.get("current_dir"),
        )

    def _file_close(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le fichier, dossier ou élément à fermer.")
        return self.fm.close_file(
            path,
            current_dir=p.get("current_dir"),
            window_title=p.get("window_title"),
        )

    def _window_close(self, p):
        query = p.get("query") or p.get("title") or p.get("path") or p.get("name") or ""
        return self.window.close_window(
            query=query,
            preferred_kind=p.get("preferred_kind"),
            close_scope=p.get("close_scope"),
            hwnd=p.get("hwnd"),
            pid=p.get("pid"),
            title=p.get("title"),
            title_candidates=p.get("title_candidates"),
        )

    def _file_copy(self, p):
        src = p.get("src") or p.get("source") or ""
        dst = p.get("dst") or p.get("destination") or ""
        if not src or not dst:
            return self._err("Précise la source et la destination. Ex: copie a.txt vers C:/Backup")
        return self.fm.copy_file(src, dst)

    def _file_move(self, p):
        src = p.get("src") or p.get("source") or ""
        dst = p.get("dst") or p.get("destination") or ""
        if not src or not dst:
            return self._err("Précise la source et la destination.")
        return self.fm.move_file(src, dst)

    def _file_rename(self, p):
        path     = p.get("path") or p.get("src") or ""
        new_name = p.get("new_name") or p.get("name") or ""
        if not path or not new_name:
            return self._err("Précise le fichier et le nouveau nom.")
        return self.fm.rename_file(path, new_name)

    def _file_delete(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le fichier à supprimer.")
        return self.fm.delete_file(path)

    def _file_info(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le chemin du fichier.")
        return self.fm.get_file_info(path)

    def _folder_list(self, p):
        path = p.get("path") or p.get("folder") or None
        result = self.fm.list_folder(path)

        # S'assurer que le chemin résolu est dans data pour la mémoire
        if result.get("success"):
            data = result.get("data") or {}
            resolved = data.get("path")

            # Si le module renvoie un chemin ambigu, tenter une résolution stable
            if path and (not resolved or str(resolved).startswith(('/', '\\')) and len(str(resolved)) <= 3):
                candidate_name = str(path).lstrip('/\\')
                search_roots = [
                    Path.home(),
                    Path.home() / "Documents",
                    Path.home() / "Desktop",
                    Path("C:/"),
                    Path("D:/"),
                    Path("E:/"),
                ]
                for root in search_roots:
                    candidate = root / candidate_name
                    if candidate.exists() and candidate.is_dir():
                        resolved = str(candidate)
                        break

            if resolved:
                out = dict(result)
                out_data = dict(data)
                out_data["resolved_path"] = resolved
                out_data["path"] = resolved
                out_data["resolved"] = True
                out["data"] = out_data
                return out

        return result

    def _folder_create(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le chemin du dossier à créer.")
        return self.fm.create_folder(path)

    # ══════════════════════════════════════════════════════════════════════════
    #                                 NAVIGATEUR 
    # ══════════════════════════════════════════════════════════════════════════
        
    def _browser_open(self, p):
        return self.bc.open_browser(browser=p.get("browser"), url=p.get("url", ""))

    def _browser_close(self, p):
        return self.bc.close_browser()

    def _browser_url(self, p):
        return self.bc.open_url(p.get("url", ""), new_tab=bool(p.get("new_tab", False)))

    def _browser_new_tab(self, p):
        count = int(p.get("count") or 1)
        url = p.get("url", "")
        if count == 1:
            return self.bc.open_new_tab(url)
        results = [self.bc.open_new_tab(url) for _ in range(count)]
        ok = sum(1 for r in results if r["success"])
        return self._ok(f"{ok}/{count} onglet(s) ouvert(s).", {"count": ok})

    def _browser_back(self, p):
        return self.bc.go_back(index=p.get("index"))

    def _browser_forward(self, p):
        return self.bc.go_forward(index=p.get("index"))

    def _browser_reload(self, p):
        return self.bc.reload_tab(hard=bool(p.get("hard", False)), index=p.get("index"))

    def _browser_close_tab(self, p):
        return self.bc.close_tab(index=p.get("index"), query=p.get("query", ""))

    def _browser_search(self, p):
        return self.bc.google_search(
            query=p.get("query", ""),
            engine=p.get("engine", "google"),
            new_tab=bool(p.get("new_tab", False)),
        )

    def _browser_search_youtube(self, p):
        return self.bc.search_youtube(p.get("query", ""))

    def _browser_search_github(self, p):
        return self.bc.search_github(p.get("query", ""))

    def _browser_open_result(self, p):
        return self.bc.open_search_result(rank=int(p.get("rank", 1)), new_tab=bool(p.get("new_tab", False)))

    def _browser_list_results(self, p):
        return self.bc.extract_search_results()

    def _browser_go_to_site(self, p):
        return self.bc.go_to_site(site=p.get("site", ""), query=p.get("query", ""))

    def _browser_navigate(self, p):
        return self.bc.navigate_to(p.get("url", ""))

    def _browser_read(self, p):
        return self.bc.read_page(index=p.get("index"))

    def _browser_page_info(self, p):
        return self.bc.get_page_info()

    def _browser_extract_links(self, p):
        return self.bc.extract_links()

    def _browser_summarize(self, p):
        return self.bc.summarize_page(index=p.get("index"))

    def _browser_scroll(self, p):
        return self.bc.scroll(
            direction=p.get("direction", "down"),
            amount=p.get("amount"),
            index=p.get("index"),
        )

    def _browser_click_text(self, p):
        return self.bc.click_text(text=p.get("text", ""))

    def _browser_fill_field(self, p):
        return self.bc.fill_form_field(
            selector=p.get("selector", ""),
            value=p.get("value", ""),
            submit=bool(p.get("submit", False)),
        )

    def _browser_type(self, p):
        return self.bc.smart_type(text=p.get("text", ""), submit=bool(p.get("submit", False)))

    def _browser_download(self, p):
        return self.bc.download_file(url=p.get("url", ""), link_text=p.get("link_text", ""))

    def _browser_list_tabs(self, p):
        return self.bc.list_tabs()

    def _browser_switch_tab(self, p):
        return self.bc.switch_tab(index=p.get("index"), query=p.get("query", ""))

    def _browser_find_and_open(self, p):
        return self.bc.find_best_and_open(query=p.get("query", ""))

    def _browser_context(self, p):
        return self.bc.get_browser_context()
     
    # ══════════════════════════════════════════════════════════════════════════
    #                               AUDIO
    # ══════════════════════════════════════════════════════════════════════════
        
    def _audio_volume_up(self, p):
        return self.au.volume_up(int(p.get("step", 10)))
 
    def _audio_volume_down(self, p):
        return self.au.volume_down(int(p.get("step", 10)))

    def _audio_volume_set(self, p):
        level = p.get("level")
        if level is None:
            return self._err("Precise un niveau de volume (0-100).")
        return self.au.set_volume(int(level))
 
    def _audio_mute(self, p):
        return self.au.mute()
 
    def _audio_play(self, p):
        """
        Jouer audio — délègue à MusicManager si disponible (semaine 3),
        sinon fallback AudioManager (ouvre l'app par défaut).
        """
        query = p.get("query") or p.get("title") or p.get("name") or ""
        if not query:
            return self._err("Précise le nom d'une chanson ou d'un artiste.")
        # Essayer MusicManager si disponible
        try:
            music = self.music
            if music is not None:
                return music.play(query)
        except Exception:
            pass
        # Fallback : ouvrir avec l'application par défaut
        return self.au.play(query)
    
    # ══════════════════════════════════════════════════════════════════════════
    #  MUSIQUE — Semaine 3 (stubs intelligents pour semaine 2)
    #  Ces handlers délèguent au module music/ dès qu'il sera créé.
    #  En attendant : fallback AudioManager ou message informatif.
    # ══════════════════════════════════════════════════════════════════════════

    def _music_play(self, p):
        """Jouer une musique — délègue à MusicManager si disponible, sinon AudioManager."""
        query = p.get("query") or p.get("title") or p.get("name") or ""
        if not query:
            return self._err("Précise le nom d'une chanson, d'un artiste ou d'une playlist.")
        # Essayer MusicManager (semaine 3) d'abord
        try:
            music = self.music
            if music is not None:
                return music.play(query)
        except Exception:
            pass
        # Fallback : AudioManager.play()
        return self.au.play(query)

    def _music_pause(self, p):
        """Pause musique — délègue à MusicManager ou AudioManager."""
        try:
            music = self.music
            if music is not None:
                return music.pause()
        except Exception:
            pass
        return self.au.pause()

    def _music_resume(self, p):
        """Reprendre la lecture."""
        try:
            music = self.music
            if music is not None:
                return music.resume()
        except Exception:
            pass
        return self.au.pause()  # toggle pause/resume sur AudioManager

    def _music_stop(self, p):
        """Arrêter la musique."""
        try:
            music = self.music
            if music is not None:
                return music.stop()
        except Exception:
            pass
        return self.au.stop()

    def _music_next(self, p):
        """Piste suivante."""
        try:
            music = self.music
            if music is not None:
                return music.next_track()
        except Exception:
            pass
        return self.au.next_track()

    def _music_prev(self, p):
        """Piste précédente."""
        try:
            music = self.music
            if music is not None:
                return music.prev_track()
        except Exception:
            pass
        return self.au.prev_track()

    def _music_volume(self, p):
        """Volume musique."""
        level = p.get("level")
        if level is None:
            return self._err("Précise un niveau de volume (0-100).")
        try:
            music = self.music
            if music is not None and hasattr(music, "set_volume"):
                return music.set_volume(int(level))
        except Exception:
            pass
        return self.au.set_volume(int(level))

    def _music_shuffle(self, p):
        """Activer/désactiver lecture aléatoire."""
        try:
            music = self.music
            if music is not None:
                return music.toggle_shuffle()
        except Exception:
            pass
        return self._ok("Mode aléatoire — disponible avec le module musique (semaine 3).", {})

    def _music_repeat(self, p):
        """Activer/désactiver répétition."""
        try:
            music = self.music
            if music is not None:
                return music.toggle_repeat()
        except Exception:
            pass
        return self._ok("Répétition — disponible avec le module musique (semaine 3).", {})

    def _music_current(self, p):
        """Quelle musique joue actuellement."""
        try:
            music = self.music
            if music is not None:
                return music.current_song()
        except Exception:
            pass
        return self._ok("Information sur la musique en cours — disponible avec le module musique (semaine 3).", {})

    def _music_playlist_create(self, p):
        """Créer une playlist."""
        name = p.get("name") or ""
        if not name:
            return self._err("Précise le nom de la playlist à créer.")
        try:
            music = self.music
            if music is not None:
                return music.create_playlist(name)
        except Exception:
            pass
        return self._ok(
            f"Création de playlist '{name}' — disponible avec le module musique (semaine 3).",
            {"name": name, "pending": True}
        )

    def _music_playlist_play(self, p):
        """Jouer une playlist."""
        name = p.get("name") or ""
        if not name:
            return self._err("Précise le nom de la playlist à jouer.")
        try:
            music = self.music
            if music is not None:
                return music.play_playlist(name)
        except Exception:
            pass
        return self._ok(
            f"Lecture playlist '{name}' — disponible avec le module musique (semaine 3).",
            {"name": name, "pending": True}
        )

    def _music_playlist_list(self, p):
        """Lister les playlists."""
        try:
            music = self.music
            if music is not None:
                return music.list_playlists()
        except Exception:
            pass
        return self._ok(
            "Liste des playlists — disponible avec le module musique (semaine 3).",
            {"playlists": [], "count": 0}
        )

    def _music_library_scan(self, p):
        """Scanner la bibliothèque musicale."""
        path = p.get("path") or ""
        try:
            music = self.music
            if music is not None:
                return music.scan_library(path or None)
        except Exception:
            pass
        # Fallback : utiliser AudioManager.list_music()
        dirs = [path] if path else None
        return self.au.list_music(music_dirs=dirs)

    # ══════════════════════════════════════════════════════════════════════════
    #                               DOCUMENTS
    # ══════════════════════════════════════════════════════════════════════════

    def _doc_read(self, p):
        path = p.get("path") or p.get("name") or p.get("file") or ""
        if not path:
            return self._err("Précise le chemin ou nom du document à lire.")
        return self.dr.read(path)
    
    def _doc_summarize(self, p):
        path = p.get("path") or p.get("name") or p.get("file") or ""
        if not path:
            return self._err("Précise le document à résumer.")
        lang = p.get("language", "français")
        return self.dr.summarize(path, language=lang)

    def _doc_search_word(self, p):
        path = p.get("path") or p.get("file") or ""
        word = p.get("word") or p.get("keyword") or p.get("query") or ""
        if not path:
            return self._err("Précise le document dans lequel chercher.")
        if not word:
            return self._err("Précise le mot à chercher.")
        return self.dr.search_word(path, word)

    # ══════════════════════════════════════════════════════════════════════════
    #  ÉCRAN (stubs — Semaine 9)
    # ══════════════════════════════════════════════════════════════════════════

    def _screen_capture(self, p):
        try:
            from modules.screen_manager import ScreenManager
            monitor = int(p.get("monitor", 1))
            send_to_phone = bool(p.get("send_to_phone", False))
            return ScreenManager().capture_screen(send_to_phone=send_to_phone, monitor=monitor)
        except Exception:
            return self._err("Capture d'ecran indisponible.")

    def _screenshot_to_phone(self, p):
        try:
            from modules.screen_manager import ScreenManager
            path = p.get("path") or p.get("image_path") or ""
            share_mode = p.get("mode") or ""
            return ScreenManager().send_screenshot_to_phone(path, share_mode=share_mode)
        except Exception as e:
            return self._err(f"Envoi capture au telephone echoue : {e}")

    def _screen_brightness(self, p):
        level = p.get("level")
        if level is None:
            return self._err("Precise un niveau de luminosite (0-100).")
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().set_brightness(int(level))
        except Exception as e:
            return self._err(f"Reglage luminosite echoue : {e}")

    def _screen_info(self, p):
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().get_screen_info()
        except Exception as e:
            return self._err(f"Lecture infos ecran echouee : {e}")

    def _screen_record(self, p):
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().record_screen(duration=p.get("duration", 30))
        except Exception:
            return self._err(f"Enregistrement ecran {p.get('duration', 30)}s indisponible.")

    # ══════════════════════════════════════════════════════════════════════════
    #  HISTORIQUE / MACROS (Semaine 11)
    # ══════════════════════════════════════════════════════════════════════════

    def _repeat_last(self, p):
        if self._raw_command_agent is None:
            return self._err("Replay indisponible: agent manquant.")
        return self.history.replay_last(self._raw_command_agent)

    def _history_show(self, p):
        count = int(p.get("count", 10))
        text = self.history.format_recent(count)
        return self._ok("Historique recent.", {"display": text, "entries": self.history.get_last(count)})

    def _history_clear(self, p):
        return self.history.clear()

    def _history_search(self, p):
        keyword = p.get("keyword") or p.get("query") or ""
        if not keyword:
            return self._err("Precise un mot-cle pour chercher dans l'historique.")
        results = self.history.search(keyword=keyword, limit=int(p.get("limit", 20)))
        return self._ok(f"{len(results)} resultat(s) trouves.", {"results": results, "keyword": keyword})

    def _macro_run(self, p):
        name = p.get("name") or p.get("macro") or ""
        if not name:
            return self._err("Precise le nom de la macro a lancer.")
        if self._raw_command_agent is None:
            return self._err("Execution macro indisponible: agent manquant.")
        return self.macros.run(name=name, agent=self._raw_command_agent)

    def _macro_list(self, p):
        return self.macros.list_macros()

    def _macro_save(self, p):
        name = p.get("name") or ""
        commands = p.get("commands") or []
        if isinstance(commands, str):
            commands = [c.strip() for c in commands.split(",") if c.strip()]
        return self.macros.save_macro(
            name=name,
            commands=commands,
            description=p.get("description", ""),
            delay_between=float(p.get("delay_between", 1.0)),
            stop_on_error=bool(p.get("stop_on_error", False)),
        )

    def _macro_delete(self, p):
        name = p.get("name") or p.get("macro") or ""
        if not name:
            return self._err("Precise le nom de la macro a supprimer.")
        return self.macros.delete_macro(name)

    # ══════════════════════════════════════════════════════════════════════════
    #  AIDE & INCONNU
    # ══════════════════════════════════════════════════════════════════════════

    def _greeting(self, p):
        import random
        responses = [
            "Bonjour ! Je suis JARVIS. Dis-moi ce que tu veux faire.",
            "Salut ! JARVIS à ton service. Qu'est-ce que je peux faire pour toi ?",
            "Bonjour ! Prêt à t'aider. Une commande ?",
            "Hello ! JARVIS opérationnel. Qu'est-ce qu'on fait ?",
        ]
        return self._ok(random.choice(responses), {})
    
    def _memory_show(self, p):
        if self._raw_command_agent is None:
            return self._err("Agent manquant.")
        summary = self._raw_command_agent._memory.get_full_summary()
        return self._ok("Voici ce dont je me souviens.", {"display": summary})

    def _incomplete(self, p):
        missing = str(p.get("missing", "plus de détails"))
        suggested = str(p.get("suggested_intent", ""))

        if "recherche" in missing.lower() or "SEARCH" in suggested:
            question = "Tu veux chercher quoi ? Et où — sur le web, dans tes fichiers, ou dans un document ?"
            choices = ["sur le web", "dans mes fichiers", "dans un document"]
        elif "fichier" in missing.lower() or "FILE" in suggested:
            question = "Quel fichier veux-tu ouvrir ? Donne-moi son nom."
            choices = []
        elif "volume" in missing.lower() or "VOLUME" in suggested:
            question = "À quel niveau veux-tu mettre le volume ? (0-100)"
            choices = []
        elif "wifi" in missing.lower() or "WIFI" in suggested:
            question = "Quel réseau WiFi veux-tu rejoindre ?"
            choices = []
        elif "application" in missing.lower() or "APP" in suggested:
            question = "Quelle application veux-tu ouvrir ?"
            choices = []
        else:
            question = f"Il me manque une information : {missing}. Tu peux préciser ?"
            choices = []

        return self._ok(
            question,
            {
                "awaiting_choice": bool(choices),
                "choices": choices,
                "missing": missing,
                "suggested_intent": suggested,
                "incomplete": True,
            }
        )

    def _help(self, p):
        lines = [
            "Je suis JARVIS, ton assistant IA personnel.",
            "Je contrôle ton PC depuis ton téléphone — voix ou texte.",
            "",
            "Voici tout ce que je sais faire :",
            "",
            "  SYSTÈME",
            "    → Éteindre, redémarrer, veille, hibernation, verrouiller",
            "    → Infos CPU / RAM / disque / température / réseau",
            "    → Lister et fermer des processus",
            "",
            "  APPLICATIONS",
            "    → Ouvrir, fermer, redémarrer une application",
            "    → Vérifier si une app est ouverte",
            "",
            "  FICHIERS & DOSSIERS",
            "    → Chercher, ouvrir, copier, déplacer, renommer, supprimer",
            "    → Créer un dossier, lister le contenu",
            "",
            "  NAVIGATEUR (Chrome)",
            "    → Ouvrir, fermer, nouvel onglet, changer d'onglet",
            "    → Rechercher sur Google / YouTube / GitHub",
            "    → Lire et résumer une page web",
            "    → Scroller, cliquer, remplir des formulaires",
            "    → Télécharger un fichier",
            "    → Navigation autonome : 'trouve le meilleur tuto Python et ouvre-le'",
            "",
            "  AUDIO",
            "    → Monter / baisser / régler le volume",
            "    → Couper le son",
            "",
            "  DOCUMENTS",
            "    → Lire un fichier Word ou PDF",
            "    → Résumer ou chercher un mot dans un document",
            "",
            "  RÉSEAU",
            "    → Voir les réseaux Wi-Fi, se connecter / déconnecter",
            "    → Activer / désactiver Bluetooth",
            "",
            "  MACROS",
            "    → 'mode travail', 'mode nuit', 'mode cinéma'",
            "    → Créer tes propres séquences automatisées",
            "",
            "  HISTORIQUE",
            "    → Voir les dernières commandes",
            "    → Répéter la dernière commande",
            "    → Chercher dans l'historique",
            "",
            "Parle-moi naturellement en français ou en anglais.",
            "Exemple : 'va sur YouTube et cherche du lofi',",
            "          'résume cette page', 'éteins le PC dans 10 minutes'",
        ]
        return self._ok(
            "Je suis JARVIS — voici tout ce que je sais faire.",
            {"display": "\n".join(lines)},
        )

    def _knowledge_qa(self, p):
        # Cette intention est normalement geree directement par Agent, sans execution.
        return self._ok("Reponse directe traitee.", {"mode": "knowledge_qa"})

    def _unknown(self, p):
        return self._err(
            "Je n'ai pas compris cette commande. "
            "Tape 'aide' pour voir tout ce que je sais faire.",
            {"tip": "Essaie des formulations comme : 'ouvre chrome', 'quel est l'état du système', "
                    "'cherche les fichiers PDF'"}
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  PROPRIÉTÉS LAZY — modules instanciés à la demande
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def sc(self):
        if self._sc is None:
            from modules.system_control import SystemControl
            self._sc = SystemControl()
        return self._sc

    @property
    def am(self):
        if self._am is None:
            from modules.app_manager import AppManager
            self._am = AppManager()
        return self._am

    @property
    def fm(self):
        if self._fm is None:
            from modules.file_manager import FileManager
            self._fm = FileManager()
        return self._fm
    
    @property
    def bc(self):
        """BrowserControl"""
        if self._bc is None:
            from modules.browser.browser_control import BrowserControl
            self._bc = BrowserControl()
        return self._bc
 
    @property
    def au(self):
        """AudioManager"""
        if self._au is None:
            from modules.audio_manager import AudioManager
            self._au = AudioManager()
        return self._au
 
    @property
    def dr(self):
        """DocReader"""
        if self._dr is None:
            from modules.doc_reader import DocReader
            self._dr = DocReader()
        return self._dr

    @property
    def nm(self):
        """NetworkManager"""
        if self._nm is None:
            from modules.network_manager import NetworkManager
            self._nm = NetworkManager()
        return self._nm

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
    def power(self):
        if self._power is None:
            from modules.power_manager import PowerManager
            self._power = PowerManager()
        return self._power

    @property
    def window(self):
        if self._window is None:
            from modules.window_manager import WindowManager
            self._window = WindowManager()
        return self._window
    
    @property
    def music(self):
        """
        MusicManager (semaine 3) — lazy init.
        Retourne None si le module n'est pas encore développé.
        Dès que modules/music/music_manager.py existera, il sera utilisé auto.
        """
        if self._music is None:
            try:
                from modules.music.music_manager import MusicManager
                self._music = MusicManager()
            except (ImportError, Exception):
                # Module pas encore créé — normal en semaine 2
                return None
        return self._music

    def _normalize_file_search_result(self, result: dict) -> dict:
        """Normalise les résultats de recherche fichier en liste de dicts sous data.results."""
        if not isinstance(result, dict):
            return result
        if not result.get("success"):
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return result

        raw_items = data.get("results")
        if raw_items is None:
            raw_items = data.get("files")
        if not isinstance(raw_items, list):
            return result

        normalized_items = []
        for item in raw_items:
            if isinstance(item, dict):
                normalized_items.append(item)
                continue

            path = str(item or "")
            is_dir = False
            name = Path(path).name if path else ""
            parent = str(Path(path).parent) if path else ""
            normalized_items.append(
                {
                    "path": path,
                    "name": name,
                    "is_dir": is_dir,
                    "parent": parent,
                }
            )

        out = dict(result)
        out_data = dict(data)
        out_data["results"] = normalized_items
        if "files" not in out_data:
            out_data["files"] = normalized_items
        if "count" not in out_data:
            out_data["count"] = len(normalized_items)
        out["data"] = out_data
        return out
 
    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}
    