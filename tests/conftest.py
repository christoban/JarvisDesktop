import pytest
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def mock_groq_response(monkeypatch):
    """
    Intercepte automatiquement tous les appels à Groq durant les tests
    pour retourner un JSON simulé sans consommer de tokens.
    """
    def mock_create(*args, **kwargs):
        # On simule la structure de réponse de Groq/OpenAI
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        # On peut adapter la réponse selon la commande si besoin
        mock_resp.choices[0].message.content = '{"intent": "HELP", "confidence": 0.99, "params": {}}'
        return mock_resp

    # On applique le mock sur le client OpenAI/Groq si le module existe.
    # Certains environnements CI/dev n'ont pas le package openai installé.
    try:
        monkeypatch.setattr("openai.resources.chat.completions.Completions.create", mock_create)
    except Exception:
        # Ne pas bloquer toute la suite de tests si openai n'est pas disponible.
        pass