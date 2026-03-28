#!/usr/bin/env python3
"""
Test complet du pipeline hybride Phase 1
Vérifie que le dataset logging fonctionne correctement
"""

import sys
from pathlib import Path

# Ajouter le parent à sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dataset_builder import save_entry, load_examples, get_stats
from config.logger import get_logger

logger = get_logger(__name__)

def test_dataset_logging():
    """Test que les entrées sont bien sauvegardées dans le dataset"""
    print("\n=== TEST DATASET LOGGING ===\n")
    
    test_cases = [
        {
            "input": "ouvre chrome",
            "result": {"intent": "APP_OPEN", "params": {"app_name": "chrome"}, "confidence": 0.95},
            "source": "test"
        },
        {
            "input": "joue ma playlist jazz",
            "result": {"intent": "MUSIC_PLAYLIST_PLAY", "params": {"playlist_name": "jazz"}, "confidence": 0.92},
            "source": "test"
        },
        {
            "input": "quelle heure est-il",
            "result": {"intent": "SYSTEM_TIME", "params": {}, "confidence": 0.99},
            "source": "test"
        },
        {
            "input": "crée un dossier sur le bureau",
            "result": {"intent": "FOLDER_CREATE", "params": {"path": "dossier", "location": "Desktop"}, "confidence": 0.88},
            "source": "test"
        },
    ]
    
    saved_count = 0
    for tc in test_cases:
        result = save_entry(tc["input"], tc["result"], source=tc["source"])
        if result:
            saved_count += 1
            print(f"✅ Sauvegardé: '{tc['input']}' → {tc['result']['intent']}")
        else:
            print(f"❌ Non sauvegardé: '{tc['input']}'")
    
    print(f"\n{saved_count}/{len(test_cases)} entrées sauvegardées\n")
    
    # Afficher les stats
    stats = get_stats()
    print(f"Stats dataset: {stats['total']} entrées")
    print(f"Intents: {stats['intents']}\n")
    
    # Charger les exemples
    examples = load_examples(n=10, min_confidence=0.80)
    print(f"Exemples chargés: {len(examples)}")
    for ex in examples[:3]:
        print(f"  - {ex['input']} → {ex['intent']}")
    print()

if __name__ == "__main__":
    test_dataset_logging()
    print("✅ Test complet")
