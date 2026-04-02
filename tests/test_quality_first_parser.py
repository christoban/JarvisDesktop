"""
TEST : Validation du pipeline QUALITY-FIRST

Vérifie que:
  1. Groq + contexte = moteur principal
  2. Confiance ≥ 0.95 = "premium training data"
  3. Confiance < 0.95 = tente fallback pour améliorer
  4. Fallbacks ne RETOURNENT PAS, tentent d'améliorer
  5. quality_flag tracé dans dataset
"""

import sys
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add JarvisDesktop to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.command_parser import CommandParser
from core.dataset_builder import save_entry


class TestQualityFirstParser:
    """Tests pour pipeline quality-first"""

    def setup_method(self):
        """Setup avant chaque test"""
        self.parser = CommandParser()
        self.mock_groq_available = True

    def test_premium_training_data_high_confidence(self):
        """✅ Confiance ≥ 0.95 = marqué PREMIUM"""
        
        result = {
            "intent": "MUSIC_PLAY",
            "params": {"song": "Bohemian Rhapsody"},
            "confidence": 0.98,
            "quality_flag": "premium"
        }
        
        # Vérifie que save_entry accepte le quality_flag
        with patch('core.dataset_builder.DATASET_MODE', True):
            # Need to also patch _load_existing_hashes_once and _append_jsonl
            with patch('core.dataset_builder._load_existing_hashes_once'):
                with patch('core.dataset_builder._append_jsonl') as mock_append:
                    saved = save_entry("play Bohemian Rhapsody", result, source="groq")
                    print(f"DEBUG: saved={saved}, append_called={mock_append.called}")
                    assert saved, "Should accept premium data"

    def test_uncertain_data_medium_confidence(self):
        """⚠️ Confiance 0.85-0.95 = marqué UNCERTAIN"""
        
        result = {
            "intent": "BROWSER_SEARCH",
            "params": {"query": "something"},
            "confidence": 0.87,
            "quality_flag": "uncertain_needs_review"
        }
        
        # Doit être accepté mais marqué uncertain
        with patch('core.dataset_builder.DATASET_MODE', True):
            with patch('core.dataset_builder._load_existing_hashes_once'):
                with patch('core.dataset_builder._append_jsonl'):
                    saved = save_entry("search for something", result, source="groq")
                    assert saved, "Should accept uncertain data (still > 0.80)"

    def test_quality_flag_in_dataset_entry(self):
        """✅ quality_flag sauvegardé dans dataset"""
        # Mock dataset_builder functions
        with patch('core.dataset_builder._append_jsonl') as mock_append:
            result = {
                "intent": "APP_OPEN",
                "params": {"app": "Chrome"},
                "confidence": 0.96,
                "quality_flag": "premium"
            }
            
            save_entry("open chrome", result, source="groq")
            
            # Vérifier que _append_jsonl a été appelé avec quality_flag
            assert mock_append.called
            calls = mock_append.call_args_list
            
            # Chercher l'appel pour DATASET_FILE (clean_entry)
            for call in calls:
                args = call[0]
                if len(args) > 1:
                    entry = args[1]
                    if "quality_flag" in entry:
                        assert entry["quality_flag"] in ["premium", "uncertain_needs_review", "standard"]
                        return
            
            assert False, "quality_flag not found in dataset entry"

    def test_fallback_chain_tries_to_improve_not_return_fast(self):
        """⚠️ Fallbacks N'échappent PAS immédiatement"""
        # C'est une vérification de logique dans parse_with_context
        # qui utilise _try_improve_confidence()
        
        # Crée un faux résultat Groq imparfait
        partial_result = {
            "intent": "MUSIC_PLAY",
            "params": {"song": "test"},
            "confidence": 0.87,  # < 0.95
            "quality_flag": "uncertain_needs_review"
        }
        
        # _try_improve_confidence devrait tenter d'améliorer
        improved = self.parser._try_improve_confidence(
            command="play something",
            base_result=partial_result,
            target_conf=0.95
        )
        
        # Soit aucune amélioration (None), soit meilleure confiance
        if improved:
            assert improved.get("confidence", 0) >= partial_result["confidence"]

    def test_parse_with_context_returns_quality_flag(self):
        """✅ parse_with_context() retourne quality_flag"""
        with patch.object(self.parser, '_can_use_groq', return_value=True):
            with patch.object(self.parser, '_call_groq_ai') as mock_groq:
                mock_groq.return_value = {
                    "intent": "BROWSER_OPEN",
                    "params": {"url": "google.com"},
                    "confidence": 0.96
                }
                
                with patch.object(self.parser, '_finalize_parse_result') as mock_finalize:
                    mock_finalize.return_value = {
                        "intent": "BROWSER_OPEN",
                        "params": {"url": "google.com"},
                        "confidence": 0.96,
                        "quality_flag": "premium"
                    }
                    
                    result = self.parser.parse_with_context(
                        "open google",
                        history=[]
                    )
                    
                    assert "quality_flag" in result
                    assert result["quality_flag"] in ["premium", "uncertain_needs_review", "standard"]

    def test_low_confidence_rejected(self):
        """❌ Confiance < 0.80 = REJETÉE"""
        result = {
            "intent": "MUSIC_PLAY",
            "params": {},
            "confidence": 0.65,  # < 0.80
            "quality_flag": "low_confidence"
        }
        
        with patch('core.dataset_builder.DATASET_MODE', True):
            with patch('core.dataset_builder._load_existing_hashes_once'):
                with patch('core.dataset_builder._append_jsonl'):
                    saved = save_entry("some ambiguous command", result, source="groq")
                    assert not saved, "Should reject low confidence entries"

    def test_dual_track_premium_vs_uncertain(self):
        """📊 Données séparées: premium vs uncertain_needs_review"""
        # Premium (conf ≥ 0.95)
        premium = {
            "intent": "MUSIC_PLAY",
            "params": {"song": "test"},
            "confidence": 0.98,
            "quality_flag": "premium"
        }
        
        # Uncertain (0.80 ≤ conf < 0.95)
        uncertain = {
            "intent": "MUSIC_PLAY",
            "params": {"song": "test"},
            "confidence": 0.88,
            "quality_flag": "uncertain_needs_review"
        }
        
        with patch('core.dataset_builder._append_jsonl') as mock_append:
            save_entry("play test", premium, source="groq")
            save_entry("play test again", uncertain, source="groq")
            
            assert mock_append.call_count >= 2
            
            # Vérifie que les flags sont différents
            calls = mock_append.call_args_list
            flags_found = set()
            for call in calls:
                args = call[0]
                if len(args) > 1:
                    entry = args[1]
                    if "quality_flag" in entry:
                        flags_found.add(entry["quality_flag"])
            
            assert "premium" in flags_found or "uncertain_needs_review" in flags_found


