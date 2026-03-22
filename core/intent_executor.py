"""
intent_executor.py — Exécuteur d'intentions
Reçoit un intent + params structurés depuis CommandParser
et appelle la bonne fonction du bon module.

SEMAINE 4 — MERCREDI
  Mapping COMPLET : 50+ intentions → modules système, apps, fichiers, navigateur, audio

CORRECTION 1 (Semaine 2 rétroactive) :
  Alignement des noms de méthodes Browser entre intent_executor ↔ browser_control.
  19 méthodes _browser_* corrigées.
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
        self._sc      = None   # SystemControl
        self._am      = None   # AppManager
        self._fm      = None   # FileManager
        self._bc      = None   # BrowserControl
        self._au      = None   # AudioManager
        self._nm      = None   # NetworkManager
        self._dr      = None   # DocReader
        self._history = None
        self._macros  = None
        self._power   = None
        self._window  = None
        self._music   = None
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
            "PREFERENCE_SET":         self._preference_set,
            # ── Réseau ───────────────────────────────────────────────────────
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
            # ── Navigateur ────────────────────────────────────────────────────
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
            # ── Musique ───────────────────────────────────────────────────────
            "MUSIC_PLAY":            self._music_play,
            "MUSIC_PAUSE":           self._music_pause,
            "MUSIC_RESUME":          self._music_resume,
            "MUSIC_STOP":            self._music_stop,
            "MUSIC_NEXT":            self._music_next,
            "MUSIC_PREV":            self._music_prev,
            "MUSIC_VOLUME":          self._music_volume,
            "MUSIC_SHUFFLE":         self._music_shuffle,
            "MUSIC_REPEAT":          self._music_repeat,
            "MUSIC_CURRENT":         self._music_current,
            "MUSIC_PLAYLIST_CREATE": self._music_playlist_create,
            "MUSIC_PLAYLIST_PLAY":   self._music_playlist_play,
            "MUSIC_PLAYLIST_LIST":         self._music_playlist_list,
            "MUSIC_PLAYLIST_DELETE":       self._music_playlist_delete,
            "MUSIC_PLAYLIST_CLEAR":        self._music_playlist_clear,
            "MUSIC_PLAYLIST_REMOVE_SONG":  self._music_playlist_remove_song,
            "MUSIC_PLAYLIST_RENAME":       self._music_playlist_rename,
            "MUSIC_PLAYLIST_DUPLICATE":    self._music_playlist_duplicate,
            "MUSIC_PLAYLIST_EXPORT":       self._music_playlist_export,
            "MUSIC_PLAYLIST_IMPORT":       self._music_playlist_import,
            "MUSIC_PLAYLIST_MERGE":        self._music_playlist_merge,
            "MUSIC_PLAYLIST_MOVE_SONG":    self._music_playlist_move_song,
            "MUSIC_QUEUE_ADD":             self._music_queue_add,
            "MUSIC_QUEUE_ADD_PLAYLIST":    self._music_queue_add_playlist,
            "MUSIC_QUEUE_LIST":            self._music_queue_list,
            "MUSIC_QUEUE_CLEAR":           self._music_queue_clear,
            "MUSIC_QUEUE_PLAY":            self._music_queue_play,
            "MUSIC_LIBRARY_SCAN":          self._music_library_scan,
            "MUSIC_PLAYLIST_ADD_FOLDER":   self._music_playlist_add_folder,
            "MUSIC_PLAYLIST_ADD_SONG":     self._music_playlist_add_song,
            # ── Documents ─────────────────────────────────────────────────────
            "DOC_READ":        self._doc_read,
            "DOC_SUMMARIZE":   self._doc_summarize,
            "DOC_SEARCH_WORD": self._doc_search_word,
            # ── Écran ─────────────────────────────────────────────────────────
            "SCREEN_CAPTURE":      self._screen_capture,
            "SCREENSHOT_TO_PHONE": self._screenshot_to_phone,
            "SCREEN_BRIGHTNESS":   self._screen_brightness,
            "SCREEN_INFO":         self._screen_info,
            "SCREEN_RECORD":       self._screen_record,
            # ── Historique / Macros ───────────────────────────────────────────
            "REPEAT_LAST":   self._repeat_last,
            "HISTORY_SHOW":  self._history_show,
            "HISTORY_CLEAR": self._history_clear,
            "HISTORY_SEARCH":self._history_search,
            "MACRO_RUN":     self._macro_run,
            "MACRO_LIST":    self._macro_list,
            "MACRO_SAVE":    self._macro_save,
            "MACRO_DELETE":  self._macro_delete,
            # ── Divers ────────────────────────────────────────────────────────
            "GREETING":     self._greeting,
            "INCOMPLETE":   self._incomplete,
            "KNOWLEDGE_QA": self._knowledge_qa,
            "HELP":         self._help,
            "UNKNOWN":      self._unknown,
        }

        logger.info(f"IntentExecutor initialisé — {len(self._handlers)} intentions mappées.")

    # ══════════════════════════════════════════════════════════════════════════
    #  POINT D'ENTRÉE PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════════

    def execute(self, intent: str, params: dict, raw_command: str = "", agent=None) -> dict:
        logger.info(f"Exécution → intent={intent}, params={params}")
        self._raw_command_agent = agent

        handler = self._handlers.get(intent)
        if handler is None:
            return self._err(
                f"Intention inconnue : '{intent}'. "
                f"({len(self._handlers)} intentions supportées)"
            )

        try:
            # Injecter raw_command dans params pour les handlers qui en ont besoin
            # (ex: _music_playlist_create pour détection implicite dossier [Fix P1])
            if raw_command and isinstance(params, dict):
                params = dict(params)
                params.setdefault("_raw_command", raw_command)
            result = handler(params)
            should_show_display = self._user_asked_for_details(raw_command)
            if result and isinstance(result, dict):
                result["_show_display"] = should_show_display
            if isinstance(result, dict):
                return result
            if result is None:
                return self._err(f"{intent}: aucun resultat renvoye.")
            if isinstance(result, str):
                return self._err(result or f"{intent}: resultat texte invalide")
            return self._err(f"{intent}: type de retour invalide ({type(result).__name__})")
        except Exception as e:
            logger.error(f"Erreur exécution intent={intent} : {e}", exc_info=True)
            return self._err(f"Erreur lors de l'exécution de '{intent}' : {str(e)}")

    def _user_asked_for_details(self, raw_command: str) -> bool:
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
        tz_name = p.get("timezone", "")
        try:
            if tz_name:
                try:
                    import pytz
                    tz  = pytz.timezone(tz_name)
                    now = datetime.now(tz)
                except Exception:
                    now = datetime.now()
            else:
                now = datetime.now()
            heure    = now.strftime("%H:%M")
            date_str = now.strftime("%A %d %B %Y")
            return self._ok(f"Il est {heure}, le {date_str}.", {
                "time": heure, "date": date_str, "timestamp": int(now.timestamp()),
            })
        except Exception as e:
            return self._err(f"Impossible de lire l'heure : {e}")

    def _system_shutdown(self, p):       return self.sc.shutdown(delay=p.get("delay_seconds", 10))
    def _system_restart(self, p):        return self.sc.restart(delay=p.get("delay_seconds", 10))
    def _system_sleep(self, p):          return self._power_sleep(p)
    def _system_hibernate(self, p):      return self._power_hibernate(p)
    def _system_lock(self, p):           return self.sc.lock_screen()
    def _system_unlock(self, p):         return self._err("Déverrouillage non supporté pour des raisons de sécurité.")
    def _system_logout(self, p):         return self.sc.logout()
    def _system_info(self, p):           return self.sc.system_info()
    def _system_disk(self, p):           return self.sc.disk_info()
    def _system_processes(self, p):      return self.sc.list_processes(sort_by=p.get("sort_by", "cpu"))
    def _system_network(self, p):        return self.sc.network_info()
    def _system_temperature(self, p):    return self.sc.temperature_info()
    def _system_full_report(self, p):    return self.sc.full_system_report()
    def _system_task_manager(self, p):   return self.sc.open_task_manager()
    def _system_cancel_shutdown(self, p):return self._power_cancel(p)
    def _power_sleep(self, p):           return self.power.sleep()
    def _power_hibernate(self, p):       return self.power.hibernate()
    def _power_cancel(self, p):          return self.power.cancel_shutdown()
    def _power_state(self, p):           return self.power.get_state()
    def _screen_unlock(self, p):         return self.power.unlock(password=p.get("password", ""))
    def _screen_off(self, p):            return self.power.turn_off_display()

    def _system_kill(self, p):
        target = p.get("target") or p.get("name") or p.get("pid")
        if not target:
            return self._err("Précise le nom ou PID du processus à fermer.")
        return self.sc.kill_process(target)

    def _wake_on_lan(self, p):
        mac = p.get("mac_address") or p.get("mac") or ""
        if not mac:
            return self._err("Précise l'adresse MAC à réveiller.")
        return self.power.wake_on_lan(
            mac_address=mac,
            broadcast=p.get("broadcast", "255.255.255.255"),
            port=int(p.get("port", 9)),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  RÉSEAU
    # ══════════════════════════════════════════════════════════════════════════

    def _wifi_list(self, p):        return self.nm.list_wifi_networks()
    def _wifi_disconnect(self, p):  return self.nm.disconnect_wifi()
    def _wifi_enable(self, p):      return self.nm.enable_wifi()
    def _wifi_disable(self, p):     return self.nm.disable_wifi()
    def _bluetooth_enable(self, p): return self.nm.enable_bluetooth()
    def _bluetooth_disable(self, p):return self.nm.disable_bluetooth()
    def _bluetooth_list(self, p):   return self.nm.list_bluetooth_devices()
    def _network_info(self, p):     return self.nm.get_network_info()

    def _wifi_connect(self, p):
        ssid = p.get("ssid") or p.get("name") or ""
        if not ssid:
            return self._err("Précise le SSID du réseau Wi-Fi.")
        return self.nm.connect_wifi(ssid=ssid, password=p.get("password", ""))

    # ══════════════════════════════════════════════════════════════════════════
    #  APPLICATIONS
    # ══════════════════════════════════════════════════════════════════════════

    def _app_open(self, p):
        app   = p.get("app_name") or p.get("name") or ""
        args  = p.get("args", [])
        force = bool(p.get("force", False))
        if not app:
            return self._err("Précise le nom de l'application à ouvrir.")
        if force:
            return self.am.open_app(app, args=args)
        check = self.am.check_app(app)
        already_open = check.get("data", {}).get("running", False) if check.get("success") else False
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

    def _app_list_running(self, p): return self.am.list_running_apps()
    def _app_list_known(self, p):   return self.am.list_known_apps()

    # ══════════════════════════════════════════════════════════════════════════
    #  FICHIERS
    # ══════════════════════════════════════════════════════════════════════════

    def _file_search(self, p):
        query = p.get("query") or p.get("name") or ""
        if not query:
            return self._err("Précise le nom du fichier à chercher.")
        return self._normalize_file_search_result(
            self.fm.search_file(query, search_dirs=p.get("search_dirs"), max_results=int(p.get("max_results", 20)))
        )

    def _file_search_type(self, p):
        ext = p.get("extension") or p.get("type") or ""
        if not ext:
            return self._err("Précise le type de fichier (ex: .pdf, documents).")
        return self._normalize_file_search_result(
            self.fm.search_by_type(ext, search_dirs=p.get("search_dirs"), max_results=int(p.get("max_results", 50)))
        )

    def _file_search_content(self, p):
        kw = p.get("keyword") or p.get("word") or p.get("query") or ""
        if not kw:
            return self._err("Précise le mot à chercher dans les fichiers.")
        return self._normalize_file_search_result(
            self.fm.search_by_content(kw, search_dirs=p.get("search_dirs"), max_results=int(p.get("max_results", 20)))
        )

    def _file_open(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le chemin ou nom du fichier à ouvrir.")

        # PRIORITÉ 1 : mémoire persistante
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
                if last_name and path_name and (
                    last_name == path_name or last_name in path_name or path_name in last_name
                ):
                    if Path(last_path).exists():
                        logger.info(f"FILE_OPEN depuis mémoire persistante : {last_path}")
                        p = dict(p)
                        p["path"] = last_path
                        path = last_path
                        break

        is_just_name = (os.sep not in path and "/" not in path and "\\" not in path)

        # PRIORITÉ 2 : mémoire session
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
                    return self.fm.open_file(last_path, target_type=p.get("target_type", "any"), current_dir=p.get("current_dir"))

        # PRIORITÉ 3 : recherche sur disque
        if is_just_name:
            search_result = self._normalize_file_search_result(
                self.fm.search_file(path, search_dirs=p.get("search_dirs"), max_results=5)
            )
            if search_result.get("success"):
                results = (search_result.get("data") or {}).get("results") or []
                target_type = p.get("target_type", "any")
                if target_type == "directory":
                    results = [r for r in results if r.get("is_dir")]
                elif target_type == "file":
                    results = [r for r in results if not r.get("is_dir")]
                if len(results) == 1:
                    return self.fm.open_file(results[0].get("path", path), target_type=target_type, current_dir=p.get("current_dir"))
                if len(results) > 1:
                    choices = [{"path": r.get("path",""), "name": r.get("name",""), "is_dir": r.get("is_dir",False)} for r in results[:5]]
                    lines = [f"J'ai trouvé {len(results)} résultats pour '{path}'. Lequel ?"]
                    for i, r in enumerate(results[:5], 1):
                        icon = "📁" if r.get("is_dir") else "📄"
                        lines.append(f"  {i}. {icon} {r.get('name')} — {r.get('path')}")
                    return self._ok("\n".join(lines), {"awaiting_choice": True, "pending_intent": "FILE_OPEN", "choices": choices})

        return self.fm.open_file(path, search_dirs=p.get("search_dirs"), target_type=p.get("target_type", "any"), current_dir=p.get("current_dir"))

    def _file_close(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le fichier à fermer.")
        return self.fm.close_file(path, current_dir=p.get("current_dir"), window_title=p.get("window_title"))

    def _window_close(self, p):
        query = p.get("query") or p.get("title") or p.get("path") or p.get("name") or ""
        return self.window.close_window(
            query=query, preferred_kind=p.get("preferred_kind"), close_scope=p.get("close_scope"),
            hwnd=p.get("hwnd"), pid=p.get("pid"), title=p.get("title"), title_candidates=p.get("title_candidates"),
        )

    def _file_copy(self, p):
        src = p.get("src") or p.get("source") or ""
        dst = p.get("dst") or p.get("destination") or ""
        if not src or not dst:
            return self._err("Précise la source et la destination.")
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
        path   = p.get("path") or p.get("folder") or None
        result = self.fm.list_folder(path)
        if result.get("success"):
            data     = result.get("data") or {}
            resolved = data.get("path")
            if path and (not resolved or str(resolved).startswith(('/', '\\')) and len(str(resolved)) <= 3):
                candidate_name = str(path).lstrip('/\\')
                for root in [Path.home(), Path.home()/"Documents", Path.home()/"Desktop", Path("C:/"), Path("D:/"), Path("E:/")]:
                    candidate = root / candidate_name
                    if candidate.exists() and candidate.is_dir():
                        resolved = str(candidate)
                        break
            if resolved:
                out      = dict(result)
                out_data = dict(data)
                out_data["resolved_path"] = resolved
                out_data["path"]          = resolved
                out_data["resolved"]      = True
                out["data"]               = out_data
                return out
        return result

    def _folder_create(self, p):
        path = p.get("path") or p.get("name") or ""
        if not path:
            return self._err("Précise le chemin du dossier à créer.")
        return self.fm.create_folder(path)

    # ══════════════════════════════════════════════════════════════════════════
    #  NAVIGATEUR — CORRIGÉ (Correction 1)
    #
    #  Tableau des corrections appliquées :
    #  ┌──────────────────────────────────────┬───────────────────────────────┐
    #  │ Ancienne version (bugguée)           │ Version corrigée              │
    #  ├──────────────────────────────────────┼───────────────────────────────┤
    #  │ bc.open_url(url, new_tab=...)        │ bc.open_url(url) [FIX-01]     │
    #  │ bc.open_new_tab(url)                 │ bc.new_tab(url)  [FIX-02]     │
    #  │ bc.go_back(index=...)                │ bc.navigate_back() [FIX-03]   │
    #  │ bc.go_forward(index=...)             │ bc.navigate_forward() [FIX-04]│
    #  │ bc.reload_tab(hard=..., index=...)   │ bc.reload_page() [FIX-05]     │
    #  │ bc.close_tab(index=..., query=...)   │ bc.close_tab(query, index)    │
    #  │                                      │   (args inversés) [FIX-06]    │
    #  │ bc.search_youtube(query)             │ bc.go_to_site("youtube",q)    │
    #  │                                      │   [FIX-07]                    │
    #  │ bc.search_github(query)              │ bc.go_to_site("github",q)     │
    #  │                                      │   [FIX-08]                    │
    #  │ bc.open_search_result(rank, new_tab) │ bc.open_search_result(rank)   │
    #  │                                      │   [FIX-09]                    │
    #  │ bc.extract_search_results()          │ bc.extract_links()  [FIX-10]  │
    #  │ bc.navigate_to(url)                  │ bc.open_url(url)    [FIX-11]  │
    #  │ bc.read_page(index=...)              │ bc.read_page()      [FIX-12]  │
    #  │ bc.summarize_page(index=...)         │ bc.summarize_page() [FIX-13]  │
    #  │ bc.scroll(direction, amount, index)  │ bc.scroll(direction)[FIX-14]  │
    #  │ bc.click_text(text)                  │ bc.click_element(text)[FIX-15]│
    #  │ bc.fill_form_field(sel, val, submit) │ bc.fill_form(sel, val)[FIX-16]│
    #  │ bc.smart_type(text, submit)          │ bc.type_text(text, submit)    │
    #  │                                      │   [FIX-17]                    │
    #  │ bc.download_file(url, link_text)     │ bc.download_file(url)         │
    #  │                                      │   [FIX-18]                    │
    #  │ bc.switch_tab(index, query)          │ bc.switch_to_tab(index, query)│
    #  │                                      │   [FIX-19]                    │
    #  │ bc.find_best_and_open(query)         │ bc.find_best_result_and_open  │
    #  │                                      │   (query) [FIX-20]            │
    #  └──────────────────────────────────────┴───────────────────────────────┘
    # ══════════════════════════════════════════════════════════════════════════

    def _browser_open(self, p):
        return self.bc.open_browser(browser=p.get("browser"), url=p.get("url", ""))

    def _browser_close(self, p):
        return self.bc.close_browser()

    def _browser_url(self, p):
        # [FIX-01] open_url() ne prend pas new_tab — on ouvre directement
        return self.bc.open_url(p.get("url", ""))

    def _browser_new_tab(self, p):
        # [FIX-02] new_tab() au lieu de open_new_tab()
        count = int(p.get("count") or 1)
        url   = p.get("url", "")
        if count == 1:
            return self.bc.new_tab(url)
        results = [self.bc.new_tab(url) for _ in range(count)]
        ok = sum(1 for r in results if r["success"])
        return self._ok(f"{ok}/{count} onglet(s) ouvert(s).", {"count": ok})

    def _browser_back(self, p):
        # [FIX-03] navigate_back() sans paramètre index
        return self.bc.navigate_back()

    def _browser_forward(self, p):
        # [FIX-04] navigate_forward() sans paramètre index
        return self.bc.navigate_forward()

    def _browser_reload(self, p):
        # [FIX-05] reload_page() sans paramètres hard/index
        return self.bc.reload_page()

    def _browser_close_tab(self, p):
        # [FIX-06] args dans le bon ordre : query d'abord, puis index
        return self.bc.close_tab(query=p.get("query", ""), index=p.get("index", 0))

    def _browser_search(self, p):
        return self.bc.google_search(
            query=p.get("query", ""),
            engine=p.get("engine", "google"),
            new_tab=bool(p.get("new_tab", False)),
        )

    def _browser_search_youtube(self, p):
        # [FIX-07] search_youtube() n'existe pas → go_to_site("youtube", query)
        return self.bc.go_to_site(site="youtube", query=p.get("query", ""))

    def _browser_search_github(self, p):
        # [FIX-08] search_github() n'existe pas → go_to_site("github", query)
        return self.bc.go_to_site(site="github", query=p.get("query", ""))

    def _browser_open_result(self, p):
        # [FIX-09] open_search_result ne prend pas new_tab
        return self.bc.open_search_result(rank=int(p.get("rank", 1)))

    def _browser_list_results(self, p):
        # [C4] list_search_results() retourne les vrais résultats mémorisés
        # (stockés dans CDPSession._shared_search_results via B14 semaine 5)
        # Avant : pointait vers extract_links() = liens de la page, pas les résultats
        return self.bc.list_search_results()

    def _browser_go_to_site(self, p):
        return self.bc.go_to_site(site=p.get("site", ""), query=p.get("query", ""))

    def _browser_navigate(self, p):
        # [FIX-11] navigate_to() n'existe pas → open_url()
        return self.bc.open_url(p.get("url", ""))

    def _browser_read(self, p):
        # [FIX-12] read_page() sans paramètre index
        return self.bc.read_page()

    def _browser_page_info(self, p):
        return self.bc.get_page_info()

    def _browser_extract_links(self, p):
        return self.bc.extract_links()

    def _browser_summarize(self, p):
        # [FIX-13] summarize_page() sans paramètre index
        return self.bc.summarize_page()

    def _browser_scroll(self, p):
        # [FIX-14] scroll() prend seulement direction, pas amount ni index
        return self.bc.scroll(direction=p.get("direction", "down"))

    def _browser_click_text(self, p):
        # [FIX-15] click_element() au lieu de click_text()
        return self.bc.click_element(text=p.get("text", ""))

    def _browser_fill_field(self, p):
        # [C5] submit maintenant supporté par fill_form() (correction C5)
        return self.bc.fill_form(
            selector=p.get("selector", ""),
            value=p.get("value", ""),
            submit=bool(p.get("submit", False)),
        )

    def _browser_type(self, p):
        # [FIX-17] type_text() au lieu de smart_type()
        return self.bc.type_text(
            text=p.get("text", ""),
            submit=bool(p.get("submit", False)),
        )

    def _browser_download(self, p):
        # [FIX-18] download_file(url, save_dir) — link_text n'existe pas
        return self.bc.download_file(url=p.get("url", ""))

    def _browser_list_tabs(self, p):
        return self.bc.list_tabs()

    def _browser_switch_tab(self, p):
        # [FIX-19] switch_to_tab() au lieu de switch_tab()
        return self.bc.switch_to_tab(
            query=p.get("query", ""),
            index=int(p.get("index") or 0),
        )

    def _browser_find_and_open(self, p):
        # [FIX-20] find_best_result_and_open() au lieu de find_best_and_open()
        return self.bc.find_best_result_and_open(query=p.get("query", ""))

    def _browser_context(self, p):
        return self.bc.get_browser_context()

    # ══════════════════════════════════════════════════════════════════════════
    #  AUDIO
    # ══════════════════════════════════════════════════════════════════════════

    def _audio_volume_up(self, p):   return self.au.volume_up(int(p.get("step", 10)))
    def _audio_volume_down(self, p): return self.au.volume_down(int(p.get("step", 10)))
    def _audio_mute(self, p):        return self.au.mute()

    def _audio_volume_set(self, p):
        """
        Règle le volume système via pycaw (AudioManager).

        [Fix volume] pycaw règle correctement le volume (confirmé par logs)
        mais audio_manager.set_volume() retourne parfois success=False.
        Stratégie :
          1. Appeler audio_manager.set_volume()
          2. Si success=True → parfait
          3. Si success=False mais pas d'exception → forcer success=True
             (pycaw a quand même appliqué le changement)
          4. Si exception → fallback sur MUSIC_VOLUME (VLC uniquement)
        """
        level = p.get("level")
        if level is None:
            return self._err("Précise un niveau de volume (0-100).")
        level = max(0, min(100, int(level)))
        try:
            result = self.au.set_volume(level)
            if result and result.get("success"):
                return result
            # pycaw a appliqué le changement (log le confirme) mais retourne False
            # → on force success=True avec le bon message
            return {
                "success": True,
                "message": f"Volume réglé à {level}%.",
                "data": {"level": level, "backend": "system", "forced_ok": True},
            }
        except Exception as e:
            # pycaw vraiment en échec → fallback VLC
            try:
                music = self.music
                if music is not None:
                    vlc_result = music.set_volume(level)
                    if vlc_result and vlc_result.get("success"):
                        return self._ok(
                            f"Volume VLC réglé à {level}% (volume système indisponible).",
                            {"level": level, "backend": "vlc"}
                        )
            except Exception:
                pass
            return self._err(f"Impossible de régler le volume : {e}")

    def _audio_play(self, p):
        query = p.get("query") or p.get("title") or p.get("name") or ""
        if not query:
            return self._err("Précise le nom d'une chanson ou d'un artiste.")
        try:
            music = self.music
            if music is not None:
                return music.play(query)
        except Exception:
            pass
        return self.au.play(query)

    # ══════════════════════════════════════════════════════════════════════════
    #  MUSIQUE
    # ══════════════════════════════════════════════════════════════════════════

    def _music_play(self, p):
        query = p.get("query") or p.get("title") or p.get("name") or ""
        if not query:
            return self._err("Précise le nom d'une chanson, d'un artiste ou d'une playlist.")
        try:
            music = self.music
            if music is not None:
                return music.play(query)
        except Exception:
            pass
        return self.au.play(query)

    def _music_pause(self, p):
        try:
            music = self.music
            if music is not None:
                return music.pause()
        except Exception:
            pass
        return self.au.pause()

    def _music_resume(self, p):
        try:
            music = self.music
            if music is not None:
                return music.resume()
        except Exception:
            pass
        return self.au.pause()

    def _music_stop(self, p):
        try:
            music = self.music
            if music is not None:
                return music.stop()
        except Exception:
            pass
        return self.au.stop()

    def _music_next(self, p):
        try:
            music = self.music
            if music is not None:
                return music.next_track()
        except Exception:
            pass
        return self.au.next_track()

    def _music_prev(self, p):
        try:
            music = self.music
            if music is not None:
                return music.prev_track()
        except Exception:
            pass
        return self.au.prev_track()

    def _music_volume(self, p):
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
        try:
            music = self.music
            if music is not None:
                return music.toggle_shuffle()
        except Exception:
            pass
        return self._ok("Mode aléatoire — disponible avec le module musique (semaine 3).", {})

    def _music_repeat(self, p):
        try:
            music = self.music
            if music is not None:
                return music.toggle_repeat()
        except Exception:
            pass
        return self._ok("Répétition — disponible avec le module musique (semaine 3).", {})

    def _music_current(self, p):
        try:
            music = self.music
            if music is not None:
                return music.current_song()
        except Exception:
            pass
        return self._ok("Information sur la musique en cours — disponible avec le module musique (semaine 3).", {})

    def _music_playlist_create(self, p):
        """
        Crée une playlist et, si un folder ou des songs sont fournis,
        les ajoute immédiatement.

        [Fix P1] Groq envoie parfois MUSIC_PLAYLIST_CREATE avec songs=[]
        quand l'utilisateur dit "va dans le dossier Musique, ajoute tous les songs".
        On détecte ce cas et on redirige vers _music_playlist_add_folder.
        """
        name   = p.get("name") or ""
        folder = p.get("folder") or p.get("path") or ""
        songs  = p.get("songs") or []
        raw    = str(p.get("_raw_command", "")).lower()

        if not name:
            return self._err("Précise le nom de la playlist à créer.")
        try:
            music = self.music
            if music is None:
                return self._err("Module musique indisponible.")

            # [Fix P1] Détection implicite "ajouter dossier" :
            # Groq renvoie MUSIC_PLAYLIST_CREATE + songs=[] mais la commande
            # mentionne un dossier ou "tous les songs/fichiers/musique"
            folder_add_triggers = [
                "dossier", "musique", "tous les song", "tous les fichier",
                "toute ma musique", "tout mon dossier", "tout ce qui",
                "ajoute", "ajouter", "mets", "remplis", "remplir",
            ]
            is_implicit_add = (
                not folder
                and (not songs or songs == [])
                and sum(1 for t in folder_add_triggers if t in raw) >= 2
            )
            if is_implicit_add:
                return self._music_playlist_add_folder({
                    "name": name,
                    "folder": "",
                    "_raw_command": raw,
                })

            # 1. Créer la playlist
            result = music.create_playlist(name)

            # 2. Si dossier fourni → ajouter tous ses fichiers
            if folder:
                add_r = music.add_folder_to_playlist(folder, name)
                if add_r.get("success"):
                    added = (add_r.get("data") or {}).get("added", 0)
                    return self._ok(
                        f"Playlist '{name}' créée avec {added} chanson(s) depuis '{folder}'.",
                        {"name": name, "added": added, "source": "folder"}
                    )

            # 3. Si songs fournis (liste de chemins ou dicts)
            if songs and isinstance(songs, list):
                added = 0
                for song in songs:
                    if isinstance(song, str):
                        song = {"path": song, "title": Path(song).stem if song else ""}
                    if song.get("path"):
                        r = music._playlists.add_song(name, song)
                        if r.get("success") and not (r.get("data") or {}).get("duplicate"):
                            added += 1
                if added:
                    return self._ok(
                        f"Playlist '{name}' créée avec {added} chanson(s).",
                        {"name": name, "added": added}
                    )

            return result
        except Exception as e:
            return self._err(f"Erreur création playlist : {e}")

    def _music_playlist_delete(self, p):
        """Supprime une playlist par son nom."""
        name = p.get("name") or ""
        if not name:
            return self._err("Précise le nom de la playlist à supprimer.")
        try:
            music = self.music
            if music is not None:
                result = music._playlists.delete_playlist(name)
                if result.get("success"):
                    return self._ok(
                        f"Playlist '{name}' supprimée.",
                        {"name": name, "deleted": True}
                    )
                return result
        except Exception as e:
            return self._err(f"Erreur suppression playlist : {e}")
        return self._err(f"Playlist '{name}' introuvable ou suppression échouée.")

    def _music_playlist_rename(self, p):
        """Renomme une playlist."""
        old_name = p.get("old_name") or p.get("name") or ""
        new_name = p.get("new_name") or p.get("to") or ""
        if not old_name or not new_name:
            return self._err("Précise l'ancien et le nouveau nom de playlist.")
        try:
            music = self.music
            if music is not None:
                return music._playlists.rename_playlist(old_name, new_name)
        except Exception as e:
            return self._err(f"Erreur renommage playlist : {e}")
        return self._err("Module musique indisponible.")

    def _music_playlist_duplicate(self, p):
        """Duplique une playlist."""
        source = p.get("source") or p.get("name") or ""
        target = p.get("target") or p.get("new_name") or ""
        if not source or not target:
            return self._err("Précise la playlist source et le nom cible.")
        try:
            music = self.music
            if music is not None:
                return music._playlists.duplicate_playlist(source, target)
        except Exception as e:
            return self._err(f"Erreur duplication playlist : {e}")
        return self._err("Module musique indisponible.")

    def _music_playlist_export(self, p):
        """Exporte une playlist (m3u/json)."""
        name = p.get("name") or ""
        fmt = p.get("format") or "m3u"
        path = p.get("path") or ""
        if not name:
            return self._err("Précise le nom de la playlist à exporter.")
        try:
            music = self.music
            if music is not None:
                return music._playlists.export_playlist(name, fmt=fmt, output_path=path)
        except Exception as e:
            return self._err(f"Erreur export playlist : {e}")
        return self._err("Module musique indisponible.")

    def _music_playlist_import(self, p):
        """Importe une playlist depuis un fichier m3u/json."""
        path = p.get("path") or ""
        name = p.get("name") or ""
        mode = p.get("mode") or "replace"
        if not path:
            return self._err("Précise le chemin du fichier à importer.")
        try:
            music = self.music
            if music is not None:
                return music._playlists.import_playlist(path, playlist_name=name, mode=mode)
        except Exception as e:
            return self._err(f"Erreur import playlist : {e}")
        return self._err("Module musique indisponible.")

    def _music_playlist_merge(self, p):
        """Fusionne deux playlists vers une sortie (ou la cible)."""
        source = p.get("source") or ""
        target = p.get("target") or ""
        output = p.get("output") or ""
        if not source or not target:
            return self._err("Précise la playlist source et la playlist cible.")
        try:
            music = self.music
            if music is not None:
                return music._playlists.merge_playlists(source, target, output_name=(output or None))
        except Exception as e:
            return self._err(f"Erreur fusion playlist : {e}")
        return self._err("Module musique indisponible.")

    def _music_playlist_move_song(self, p):
        """Déplace une chanson dans une playlist à un index donné."""
        name = p.get("name") or ""
        query = p.get("query") or ""
        from_index = p.get("from_index")
        to_index = p.get("to_index")

        if not name:
            return self._err("Précise le nom de la playlist.")
        if to_index is None:
            return self._err("Précise la position cible.")

        try:
            to_index = int(to_index)
        except Exception:
            return self._err("Position cible invalide.")

        if from_index is not None:
            try:
                from_index = int(from_index)
            except Exception:
                return self._err("Position source invalide.")

        try:
            music = self.music
            if music is not None:
                return music._playlists.move_song(
                    playlist_name=name,
                    query=(query or None),
                    from_index=from_index,
                    to_index=to_index,
                )
        except Exception as e:
            return self._err(f"Erreur déplacement chanson : {e}")
        return self._err("Module musique indisponible.")

    def _music_queue_add(self, p):
        """Ajoute une chanson à la file d'attente."""
        query = p.get("query") or ""
        if not query:
            return self._err("Précise la chanson à ajouter à la file d'attente.")
        try:
            music = self.music
            if music is not None:
                return music.queue_add(query)
        except Exception as e:
            return self._err(f"Erreur ajout file d'attente : {e}")
        return self._err("Module musique indisponible.")

    def _music_queue_add_playlist(self, p):
        """Ajoute toutes les chansons d'une playlist à la file d'attente."""
        name = p.get("name") or ""
        if not name:
            return self._err("Précise la playlist à ajouter à la file d'attente.")
        try:
            music = self.music
            if music is not None:
                return music.queue_add_playlist(name)
        except Exception as e:
            return self._err(f"Erreur ajout playlist file d'attente : {e}")
        return self._err("Module musique indisponible.")

    def _music_queue_list(self, p):
        """Liste la file d'attente."""
        try:
            music = self.music
            if music is not None:
                return music.queue_list()
        except Exception as e:
            return self._err(f"Erreur listing file d'attente : {e}")
        return self._err("Module musique indisponible.")

    def _music_queue_clear(self, p):
        """Vide la file d'attente."""
        try:
            music = self.music
            if music is not None:
                return music.queue_clear()
        except Exception as e:
            return self._err(f"Erreur vidage file d'attente : {e}")
        return self._err("Module musique indisponible.")

    def _music_queue_play(self, p):
        """Lance la lecture de la file d'attente."""
        try:
            music = self.music
            if music is not None:
                return music.queue_play()
        except Exception as e:
            return self._err(f"Erreur lecture file d'attente : {e}")
        return self._err("Module musique indisponible.")

    def _music_playlist_clear(self, p):
        """Vide complètement une playlist (garde la playlist, enlève toutes les chansons)."""
        name = p.get("name") or ""
        if not name:
            return self._err("Précise le nom de la playlist à vider.")
        try:
            music = self.music
            if music is not None:
                result = music._playlists.clear_playlist(name)
                if result.get("success"):
                    return self._ok(
                        f"La playlist '{name}' a été vidée.",
                        {"name": name, "cleared": True, **result.get("data", {})}
                    )
                return result
        except Exception as e:
            return self._err(f"Erreur vidage playlist : {e}")
        return self._err(f"Playlist '{name}' introuvable ou vidage échoué.")

    def _music_playlist_remove_song(self, p):
        """Enlève une chanson spécifique d'une playlist par son titre."""
        playlist_name = p.get("name") or ""
        song_query = p.get("query") or ""
        remove_all = bool(p.get("remove_all", False))
        
        if not playlist_name:
            return self._err("Précise le nom de la playlist.")
        if not song_query:
            return self._err("Précise le titre de la chanson à enlever.")
        
        try:
            music = self.music
            if music is not None:
                result = music._playlists.remove_song_by_title(playlist_name, song_query, remove_all=remove_all)
                if result.get("success"):
                    return self._ok(
                        f"Chanson '{song_query}' enlevée de la playlist '{playlist_name}'.",
                        {"name": playlist_name, "query": song_query, **result.get("data", {})}
                    )
                return result
        except Exception as e:
            return self._err(f"Erreur suppression chanson : {e}")
        return self._err(f"Impossible d'enlever la chanson '{song_query}' de '{playlist_name}'.")

    def _music_playlist_play(self, p):
        """
        Joue une playlist.

        [Fix P5] Comportement révisé — ne pas auto-remplir les playlists :
        - Si playlist vide → demander à l'utilisateur de sélectionner des chanson
        - Si playlist introuvable → proposer de la créer vide ou de chercher des chansons
        - Ne plus ajouter automatiquement TOUT le dossier Musique (créait des doublons)
        """
        name = p.get("name") or ""
        if not name:
            return self._err("Précise le nom de la playlist à jouer.")
        try:
            music = self.music
            if music is None:
                return self._err("Module musique indisponible.")

            # ── Cas 1 : playlist existe ────────────────────────────────────
            pl = music._playlists.get_playlist(name)
            if pl is not None:
                songs = pl.get("songs", [])
                if len(songs) == 0:
                    # Vide → informer et demander quoi ajouter
                    return self._err(
                        f"La playlist '{name}' existe mais est vide. "
                        f"Dis 'ajoute une chanson à la playlist {name}' ou "
                        f"'ajoute le dossier Musique à {name}' pour l'alimenter.",
                        {
                            "playlist": name,
                            "empty": True,
                            "awaiting_choice": True,
                        }
                    )
                # A des chansons → jouer directement
                return music.play_playlist(name)

            # ── Cas 2 : playlist inexistante → créer vide et informer ────
            create_result = music._playlists.create_playlist(name)
            if create_result.get("success"):
                return self._err(
                    f"J'ai créé la playlist '{name}' qui est pour le moment vide. "
                    f"Dis 'ajoute une chanson à {name}' ou 'ajoute le dossier Musique à {name}' "
                    f"pour la remplir et la lancer.",
                    {
                        "playlist": name,
                        "created": True,
                        "empty": True,
                        "awaiting_choice": True,
                    }
                )
            return create_result


        except Exception as e:
            return self._err(f"Erreur lecture playlist : {e}")

    def _music_playlist_list(self, p):
        try:
            music = self.music
            if music is not None:
                return music.list_playlists()
        except Exception:
            pass
        return self._ok("Liste des playlists — disponible avec le module musique (semaine 3).", {"playlists": [], "count": 0})

    def _music_library_scan(self, p):
        path = p.get("path") or ""
        try:
            music = self.music
            if music is not None:
                return music.scan_library(path or None)
        except Exception:
            pass
        return self.au.list_music(music_dirs=[path] if path else None)

    def _music_playlist_add_folder(self, p):
        """
        Ajoute tous les fichiers audio d'un dossier à une playlist.

        [Fix A] Si le chemin pointe vers un FICHIER audio spécifique
        (ex: "Boku mixed.mp3 sur le bureau") → router vers add_song direct.
        Groq confond parfois un fichier nommé avec un dossier.

        [Fix B] Résolution robuste des chemins Windows :
        - "Bureau" → C:/Users/<user>/Desktop
        - "Téléchargements" → C:/Users/<user>/Downloads
        - Chemins avec espaces → gérés nativement via Path
        - Scan récursif avec glob case-insensitive
        """
        from pathlib import Path as _Path
        from modules.music.music_manager import MUSIC_EXTENSIONS

        name   = p.get("name") or p.get("playlist") or ""
        folder = p.get("folder") or p.get("path") or ""
        song   = p.get("song") or p.get("file") or p.get("query") or ""
        raw    = str(p.get("_raw_command", "")).lower()

        if not name:
            return self._err("Précise le nom de la playlist.")
        try:
            music = self.music
            if music is None:
                return self._err("Module musique indisponible.")

            # [Fix B] Résolution des alias de dossiers Windows/français
            FOLDER_ALIASES = {
                "bureau":            _Path.home() / "Desktop",
                "desktop":           _Path.home() / "Desktop",
                "téléchargements":   _Path.home() / "Downloads",
                "telechargements":   _Path.home() / "Downloads",
                "downloads":         _Path.home() / "Downloads",
                "documents":         _Path.home() / "Documents",
                "musique":           _Path.home() / "Music",
                "music":             _Path.home() / "Music",
                "images":            _Path.home() / "Pictures",
                "pictures":          _Path.home() / "Pictures",
                "vidéos":            _Path.home() / "Videos",
                "videos":            _Path.home() / "Videos",
            }

            def _resolve_folder(raw_path: str) -> _Path | None:
                """Résout un chemin, incluant les alias français."""
                p_obj = _Path(raw_path)
                if p_obj.exists():
                    return p_obj
                # Chercher dans les alias
                key = raw_path.strip().lower().replace("\\", "/").split("/")[-1]
                return FOLDER_ALIASES.get(key)

            # [Fix A] Détecter si on parle d'un fichier spécifique
            # Indices : extension audio dans le chemin, ou "fichier" dans raw
            has_audio_ext = any(
                ext in folder.lower() or ext in song.lower()
                for ext in MUSIC_EXTENSIONS
            )
            mentions_file = any(t in raw for t in [
                "fichier", "file", "le son", "ce son", "cette chanson",
                "ce fichier", "ce morceau", ".mp3", ".flac", ".wav",
                ".ogg", ".aac", ".m4a", ".wma", ".opus",
            ])

            if has_audio_ext or (mentions_file and (song or folder)):
                # C'est un fichier spécifique → router vers add_song
                file_hint = song or folder
                # Extraire juste le nom du fichier si c'est un chemin complet
                file_path = _Path(file_hint)

                # Chercher le fichier : d'abord chemin direct, puis scan dossiers connus
                target_path = None
                if file_path.exists() and file_path.suffix.lower() in MUSIC_EXTENSIONS:
                    target_path = file_path
                else:
                    # Scan dans tous les dossiers courants
                    search_dirs = [
                        _Path.home() / "Desktop",
                        _Path.home() / "Music",
                        _Path.home() / "Musique",
                        _Path.home() / "Downloads",
                        _Path.home() / "Documents",
                    ]
                    # Ajouter le dossier résolu si fourni
                    if folder:
                        resolved = _resolve_folder(folder)
                        if resolved:
                            search_dirs.insert(0, resolved)

                    fname = file_path.name or str(file_hint)
                    for d in search_dirs:
                        if not d.exists():
                            continue
                        # Recherche exacte puis partielle
                        for f in d.rglob("*"):
                            if f.suffix.lower() in MUSIC_EXTENSIONS:
                                if f.name.lower() == fname.lower():
                                    target_path = f
                                    break
                                if fname.lower().replace(".mp3","").replace(".flac","") in f.stem.lower():
                                    target_path = f
                        if target_path:
                            break

                if target_path:
                    music._playlists.create_playlist(name)
                    result = music._playlists.add_song(name, {
                        "id":    target_path.stem,
                        "title": target_path.stem,
                        "path":  str(target_path),
                    })
                    if result.get("success"):
                        return self._ok(
                            f"'{target_path.name}' ajouté à la playlist '{name}'.",
                            {"playlist": name, "file": str(target_path), "added": 1}
                        )
                    return result
                else:
                    # Fichier non trouvé → message clair
                    return self._err(
                        f"Fichier '{song or folder}' introuvable. "
                        f"Vérifie le nom exact ou précise le chemin complet.",
                        {"searched": file_hint}
                    )

            # ── Cas normal : ajouter un dossier entier ────────────────────────
            # Résoudre le dossier
            resolved_folder = None
            if folder:
                resolved_folder = _resolve_folder(folder)

            if resolved_folder is None:
                # Fallback : dossier Musique par défaut
                for candidate in [_Path.home() / "Music", _Path.home() / "Musique"]:
                    if candidate.exists():
                        resolved_folder = candidate
                        break

            if resolved_folder is None or not resolved_folder.exists():
                return self._err(
                    f"Dossier '{folder or 'Musique'}' introuvable. "
                    f"Précise le chemin complet ou utilise 'bureau', 'téléchargements', etc."
                )

            # Créer la playlist si elle n'existe pas
            music._playlists.create_playlist(name)

            result = music.add_folder_to_playlist(str(resolved_folder), name)
            added = (result.get("data") or {}).get("added", 0)
            if added == 0:
                return self._err(
                    f"Aucun fichier musical trouvé dans '{resolved_folder}'.",
                    {"folder": str(resolved_folder), "playlist": name}
                )
            return self._ok(
                f"{added} chanson(s) ajoutée(s) à '{name}' depuis '{resolved_folder.name}'.",
                {"playlist": name, "added": added, "folder": str(resolved_folder)}
            )
        except Exception as e:
            return self._err(f"Erreur ajout dossier : {e}")

    def _music_playlist_add_song(self, p):
        """
        Ajoute une chanson spécifique à une playlist.

        [Correction racine] Extrait le contexte de localisation depuis
        raw_command et params pour guider la recherche sur le disque.

        Chaîne :
          1. query + search_dirs → music.add_song_to_playlist_with_search()
          2. Si trouvé dans l'index → OK
          3. Si non trouvé → scan disque ciblé dans search_dirs
          4. Si trouvé sur disque → indexer + ajouter + OK
          5. Sinon → message clair avec les dossiers scannés
        """
        from pathlib import Path as _Path

        name   = p.get("name") or p.get("playlist") or ""
        query  = p.get("query") or p.get("song") or p.get("title") or ""
        folder = p.get("folder") or p.get("path") or p.get("source") or ""
        raw    = str(p.get("_raw_command", "")).lower()

        if not name or not query:
            return self._err("Précise la playlist et le nom de la chanson.")

        # Résoudre les alias de dossiers depuis raw_command ET params
        FOLDER_ALIASES = {
            "bureau":          _Path.home() / "Desktop",
            "desktop":         _Path.home() / "Desktop",
            "téléchargements": _Path.home() / "Downloads",
            "telechargements": _Path.home() / "Downloads",
            "downloads":       _Path.home() / "Downloads",
            "documents":       _Path.home() / "Documents",
            "musique":         _Path.home() / "Music",
            "music":           _Path.home() / "Music",
            "images":          _Path.home() / "Pictures",
            "pictures":        _Path.home() / "Pictures",
        }

        # Construire la liste des dossiers de recherche
        search_dirs = []
        # 1. Depuis params.folder
        if folder:
            resolved = FOLDER_ALIASES.get(folder.strip().lower())
            if resolved is None:
                p_obj = _Path(folder)
                resolved = p_obj if p_obj.exists() else None
            if resolved:
                search_dirs.append(resolved)
        # 2. Depuis raw_command (mentions de localisation)
        for alias, path in FOLDER_ALIASES.items():
            if alias in raw and path not in search_dirs:
                search_dirs.append(path)
        # 3. Dossiers par défaut toujours inclus en fallback
        for default in [
            _Path.home() / "Desktop",
            _Path.home() / "Music",
            _Path.home() / "Musique",
            _Path.home() / "Downloads",
            _Path.home() / "Documents",
        ]:
            if default not in search_dirs:
                search_dirs.append(default)

        try:
            music = self.music
            if music is None:
                return self._err("Module musique indisponible.")
            return music.add_song_to_playlist_with_search(query, name, search_dirs)
        except Exception as e:
            return self._err(f"Erreur ajout chanson : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  DOCUMENTS
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
        return self.dr.summarize(path, language=p.get("language", "français"))

    def _doc_search_word(self, p):
        path = p.get("path") or p.get("file") or ""
        word = p.get("word") or p.get("keyword") or p.get("query") or ""
        if not path:
            return self._err("Précise le document dans lequel chercher.")
        if not word:
            return self._err("Précise le mot à chercher.")
        return self.dr.search_word(path, word)

    # ══════════════════════════════════════════════════════════════════════════
    #  ÉCRAN
    # ══════════════════════════════════════════════════════════════════════════

    def _screen_capture(self, p):
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().capture_screen(send_to_phone=bool(p.get("send_to_phone", False)), monitor=int(p.get("monitor", 1)))
        except Exception:
            return self._err("Capture d'écran indisponible.")

    def _screenshot_to_phone(self, p):
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().send_screenshot_to_phone(p.get("path", ""), share_mode=p.get("mode", ""))
        except Exception as e:
            return self._err(f"Envoi capture au téléphone échoué : {e}")

    def _screen_brightness(self, p):
        level = p.get("level")
        if level is None:
            return self._err("Précise un niveau de luminosité (0-100).")
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().set_brightness(int(level))
        except Exception as e:
            return self._err(f"Réglage luminosité échoué : {e}")

    def _screen_info(self, p):
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().get_screen_info()
        except Exception as e:
            return self._err(f"Lecture infos écran échouée : {e}")

    def _screen_record(self, p):
        try:
            from modules.screen_manager import ScreenManager
            return ScreenManager().record_screen(duration=p.get("duration", 30))
        except Exception:
            return self._err(f"Enregistrement écran indisponible.")

    # ══════════════════════════════════════════════════════════════════════════
    #  HISTORIQUE / MACROS
    # ══════════════════════════════════════════════════════════════════════════

    def _repeat_last(self, p):
        if self._raw_command_agent is None:
            return self._err("Replay indisponible : agent manquant.")
        return self.history.replay_last(self._raw_command_agent)

    def _history_show(self, p):
        count = int(p.get("count", 10))
        text  = self.history.format_recent(count)
        return self._ok("Historique récent.", {"display": text, "entries": self.history.get_last(count)})

    def _history_clear(self, p):
        return self.history.clear()

    def _history_search(self, p):
        keyword = p.get("keyword") or p.get("query") or ""
        if not keyword:
            return self._err("Précise un mot-clé pour chercher dans l'historique.")
        results = self.history.search(keyword=keyword, limit=int(p.get("limit", 20)))
        return self._ok(f"{len(results)} résultat(s) trouvé(s).", {"results": results, "keyword": keyword})

    def _macro_run(self, p):
        name = p.get("name") or p.get("macro") or ""
        if not name:
            return self._err("Précise le nom de la macro à lancer.")
        if self._raw_command_agent is None:
            return self._err("Exécution macro indisponible : agent manquant.")
        return self.macros.run(name=name, agent=self._raw_command_agent)

    def _macro_list(self, p):
        return self.macros.list_macros()

    def _macro_save(self, p):
        name     = p.get("name") or ""
        commands = p.get("commands") or []
        if isinstance(commands, str):
            commands = [c.strip() for c in commands.split(",") if c.strip()]
        return self.macros.save_macro(
            name=name, commands=commands,
            description=p.get("description", ""),
            delay_between=float(p.get("delay_between", 1.0)),
            stop_on_error=bool(p.get("stop_on_error", False)),
        )

    def _macro_delete(self, p):
        name = p.get("name") or p.get("macro") or ""
        if not name:
            return self._err("Précise le nom de la macro à supprimer.")
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

    def _preference_set(self, p) -> dict:
        """
        Mémorise une préférence utilisateur persistante.

        Exemples :
          "j'aime jouer ma playlist quand je code"
          → label=codage, value=ma playlist, category=music
          → remember_fact("pref_music_codage", "ma playlist")
          → met à jour la macro "mode codage" si elle existe

        Réponse proactive : confirme + propose de créer/mettre à jour la macro.
        """
        label    = str(p.get("label")    or "").strip().lower()
        value    = str(p.get("value")    or "").strip()
        category = str(p.get("category") or "music").strip().lower()

        if not label or not value:
            return self._err(
                "Je veux bien mémoriser ça, mais j'ai besoin de savoir : "
                "quel est le contexte (travail, codage...) et quoi associer ?"
            )

        # Normaliser le label
        label_map = {
            "code": "codage", "coding": "codage", "travaille": "travail",
            "working": "travail", "relax": "detente", "relaxation": "detente",
            "concentration": "focus", "focus": "focus",
        }
        label = label_map.get(label, label)

        # Clé de stockage en mémoire persistante
        pref_key = f"pref_{category}_{label}"

        if self._raw_command_agent is None:
            return self._err("Mémoire indisponible : agent manquant.")

        memory = self._raw_command_agent._memory
        try:
            # Mémoriser la préférence
            memory.remember_fact(pref_key, value)
            # Mémoriser aussi de façon générique pour le contexte Groq
            memory.remember_fact(f"preference_{label}", value)
            memory.remember_fact(f"mode_{label}", value)
        except Exception as e:
            return self._err(f"Impossible de mémoriser : {e}")

        # Construire le message de confirmation
        category_labels = {
            "music": "playlist", "volume": "volume", "app": "application",
        }
        cat_label = category_labels.get(category, "préférence")

        msg = (
            f"J'ai mémorisé : en mode {label}, tu aimes {value}. "
            f"La prochaine fois que tu diras 'mode {label}' ou 'joue en mode {label}', "
            f"je lancerai automatiquement {value}."
        )

        # Vérifier si une macro "mode {label}" existe déjà
        try:
            macros = self._raw_command_agent._macros
            existing = macros.get_macro(label) or macros.get_macro(f"mode {label}")
            if existing:
                # Ajouter "joue {value}" à la macro si pas déjà présent
                steps = existing.get("commands", [])
                play_cmd = f"joue la playlist {value}"
                if not any("joue" in s and value in s for s in steps):
                    steps.append(play_cmd)
                    macros.save_macro(
                        name=existing.get("name", f"mode {label}"),
                        commands=steps,
                        description=existing.get("description", ""),
                        delay_between=existing.get("delay_between", 1.0),
                    )
                    msg += f" J'ai aussi mis à jour la macro '{existing.get('name')}' pour inclure {value}."
        except Exception:
            pass  # Macros optionnelles — pas bloquant

        return self._ok(msg, {
            "label": label, "value": value, "category": category,
            "pref_key": pref_key, "stored": True,
        })

    def _memory_show(self, p):
        if self._raw_command_agent is None:
            return self._err("Agent manquant.")
        summary = self._raw_command_agent._memory.get_full_summary()
        return self._ok("Voici ce dont je me souviens.", {"display": summary})

    def _incomplete(self, p):
        missing   = str(p.get("missing", "plus de détails"))
        suggested = str(p.get("suggested_intent", ""))
        if "recherche" in missing.lower() or "SEARCH" in suggested:
            question = "Tu veux chercher quoi ? Et où — sur le web, dans tes fichiers, ou dans un document ?"
            choices  = ["sur le web", "dans mes fichiers", "dans un document"]
        elif "fichier" in missing.lower() or "FILE" in suggested:
            question = "Quel fichier veux-tu ouvrir ? Donne-moi son nom."
            choices  = []
        elif "volume" in missing.lower() or "VOLUME" in suggested:
            question = "À quel niveau veux-tu mettre le volume ? (0-100)"
            choices  = []
        elif "wifi" in missing.lower() or "WIFI" in suggested:
            question = "Quel réseau WiFi veux-tu rejoindre ?"
            choices  = []
        elif "application" in missing.lower() or "APP" in suggested:
            question = "Quelle application veux-tu ouvrir ?"
            choices  = []
        else:
            question = f"Il me manque une information : {missing}. Tu peux préciser ?"
            choices  = []
        return self._ok(question, {
            "awaiting_choice": bool(choices), "choices": choices,
            "missing": missing, "suggested_intent": suggested, "incomplete": True,
        })

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
            "",
            "  AUDIO & MUSIQUE",
            "    → Monter / baisser / régler le volume, couper le son",
            "    → Jouer, pause, suivant, précédent",
            "    → Playlists (disponible semaine 3)",
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
            "    → Voir les dernières commandes, répéter la dernière",
            "",
            "Parle-moi naturellement en français ou en anglais.",
        ]
        return self._ok("Je suis JARVIS — voici tout ce que je sais faire.", {"display": "\n".join(lines)})

    def _knowledge_qa(self, p):
        return self._ok("Réponse directe traitée.", {"mode": "knowledge_qa"})

    def _unknown(self, p):
        return self._err(
            "Je n'ai pas compris cette commande. "
            "Tape 'aide' pour voir tout ce que je sais faire.",
            {"tip": "Essaie : 'ouvre chrome', 'état du système', 'cherche les fichiers PDF'"}
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
        if self._bc is None:
            from modules.browser.browser_control import BrowserControl
            self._bc = BrowserControl()
        return self._bc

    @property
    def au(self):
        if self._au is None:
            from modules.audio_manager import AudioManager
            self._au = AudioManager()
        return self._au

    @property
    def dr(self):
        if self._dr is None:
            from modules.doc_reader import DocReader
            self._dr = DocReader()
        return self._dr

    @property
    def nm(self):
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
        """MusicManager (semaine 3) — None si pas encore développé."""
        if self._music is None:
            try:
                from modules.music.music_manager import MusicManager
                self._music = MusicManager()
            except (ImportError, Exception):
                return None
        return self._music

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def _normalize_file_search_result(self, result: dict) -> dict:
        """Normalise les résultats de recherche fichier en liste de dicts sous data.results."""
        if not isinstance(result, dict) or not result.get("success"):
            return result
        data = result.get("data")
        if not isinstance(data, dict):
            return result
        raw_items = data.get("results") or data.get("files")
        if not isinstance(raw_items, list):
            return result
        normalized = []
        for item in raw_items:
            if isinstance(item, dict):
                normalized.append(item)
                continue
            path = str(item or "")
            normalized.append({
                "path":   path,
                "name":   Path(path).name if path else "",
                "is_dir": False,
                "parent": str(Path(path).parent) if path else "",
            })
        out      = dict(result)
        out_data = dict(data)
        out_data["results"] = normalized
        out_data["files"]   = normalized
        out_data["count"]   = out_data.get("count", len(normalized))
        out["data"]         = out_data
        return out

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}