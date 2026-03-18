"""history_manager.py - Historique persistant des commandes (Semaine 11)."""

import json
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from config.logger import get_logger
from config.settings import HISTORY_DIR

logger = get_logger(__name__)

MAX_ENTRIES = 1000
HISTORY_FILE = HISTORY_DIR / "commands_history.json"

_META_INTENTS = {
    "HELP",
    "HISTORY_SHOW",
    "HISTORY_CLEAR",
    "HISTORY_SEARCH",
    "REPEAT_LAST",
    "MACRO_LIST",
    "MACRO_RUN",
    "MACRO_SAVE",
    "MACRO_DELETE",
}


def _safe_message(result: dict) -> str:
    msg = str((result or {}).get("message", ""))
    return msg[:200]


def _looks_binary_or_base64(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value.startswith("data:") and "base64," in value:
        return True
    if len(value) > 256 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r" for c in value[:256]):
        return True
    return False


def _summarize_data(data):
    if data is None:
        return None
    if isinstance(data, bytes):
        return "<bytes omitted>"
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if isinstance(v, bytes):
                out[k] = "<bytes omitted>"
            elif isinstance(v, str) and _looks_binary_or_base64(v):
                out[k] = "<base64 omitted>"
            elif isinstance(v, (dict, list)):
                out[k] = "<nested omitted>"
            else:
                out[k] = v
        return out
    if isinstance(data, list):
        return ["<omitted>"] if data else []
    if isinstance(data, str) and _looks_binary_or_base64(data):
        return "<base64 omitted>"
    return data


class HistoryEntry:
    """Une entrée d'historique sérialisable en JSON."""

    def __init__(self, command: str, result: dict, source: str, intent: str = "UNKNOWN", duration_ms: int = 0):
        ts = int(time.time())
        self.id = int(time.time() * 1000)
        self.command = (command or "").strip()
        self.intent = intent or result.get("_intent", "UNKNOWN")
        self.success = bool((result or {}).get("success", False))
        self.message = _safe_message(result or {})
        self.source = source or "terminal"
        self.timestamp = ts
        self.duration_ms = int(max(0, duration_ms or 0))
        self.data_summary = _summarize_data((result or {}).get("data"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "intent": self.intent,
            "success": self.success,
            "message": self.message,
            "source": self.source,
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": self.duration_ms,
            "data_summary": self.data_summary,
        }

    @classmethod
    def from_dict(cls, d: dict):
        obj = cls(
            command=d.get("command", ""),
            result={
                "success": d.get("success", False),
                "message": d.get("message", ""),
                "data": d.get("data_summary"),
                "_intent": d.get("intent", "UNKNOWN"),
            },
            source=d.get("source", "terminal"),
            intent=d.get("intent", "UNKNOWN"),
            duration_ms=d.get("duration_ms", 0),
        )
        obj.id = int(d.get("id", obj.id))
        obj.timestamp = int(d.get("timestamp", obj.timestamp))
        return obj


class HistoryManager:
    """Historique des commandes persistant, thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.history_file = Path(HISTORY_FILE)
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self._entries = []
        self._load()
        logger.info(f"HistoryManager initialisé. Fichier: {self.history_file}")

    def save(self, command: str, result: dict, source: str = "terminal", intent: str = "UNKNOWN", duration_ms: int = 0):
        """Sauvegarde une commande en tête de liste (plus récent d'abord)."""
        entry = HistoryEntry(command, result or {}, source, intent, duration_ms)
        with self._lock:
            self._entries.insert(0, entry)
            self._rotate()
        self._save_async()
        return entry.to_dict()

    def get_last(self, n: int = 10) -> list:
        with self._lock:
            return [e.to_dict() for e in self._entries[: max(0, int(n))]]

    def get_last_command(self) -> dict | None:
        last = self.get_last(1)
        return last[0] if last else None

    def get_last_successful(self) -> dict | None:
        with self._lock:
            for e in self._entries:
                if e.success:
                    return e.to_dict()
        return None

    def replay_last(self, agent) -> dict:
        """Relance la dernière commande non-méta."""
        target = None
        with self._lock:
            for e in self._entries:
                if not e.intent or e.intent in _META_INTENTS:
                    continue
                target = e
                break

        if target is None:
            return {"success": False, "message": "Aucune commande rejouable trouvée.", "data": None}

        started = time.time()
        result = agent.handle_command(target.command)
        duration = int((time.time() - started) * 1000)
        self.save(
            command=target.command,
            result=result,
            source="replay",
            intent=result.get("_intent", target.intent),
            duration_ms=duration,
        )
        return result

    def search(self, keyword: str, limit: int = 20) -> list:
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        out = []
        max_items = min(max(1, int(limit)), 20)
        with self._lock:
            for e in self._entries:
                hay = f"{e.command} {e.message} {e.intent}".lower()
                if kw in hay:
                    out.append(e.to_dict())
                    if len(out) >= max_items:
                        break
        return out

    def get_stats(self) -> dict:
        with self._lock:
            entries = list(self._entries)
        total = len(entries)
        if total == 0:
            return {
                "total": 0,
                "success_rate": 0.0,
                "today": 0,
                "top_intents": [],
                "sources": {},
                "avg_duration_ms": 0.0,
            }

        now = datetime.now()
        midnight = int(datetime(now.year, now.month, now.day).timestamp())
        success_count = sum(1 for e in entries if e.success)
        intent_counts = Counter(e.intent for e in entries if e.intent)
        source_counts = Counter(e.source for e in entries if e.source)
        avg_duration = round(sum(e.duration_ms for e in entries) / total, 1)

        return {
            "total": total,
            "success_rate": round((success_count / total) * 100, 1),
            "today": sum(1 for e in entries if e.timestamp >= midnight),
            "top_intents": [{"intent": k, "count": v} for k, v in intent_counts.most_common(5)],
            "sources": dict(source_counts),
            "avg_duration_ms": avg_duration,
        }

    def format_recent(self, n: int = 10) -> str:
        lines = []
        for e in self.get_last(n):
            dt = datetime.fromtimestamp(e["timestamp"]).strftime("%H:%M")
            icon = "✓" if e.get("success") else "✗"
            lines.append(f"{icon} [{dt}] {e.get('command', '')}")
        return "\n".join(lines) if lines else "Aucune commande dans l'historique."

    def clear(self):
        with self._lock:
            deleted = len(self._entries)
            self._entries = []
        self._save_sync()
        return {"success": True, "message": f"{deleted} entree(s) supprimee(s)", "data": {"deleted": deleted}}

    def _load(self):
        if not self.history_file.exists():
            self.history_file.write_text("[]", encoding="utf-8")
            return
        try:
            raw = self.history_file.read_text(encoding="utf-8").strip() or "[]"
            data = json.loads(raw)
            self._entries = [HistoryEntry.from_dict(d) for d in data if isinstance(d, dict)]
        except Exception as exc:
            logger.error(f"Chargement historique échoué: {exc}")
            self._entries = []

    def _save_async(self):
        threading.Thread(target=self._save_sync, daemon=True).start()

    def _save_sync(self):
        with self._lock:
            payload = [e.to_dict() for e in self._entries]
        try:
            self.history_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error(f"Sauvegarde historique échouée: {exc}")

    def _rotate(self):
        if len(self._entries) <= MAX_ENTRIES:
            return

        overflow = self._entries[MAX_ENTRIES:]
        self._entries = self._entries[:MAX_ENTRIES]

        archives = {}
        for entry in overflow:
            month = datetime.fromtimestamp(entry.timestamp).strftime("%Y-%m")
            archives.setdefault(month, []).append(entry.to_dict())

        for month, rows in archives.items():
            archive_path = self.history_file.parent / f"history_{month}.json"
            try:
                old = []
                if archive_path.exists():
                    old = json.loads(archive_path.read_text(encoding="utf-8") or "[]")
                archive_path.write_text(
                    json.dumps(old + rows, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.error(f"Rotation archive {archive_path.name} échouée: {exc}")