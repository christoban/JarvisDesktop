#!/usr/bin/env python3
"""
scripts/agent_kpi_integration.py — Exemple d'intégration KPI dans Agent
========================================================================
Démontre comment injecter le KPI monitor dans le pipeline Agent
parse → execute pour collecter les métriques en live.

USAGE (à intégrer dans Agent.execute() real):
    from core.kpi_monitor import get_kpi_monitor
    
    def execute(self, command: str):
        monitor = get_kpi_monitor()
        
        # Parse
        result = self.parser.parse(command)
        monitor.record_parse(command, result)
        
        # Execute
        exec_result = self.executor.execute(result["intent"], result["params"])
        monitor.record_execute(result["intent"], exec_result["success"], exec_result.get("error", ""))
        
        # Check alerts
        alerts = monitor.check_drift_alerts()
        if alerts:
            logger.warning(f"KPI Alerts: {alerts}")
        
        return exec_result
"""

import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.kpi_monitor import get_kpi_monitor
from config.logger import get_logger
from config.settings import BASE_DIR

logger = get_logger(__name__)


class AgentKPIIntegration:
    """Exemple d'intégration KPI dans Agent."""
    
    def __init__(self):
        self.monitor = get_kpi_monitor()
    
    
    def simulate_command_execution(self, command: str, intent: str, confidence: float, success: bool = True):
        """Simule l'exécution d'une commande avec collecte KPI."""
        
        # Enregistrer le parsing
        self.monitor.record_parse(command, {
            "intent": intent,
            "confidence": confidence,
            "source": "groq",
        })
        
        # Enregistrer l'exécution
        error = "" if success else "Execution failed"
        self.monitor.record_execute(intent, success=success, error=error)
        
        # Logger le status courant
        status = self.monitor.get_kpi_status()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Command: {command}")
        print(f"  Intent: {intent} | Confidence: {confidence:.2f}")
        print(f"  Status: Total={status['total_commands']} | Recent={status['recent_window']['parse_count']}")
        
        # Vérifier les alertes
        alerts = self.monitor.check_drift_alerts()
        if alerts:
            print(f"  ⚠️  ALERTS: {len(alerts)}")
            for alert_key, alert_data in alerts.items():
                print(f"    - {alert_key}: {alert_data['message']}")
    
    
    def print_kpi_dashboard(self):
        """Affiche le dashboard KPI courant."""
        status = self.monitor.get_kpi_status()
        alerts = self.monitor.check_drift_alerts()
        
        print("\n" + "="*70)
        print("KPI DASHBOARD")
        print("="*70)
        
        print(f"\n📊 COUNTERS:")
        print(f"  ├─ Total commands  : {status['total_commands']}")
        print(f"  ├─ Total executions: {status['total_executions']}")
        print(f"  ├─ Total errors    : {status['total_errors']}")
        print(f"  └─ System uptime   : {status['uptime_minutes']} min")
        
        print(f"\n🪟 WINDOW (last {status['window_minutes']} min):")
        print(f"  ├─ Commands parsed : {status['recent_window']['parse_count']}")
        print(f"  ├─ Commands exec'd : {status['recent_window']['exec_count']}")
        print(f"  ├─ Errors          : {status['recent_window']['error_count']}")
        print(f"  └─ Idle            : {status['idle']}")
        
        if "confidence" in status:
            conf = status["confidence"]
            print(f"\n📈 CONFIDENCE:")
            print(f"  ├─ Average  : {conf['avg']:.3f}")
            print(f"  ├─ Min      : {conf['min']:.3f}")
            print(f"  ├─ Max      : {conf['max']:.3f}")
            print(f"  └─ <0.80    : {conf['below_threshold']}")
        
        print(f"\n🔝 TOP INTENTS:")
        for intent, count in status['top_intents'].items():
            print(f"  ├─ {intent:25s} : {count}")
        
        print(f"\n⚙️  SOURCES:")
        for source, count in status['sources'].items():
            print(f"  ├─ {source:15s} : {count}")
        
        if alerts:
            print(f"\n🚨 ACTIVE ALERTS ({len(alerts)}):")
            for alert_key, alert_data in alerts.items():
                severity = alert_data["severity"]
                message = alert_data["message"]
                print(f"  ├─ [{severity}] {alert_key}")
                print(f"  │  └─ {message}")
        else:
            print(f"\n✅ NO ALERTS")
        
        print("\n" + "="*70)


def main():
    """Simulation de 20 commandes avec KPI collection."""
    
    integration = AgentKPIIntegration()
    
    # Scénario 1 : Fonctionnement normal
    print("\n[SCENARIO 1] Normal Operation\n")
    
    commands = [
        ("ouvre chrome", "APP_OPEN", 0.99),
        ("ferme fenêtre", "WINDOW_CLOSE", 0.98),
        ("nouvel onglet", "BROWSER_NEW_TAB", 0.95),
        ("joue ma playlist", "MUSIC_PLAYLIST_PLAY", 0.95),
        ("quelle heure", "SYSTEM_TIME", 0.98),
    ]
    
    for command, intent, conf in commands:
        integration.simulate_command_execution(command, intent, conf, success=True)
    
    integration.print_kpi_dashboard()
    
    # Scénario 2 : Surge de FOLLOWUP
    print("\n[SCENARIO 2] FOLLOWUP Surge\n")
    
    followup_commands = [
        ("non plutôt chrome", "FOLLOWUP", 0.90),
        ("attends, pas ça", "FOLLOWUP", 0.88),
        ("change d'avis", "FOLLOWUP", 0.85),
    ]
    
    for command, intent, conf in followup_commands:
        integration.simulate_command_execution(command, intent, conf, success=True)
    
    integration.print_kpi_dashboard()
    
    # Scénario 3 : Confiance basse
    print("\n[SCENARIO 3] Low Confidence Wave\n")
    
    low_conf_commands = [
        ("euh quelque chose", "UNKNOWN", 0.65),
        ("je sais pas", "UNKNOWN", 0.60),
        ("peut être chrome", "APP_OPEN", 0.70),
        ("ouais", "UNKNOWN", 0.55),
        ("ok fais quelque chose", "UNKNOWN", 0.58),
        ("trop dur", "UNKNOWN", 0.62),
        ("comprends pas", "UNKNOWN", 0.68),
        ("bizarre", "UNKNOWN", 0.64),
    ]
    
    for command, intent, conf in low_conf_commands:
        integration.simulate_command_execution(command, intent, conf, success=True)
    
    integration.print_kpi_dashboard()
    
    # Scénario 4 : Erreurs d'exécution
    print("\n[SCENARIO 4] Execution Failures\n")
    
    error_commands = [
        ("ouvre chrome", "APP_OPEN", 0.99, False),
        ("ferme fenêtre", "WINDOW_CLOSE", 0.98, False),
        ("jouez musique", "MUSIC_PLAY", 0.90, False),
        ("ouvre un lien", "BROWSER_OPEN", 0.95, False),
    ]
    
    for command, intent, conf, success in error_commands:
        integration.simulate_command_execution(command, intent, conf, success=success)
    
    integration.print_kpi_dashboard()
    
    # Exporter le rapport final
    print("\n[EXPORT] Exporting final KPI report...\n")
    report_path = BASE_DIR / "data" / "kpi_demo_report.json"
    integration.monitor.export_report(report_path)
    print(f"✅ Report saved: {report_path}")


if __name__ == "__main__":
    main()