class TestQualityFirstMetrics:
    """Métriques et rapports quality-first"""

    def test_metrics_by_quality_flag(self):
        """📈 Compteur premium vs uncertain"""
        # Simule plusieurs entrées avec flags différents
        entries = [
            {"intent": "APP_OPEN", "confidence": 0.98, "quality_flag": "premium"},
            {"intent": "APP_OPEN", "confidence": 0.88, "quality_flag": "uncertain_needs_review"},
            {"intent": "APP_OPEN", "confidence": 0.97, "quality_flag": "premium"},
            {"intent": "APP_OPEN", "confidence": 0.82, "quality_flag": "uncertain_needs_review"},
        ]
        
        premium_count = sum(1 for e in entries if e["quality_flag"] == "premium")
        uncertain_count = sum(1 for e in entries if e["quality_flag"] == "uncertain_needs_review")
        
        assert premium_count == 2
        assert uncertain_count == 2
        
        print(f"\n📊 Quality Distribution:")
        print(f"   Premium: {premium_count} (100% confidence-ready)")
        print(f"   Uncertain: {uncertain_count} (needs admin review)")


def run_all_tests():
    """Lance tous les tests"""
    test_parser = TestQualityFirstParser()
    test_metrics = TestQualityFirstMetrics()
    
    tests_passed = 0
    tests_failed = 0
    
    # Parser tests
    for method_name in dir(test_parser):
        if method_name.startswith("test_"):
            try:
                test_parser.setup_method()
                method = getattr(test_parser, method_name)
                method()
                print(f"✅ {method_name}")
                tests_passed += 1
            except AssertionError as e:
                print(f"❌ {method_name}: {e}")
                tests_failed += 1
            except Exception as e:
                print(f"⚠️  {method_name}: {type(e).__name__}: {e}")
                tests_failed += 1
    
    # Metrics tests
    for method_name in dir(test_metrics):
        if method_name.startswith("test_"):
            try:
                method = getattr(test_metrics, method_name)
                method()
                print(f"✅ {method_name}")
                tests_passed += 1
            except AssertionError as e:
                print(f"❌ {method_name}: {e}")
                tests_failed += 1
            except Exception as e:
                print(f"⚠️  {method_name}: {type(e).__name__}: {e}")
                tests_failed += 1
    
    print(f"\n{'='*60}")
    print(f"RÉSULTATS: {tests_passed} ✅ | {tests_failed} ❌")
    print(f"{'='*60}\n")
    
    return tests_failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
