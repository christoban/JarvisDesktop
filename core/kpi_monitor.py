"""
core/kpi_monitor.py — KPI Collection & Drift Detection
=======================================================
Collecte les métriques en live pendant l'exécution.
Détecte le drift sur FOLLOWUP, UNKNOWN, duplicats, confiance.

Usage (injecté dans Agent):
    monitor = KPIMonitor()
    # À chaque parse/execute:
    monitor.record_parse(command, result)
    monitor.record_execute(intent, success)
    
    # État à tout moment:
    status = monitor.get_kpi_status()
    monitor.check_drift_alerts()
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from threading import Lock

from config.logger import get_logger
from config.settings import BASE_DIR

logger = get_logger(__name__)


class KPIMonitor:
    """Collecte et alerte sur les KPIs critiques."""

    @classmethod
    def get_instance(cls) -> "KPIMonitor":
        """Compat API: retourne l'instance singleton du monitor."""
        return get_kpi_monitor()
    
    def __init__(self, window_minutes: int = 60):
        self._lock = Lock()
        self._window = timedelta(minutes=window_minutes)
        self._start_time = datetime.now()
        
        # Métriques par fenêtre glissante
        self._parse_events = []
        self._execute_events = []
        self._errors = []
        
        # Compteurs cumulatifs
        self._total_parse = 0
        self._total_execute = 0
        self._total_errors = 0
        
        # Drift baselines (à calibrer)
        self._baselines = {
            "followup_rate": 0.05,       # < 5% should be FOLLOWUP
            "unknown_rate": 0.02,        # < 2% should be UNKNOWN
            "errors_rate": 0.01,         # < 1% should fail
            "min_avg_confidence": 0.90,  # avg >= 0.90
        }
        
        # Alertes actives
        self._active_alerts = set()
    
    
    def record_parse(self, command: str, result: dict) -> None:
        """Enregistre un résultat de parsing."""
        if not result or not isinstance(result, dict):
            return
        
        with self._lock:
            event = {
                "timestamp": datetime.now().isoformat(),
                "command": command[:100],
                "intent": result.get("intent", "UNKNOWN"),
                "confidence": float(result.get("confidence", 0.0)),
                "source": result.get("source", "unknown"),
            }
            self._parse_events.append(event)
            self._total_parse += 1
            
            # Log niveau WARN si confiance très faible
            if event["confidence"] < 0.70:
                logger.warning(f"[KPI] Low confidence: intent={event['intent']}, conf={event['confidence']:.2f}")
    
    
    def record_execute(self, intent: str, success: bool, error: str = "") -> None:
        """Enregistre un résultat d'exécution."""
        with self._lock:
            event = {
                "timestamp": datetime.now().isoformat(),
                "intent": intent,
                "success": success,
                "error": error,
            }
            self._execute_events.append(event)
            self._total_execute += 1
            
            if not success:
                self._total_errors += 1
                self._errors.append(event)
                logger.error(f"[KPI] Execute failed: intent={intent}, error={error}")
    
    
    def get_kpi_status(self) -> dict:
        """Retourne le status KPI courant."""
        with self._lock:
            now = datetime.now()
            
            # Filtrer dernière fenêtre glissante
            recent_parse = [e for e in self._parse_events if datetime.fromisoformat(e["timestamp"]) > now - self._window]
            recent_exec = [e for e in self._execute_events if datetime.fromisoformat(e["timestamp"]) > now - self._window]
            
            status = {
                "uptime_minutes": int((now - self._start_time).total_seconds() / 60),
                "total_commands": self._total_parse,
                "total_executions": self._total_execute,
                "total_errors": self._total_errors,
                "window_minutes": int(self._window.total_seconds() / 60),
                "recent_window": {
                    "parse_count": len(recent_parse),
                    "exec_count": len(recent_exec),
                    "error_count": sum(1 for e in recent_exec if not e.get("success", False)),
                },
                "idle": len(recent_parse) == 0 and len(recent_exec) == 0,
            }
            
            # Intent distribution (dernière fenêtre)
            intent_dist = defaultdict(int)
            for event in recent_parse:
                intent_dist[event["intent"]] += 1
            status["top_intents"] = dict(sorted(intent_dist.items(), key=lambda x: -x[1])[:5])
            
            # Confidence stats
            if recent_parse:
                confs = [e["confidence"] for e in recent_parse]
                status["confidence"] = {
                    "avg": round(sum(confs) / len(confs), 3),
                    "min": round(min(confs), 3),
                    "max": round(max(confs), 3),
                    "below_threshold": sum(1 for c in confs if c < 0.80),
                }
            
            # Source distribution
            source_dist = defaultdict(int)
            for event in recent_parse:
                source_dist[event["source"]] += 1
            status["sources"] = dict(sorted(source_dist.items(), key=lambda x: -x[1]))
            
            # Alerts
            status["active_alerts"] = list(self._active_alerts)
            
            return status
    
    
    def check_drift_alerts(self) -> dict:
        """Détecte et signale les dérives."""
        alerts = {}
        
        with self._lock:
            now = datetime.now()
            recent_parse = [e for e in self._parse_events if datetime.fromisoformat(e["timestamp"]) > now - self._window]
            recent_exec = [e for e in self._execute_events if datetime.fromisoformat(e["timestamp"]) > now - self._window]
        
        if not recent_parse:
            return alerts
        
        # ── Alerte FOLLOWUP ─────────────────────────────────────────────────
        followup_count = sum(1 for e in recent_parse if e["intent"] == "FOLLOWUP")
        followup_rate = followup_count / len(recent_parse) if recent_parse else 0
        
        if followup_rate > self._baselines["followup_rate"] * 2:
            alert_key = "FOLLOWUP_SURGE"
            alerts[alert_key] = {
                "severity": "HIGH",
                "message": f"FOLLOWUP rate surge: {followup_rate*100:.1f}% (baseline {self._baselines['followup_rate']*100:.1f}%)",
                "value": followup_rate,
                "threshold": self._baselines["followup_rate"],
            }
            self._active_alerts.add(alert_key)
            logger.warning(f"[ALERT] {alert_key}: {followup_rate*100:.1f}%")
        elif alert_key := "FOLLOWUP_SURGE" in self._active_alerts:
            self._active_alerts.discard(alert_key)
        
        # ── Alerte UNKNOWN ──────────────────────────────────────────────────
        unknown_count = sum(1 for e in recent_parse if e["intent"] == "UNKNOWN")
        unknown_rate = unknown_count / len(recent_parse) if recent_parse else 0
        
        if unknown_rate > self._baselines["unknown_rate"] * 2:
            alert_key = "UNKNOWN_SURGE"
            alerts[alert_key] = {
                "severity": "MEDIUM",
                "message": f"UNKNOWN rate surge: {unknown_rate*100:.1f}%",
                "value": unknown_rate,
                "threshold": self._baselines["unknown_rate"],
            }
            self._active_alerts.add(alert_key)
            logger.warning(f"[ALERT] {alert_key}: {unknown_rate*100:.1f}%")
        elif alert_key := "UNKNOWN_SURGE" in self._active_alerts:
            self._active_alerts.discard(alert_key)
        
        # ── Alerte Confiance basse ─────────────────────────────────────────
        confidences = [e["confidence"] for e in recent_parse]
        avg_conf = sum(confidences) / len(confidences) if confidences else 1.0
        below_threshold = sum(1 for c in confidences if c < 0.80)
        
        if avg_conf < self._baselines["min_avg_confidence"]:
            alert_key = "LOW_CONFIDENCE"
            alerts[alert_key] = {
                "severity": "MEDIUM",
                "message": f"Average confidence drop: {avg_conf:.2f} (target {self._baselines['min_avg_confidence']:.2f})",
                "value": avg_conf,
                "threshold": self._baselines["min_avg_confidence"],
            }
            self._active_alerts.add(alert_key)
            logger.warning(f"[ALERT] {alert_key}: {avg_conf:.2f}")
        elif alert_key := "LOW_CONFIDENCE" in self._active_alerts:
            self._active_alerts.discard(alert_key)
        
        # ── Alerte Taux d'erreur ───────────────────────────────────────────
        if recent_exec:
            error_rate = sum(1 for e in recent_exec if not e.get("success", False)) / len(recent_exec)
            if error_rate > self._baselines["errors_rate"] * 2:
                alert_key = "HIGH_ERROR_RATE"
                alerts[alert_key] = {
                    "severity": "HIGH",
                    "message": f"Execution error rate: {error_rate*100:.1f}%",
                    "value": error_rate,
                    "threshold": self._baselines["errors_rate"],
                }
                self._active_alerts.add(alert_key)
                logger.error(f"[ALERT] {alert_key}: {error_rate*100:.1f}%")
            elif alert_key := "HIGH_ERROR_RATE" in self._active_alerts:
                self._active_alerts.discard(alert_key)
        
        return alerts
    
    
    def export_report(self, output_path: Path = None) -> dict:
        """Exporte un rapport KPI complet."""
        if output_path is None:
            output_path = BASE_DIR / "data" / "kpi_report.json"
        
        status = self.get_kpi_status()
        alerts = self.check_drift_alerts()
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "alerts": alerts,
            "baselines": self._baselines,
        }
        
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[KPI] Report exported: {output_path}")
        except Exception as e:
            logger.error(f"[KPI] Export failed: {e}")
        
        return report


# Instance globale (singleton-like)
_monitor = None
_monitor_lock = Lock()


def get_kpi_monitor() -> KPIMonitor:
    """Récupère ou crée l'instance KPI monitor."""
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = KPIMonitor()
    return _monitor
