"""
crypto.py — Chiffrement des messages téléphone ↔ bridge
SEMAINE 10 — JEUDI

Schéma :
  AES-256-GCM (authentifié) — chiffrement symétrique avec la clé dérivée
  de SECRET_TOKEN via PBKDF2-HMAC-SHA256.

  Chaque message chiffré = base64( IV(12) + CIPHERTEXT + TAG(16) )

  Le téléphone (TypeScript) utilise la même clé dérivée via
  crypto.subtle.importKey() + AES-GCM.

Utilisation :
    from security.crypto import MessageCrypto
    mc = MessageCrypto()

    encrypted = mc.encrypt({"command": "ouvre chrome"})
    decrypted = mc.decrypt(encrypted)
"""

import base64
import hashlib
import json
import os
from config.logger   import get_logger
from config.settings import SECRET_TOKEN

logger = get_logger(__name__)

# ── Détection backends ────────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.info("MessageCrypto : 'cryptography' absent — pip install cryptography")

# ── Constantes ────────────────────────────────────────────────────────────────
SALT          = b"JarvisWindows_v1_Salt_2024"   # sel fixe (échangé hors-bande)
PBKDF2_ITER   = 100_000
KEY_LEN       = 32    # 256 bits
IV_LEN        = 12    # 96 bits (recommandé GCM)
TAG_LEN       = 16    # 128 bits


class MessageCrypto:
    """
    Chiffrement AES-256-GCM des messages JSON.
    Fallback : pas de chiffrement si 'cryptography' absent (log warning).
    """

    def __init__(self):
        self.available = _CRYPTO_AVAILABLE
        if _CRYPTO_AVAILABLE:
            self._key = self._derive_key(SECRET_TOKEN)
            self._aes = AESGCM(self._key)
            logger.info("MessageCrypto initialisé — AES-256-GCM")
        else:
            self._key = None
            self._aes = None
            logger.warning("MessageCrypto : chiffrement désactivé (cryptography absent)")

    # ── API publique ─────────────────────────────────────────────────────────

    def encrypt(self, data: dict) -> str:
        """
        Chiffre un dict en chaîne base64 transportable.

        Returns:
            str base64 — ou JSON clair si crypto indisponible
        """
        if not self.available:
            return json.dumps(data)

        try:
            plaintext = json.dumps(data, ensure_ascii=False).encode()
            iv        = os.urandom(IV_LEN)
            ciphertext = self._aes.encrypt(iv, plaintext, None)
            # Format : IV(12) || CIPHERTEXT+TAG
            payload   = iv + ciphertext
            return base64.b64encode(payload).decode()
        except Exception as e:
            logger.error(f"Chiffrement échoué : {e}")
            return json.dumps(data)   # fallback non chiffré

    def decrypt(self, payload: str) -> dict:
        """
        Déchiffre une chaîne base64 en dict.

        Returns:
            dict — ou {} si échec
        """
        if not self.available:
            try:
                return json.loads(payload)
            except Exception:
                return {}

        try:
            raw        = base64.b64decode(payload)
            iv         = raw[:IV_LEN]
            ciphertext = raw[IV_LEN:]
            plaintext  = self._aes.decrypt(iv, ciphertext, None)
            return json.loads(plaintext.decode())
        except Exception as e:
            logger.error(f"Déchiffrement échoué : {e}")
            # Essayer comme JSON clair (rétro-compat)
            try:
                return json.loads(payload)
            except Exception:
                return {}

    def is_encrypted(self, payload: str) -> bool:
        """Détecte si un payload est chiffré (base64 valide, non JSON)."""
        try:
            json.loads(payload)
            return False   # JSON valide → non chiffré
        except Exception:
            pass
        try:
            base64.b64decode(payload, validate=True)
            return True
        except Exception:
            return False

    def health_check(self) -> dict:
        if not self.available:
            return {"available": False,
                    "message": "pip install cryptography"}
        try:
            test    = self.encrypt({"test": True})
            decoded = self.decrypt(test)
            ok      = decoded.get("test") is True
            return {"available": ok, "algorithm": "AES-256-GCM",
                    "message": "OK" if ok else "Auto-test échoué"}
        except Exception as e:
            return {"available": False, "message": str(e)}

    # ── Utilitaires ───────────────────────────────────────────────────────────

    @staticmethod
    def _derive_key(secret: str) -> bytes:
        """Dérive une clé AES-256 depuis SECRET_TOKEN via PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LEN,
            salt=SALT,
            iterations=PBKDF2_ITER,
        )
        return kdf.derive(secret.encode())

    @staticmethod
    def derive_key_b64(secret: str) -> str:
        """
        Retourne la clé dérivée en base64.
        Sert à initialiser la même clé côté TypeScript (Web Crypto API).
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LEN,
            salt=SALT,
            iterations=PBKDF2_ITER,
        )
        return base64.b64encode(kdf.derive(secret.encode())).decode()