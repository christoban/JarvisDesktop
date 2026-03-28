"""
core/embedding_router.py — Router sémantique via embeddings Ollama
==================================================================
Utilise nomic-embed-text pour comparer sémantiquement la commande
aux exemples connus du dataset.

Avantage : ultra rapide (5-20 ms), pas d'hallucination possible.
Limite : ne fonctionne que sur des commandes proches des exemples vus.

Architecture :
  1. Calculer l'embedding de la commande utilisateur
  2. Comparer avec les embeddings pré-calculés du dataset
  3. Si cosine similarity > EMBED_CONFIDENCE → retourner l'intent directement
  4. Sinon → passer au LocalLLM
"""

import json
import math
import requests
from pathlib import Path
from config.logger import get_logger
from config.settings import OLLAMA_URL, OLLAMA_EMBED_MODEL, EMBED_CONFIDENCE, EMBED_CACHE_FILE

logger = get_logger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calcule la similarité cosine entre deux vecteurs."""
    dot   = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_embedding(text: str) -> list[float] | None:
    """Appelle l'API Ollama pour obtenir l'embedding d'un texte."""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
            timeout=5
        )
        if r.status_code == 200:
            return r.json().get("embedding")
    except Exception as e:
        logger.debug(f"Embedding error: {e}")
    return None


class EmbeddingRouter:
    """
    Router sémantique basé sur les embeddings.
    Charge les embeddings pré-calculés du dataset au démarrage.
    """

    def __init__(self):
        self._index: list[dict] = []   # [{"input", "intent", "params", "embedding"}]
        self._available = False
        self._load_index()

    def _load_index(self):
        """Charge l'index d'embeddings depuis le fichier cache."""
        if not EMBED_CACHE_FILE.exists():
            logger.info("EmbeddingRouter: index vide — lance build_index() d'abord")
            return
        try:
            with open(str(EMBED_CACHE_FILE), encoding="utf-8") as f:
                self._index = json.load(f)
            self._available = len(self._index) > 0
            logger.info(f"EmbeddingRouter: {len(self._index)} embeddings chargés")
        except Exception as e:
            logger.error(f"EmbeddingRouter load error: {e}")

    def build_index(self, examples: list[dict]) -> int:
        """
        Construit l'index d'embeddings depuis les exemples du dataset.
        À appeler une fois après avoir collecté suffisamment de données.

        Args:
            examples : liste de dicts {"input", "intent", "params"}

        Returns:
            Nombre d'embeddings générés
        """
        logger.info(f"Construction index embeddings ({len(examples)} exemples)...")
        index = []
        for i, ex in enumerate(examples):
            emb = _get_embedding(ex["input"])
            if emb:
                index.append({
                    "input":     ex["input"],
                    "intent":    ex["intent"],
                    "params":    ex.get("params", {}),
                    "embedding": emb,
                })
                if (i + 1) % 50 == 0:
                    logger.info(f"  {i + 1}/{len(examples)} embeddings calculés...")

        # Sauvegarder le cache
        EMBED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(str(EMBED_CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)

        self._index     = index
        self._available = len(index) > 0
        logger.info(f"Index embeddings construit : {len(index)} entrées")
        return len(index)

    def route(self, command: str) -> dict | None:
        """
        Tente de router la commande par similarité sémantique.

        Returns:
            dict {"intent", "confidence", "params", "source": "embedding"}
            ou None si confiance insuffisante
        """
        if not self._available or not self._index:
            return None

        cmd_emb = _get_embedding(command)
        if not cmd_emb:
            return None

        # Trouver le meilleur match
        best_score  = 0.0
        best_entry  = None

        for entry in self._index:
            score = _cosine_similarity(cmd_emb, entry["embedding"])
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry and best_score >= EMBED_CONFIDENCE:
            logger.info(f"EmbeddingRouter: '{command}' → {best_entry['intent']} (score={best_score:.3f})")
            return {
                "intent":     best_entry["intent"],
                "confidence": round(best_score, 3),
                "params":     best_entry["params"],
                "source":     "embedding",
            }

        logger.debug(f"EmbeddingRouter: confiance insuffisante ({best_score:.3f} < {EMBED_CONFIDENCE})")
        return None

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def index_size(self) -> int:
        return len(self._index)