import chromadb
from chromadb.utils import embedding_functions
import time
from pathlib import Path

class VectorDB:
    """Moteur de mémoire sémantique utilisant ChromaDB."""
    
    def __init__(self, db_path: Path):
        self.client = chromadb.PersistentClient(path=str(db_path))
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name="jarvis_long_term_memory",
            embedding_function=self.embedding_fn
        )

    def add_memory(self, text: str, metadata: dict = None):
        """Ajoute un souvenir à la base vectorielle."""
        if not text.strip(): return
        mem_id = f"mem_{int(time.time() * 1000)}"
        self.collection.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[mem_id]
        )

    def query_memory(self, query_text: str, limit: int = 3):
        """Cherche des souvenirs similaires à la requête."""
        results = self.collection.query(
            query_texts=[query_text],
            n_results=limit
        )
        # On aplatit les résultats pour l'agent
        memories = []
        if results and results['documents']:
            for doc in results['documents'][0]:
                memories.append(doc)
        return memories

    def clear_all(self):
        """Purger toute la mémoire sémantique."""
        self.client.delete_collection("jarvis_long_term_memory")