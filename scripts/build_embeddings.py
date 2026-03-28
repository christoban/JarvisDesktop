"""
scripts/build_embeddings.py — Construit l'index d'embeddings depuis le dataset
===============================================================================
À exécuter manuellement après avoir collecté ~500 exemples dans dataset.jsonl.
Puis re-exécuter régulièrement pour enrichir l'index.

Usage :
    python scripts/build_embeddings.py
    python scripts/build_embeddings.py --n 200 --min-confidence 0.88
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dataset_builder import load_examples, get_stats
from core.embedding_router import EmbeddingRouter


def main():
    parser = argparse.ArgumentParser(description="Build embedding index for Jarvis")
    parser.add_argument("--n",              type=int,   default=200,  help="Max exemples à indexer")
    parser.add_argument("--min-confidence", type=float, default=0.85, help="Confiance minimale")
    args = parser.parse_args()

    print("=== JARVIS — Construction Index Embeddings ===\n")

    # Stats dataset
    stats = get_stats()
    print(f"Dataset actuel : {stats['total']} entrées")
    print(f"Intents couverts : {len(stats['intents'])}")
    print(f"Détail : {dict(list(stats['intents'].items())[:10])}\n")

    if stats['total'] < 50:
        print("⚠️  Dataset insuffisant (minimum 50 entrées recommandées).")
        print("    Continue d'utiliser Jarvis en mode DATASET_MODE=true pour collecter des données.")
        return

    # Charger les meilleurs exemples
    examples = load_examples(n=args.n, min_confidence=args.min_confidence)
    print(f"Exemples sélectionnés : {len(examples)} (confiance ≥ {args.min_confidence})\n")

    # Construire l'index
    router = EmbeddingRouter()
    count  = router.build_index(examples)

    print(f"\n✅ Index construit : {count} embeddings")
    print(f"   Fichier : data/intent_embeddings.json")
    print(f"\nRedémarre Jarvis pour activer le router sémantique.")


if __name__ == "__main__":
    main()