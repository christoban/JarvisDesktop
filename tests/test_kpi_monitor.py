"""
tests/test_kpi_monitor.py — Tests du KPI Monitor
================================================
Valide la collecte de métriques et la détection de drift.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.kpi_monitor import KPIMonitor


@pytest.fixture
def monitor():
    """Crée une nouvelle instance KPI monitor pour chaque test."""
    return KPIMonitor(window_minutes=5)


class TestKPICollection:
    """Tests de collecte des métriques."""
    
    def test_record_parse_basic(self, monitor):
        """Enregistre un résultat de parsing."""
        result = {
            "intent": "APP_OPEN",
            "confidence": 0.99,
            "source": "groq",
        }
        
        monitor.record_parse("ouvre chrome", result)
        
        status = monitor.get_kpi_status()
        assert status["total_commands"] == 1
        assert status["recent_window"]["parse_count"] == 1
    
    
    def test_record_execute_success(self, monitor):
        """Enregistre une exécution réussie."""
        monitor.record_execute("APP_OPEN", success=True)
        
        status = monitor.get_kpi_status()
        assert status["total_executions"] == 1
        assert status["total_errors"] == 0
    
    
    def test_record_execute_failure(self, monitor):
        """Enregistre une exécution échouée."""
        monitor.record_execute("BROWSER_SEARCH", success=False, error="Browser not available")
        
        status = monitor.get_kpi_status()
        assert status["total_executions"] == 1
        assert status["total_errors"] == 1
    
    
    def test_multiple_records_aggregation(self, monitor):
        """Agrège plusieurs records."""
        for i in range(5):
            monitor.record_parse(f"command {i}", {
                "intent": "APP_OPEN",
                "confidence": 0.95 + (i * 0.01),
                "source": "groq",
            })
        
        status = monitor.get_kpi_status()
        assert status["total_commands"] == 5
        assert status["recent_window"]["parse_count"] == 5
        assert status["confidence"]["avg"] >= 0.95


class TestKPIStatus:
    """Tests du status KPI."""
    
    def test_status_structure(self, monitor):
        """Valide la structure du status retourné."""
        monitor.record_parse("test", {"intent": "APP_OPEN", "confidence": 0.95, "source": "groq"})
        
        status = monitor.get_kpi_status()
        
        assert "total_commands" in status
        assert "total_executions" in status
        assert "total_errors" in status
        assert "window_minutes" in status
        assert "recent_window" in status
        assert "confidence" in status
        assert "sources" in status
        assert "top_intents" in status
        assert "idle" in status
        assert "active_alerts" in status
    
    
    def test_idle_detection(self, monitor):
        """Détecte quand le système est inactif."""
        status = monitor.get_kpi_status()
        assert status["idle"] is True
        
        monitor.record_parse("test", {"intent": "APP_OPEN", "confidence": 0.95, "source": "groq"})
        status = monitor.get_kpi_status()
        assert status["idle"] is False
    
    
    def test_top_intents_ranking(self, monitor):
        """Classe les intents par fréquence."""
        intents = ["APP_OPEN", "WINDOW_CLOSE", "APP_OPEN", "BROWSER_SEARCH", "APP_OPEN"]
        
        for intent in intents:
            monitor.record_parse("cmd", {"intent": intent, "confidence": 0.95, "source": "groq"})
        
        status = monitor.get_kpi_status()
        top = status["top_intents"]
        
        assert top["APP_OPEN"] == 3
        assert top["WINDOW_CLOSE"] == 1
        assert top["BROWSER_SEARCH"] == 1


class TestDriftDetection:
    """Tests de la détection de drift."""
    
    def test_no_alerts_normal_operation(self, monitor):
        """Pas d'alerte en fonctionnement normal."""
        for _ in range(10):
            monitor.record_parse("cmd", {
                "intent": "APP_OPEN",
                "confidence": 0.95,
                "source": "groq",
            })
        
        alerts = monitor.check_drift_alerts()
        assert len(alerts) == 0
    
    
    def test_followup_surge_alert(self, monitor):
        """Alerte si FOLLOWUP rate monte."""
        # Baseline: 5%, alerte si > 10%
        for i in range(5):
            intent = "FOLLOWUP" if i < 2 else "APP_OPEN"  # 40% FOLLOWUP
            monitor.record_parse("cmd", {
                "intent": intent,
                "confidence": 0.90,
                "source": "context",
            })
        
        alerts = monitor.check_drift_alerts()
        
        # 40% > baseline 5% * 2 = 10%
        assert "FOLLOWUP_SURGE" in alerts
    
    
    def test_unknown_surge_alert(self, monitor):
        """Alerte si UNKNOWN rate monte."""
        for i in range(10):
            intent = "UNKNOWN" if i < 3 else "APP_OPEN"  # 30% UNKNOWN
            monitor.record_parse("cmd", {
                "intent": intent,
                "confidence": 0.75,
                "source": "fallback",
            })
        
        alerts = monitor.check_drift_alerts()
        
        # 30% > baseline 2% * 2 = 4%
        assert "UNKNOWN_SURGE" in alerts
    
    
    def test_confidence_alert(self, monitor):
        """Alerte si confiance moyenne baisse."""
        for i in range(8):
            monitor.record_parse("cmd", {
                "intent": "APP_OPEN",
                "confidence": 0.75,  # Below 0.90 threshold
                "source": "groq",
            })
        
        alerts = monitor.check_drift_alerts()
        
        assert "LOW_CONFIDENCE" in alerts
        assert alerts["LOW_CONFIDENCE"]["value"] < 0.90
    
    
    def test_error_rate_alert(self, monitor):
        """Alerte si error rate monte."""
        for i in range(10):
            monitor.record_parse(f"cmd{i}", {
                "intent": "APP_OPEN",
                "confidence": 0.95,
                "source": "groq",
            })
            
            success = False if i < 3 else True  # 30% errors
            monitor.record_execute("APP_OPEN", success=success)
        
        alerts = monitor.check_drift_alerts()
        
        # 30% > baseline 1% * 2 = 2%
        assert "HIGH_ERROR_RATE" in alerts


class TestKPIExport:
    """Tests d'export des rapports."""
    
    def test_export_report_structure(self, monitor, tmp_path):
        """Exporte un rapport structuré."""
        monitor.record_parse("cmd", {
            "intent": "APP_OPEN",
            "confidence": 0.95,
            "source": "groq",
        })
        
        output = tmp_path / "kpi_report.json"
        report = monitor.export_report(output)
        
        assert "timestamp" in report
        assert "status" in report
        assert "alerts" in report
        assert "baselines" in report
        assert output.exists()


class TestKPISingleton:
    """Tests du pattern singleton."""
    
    def test_get_kpi_monitor_singleton(self):
        """test_kpi_monitor.py retrieves same instance."""
        from core.kpi_monitor import get_kpi_monitor
        
        m1 = get_kpi_monitor()
        m2 = get_kpi_monitor()
        
        # Même instance en mémoire
        assert m1 is m2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
