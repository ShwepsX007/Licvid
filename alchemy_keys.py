"""Encrypted admin-managed Alchemy API credentials; never expose secret values in responses.

LIQSCOPE_SECRET is the encryption root. Keep its value stable across restarts.
The ciphertext is stored under ignored data/, permissions 0600; no plaintext DB
settings/audit entries, and no credentials in logs or exception messages.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import warnings
from pathlib import Path

try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_AVAILABLE = True
except ImportError:
    # Never fall back to plaintext credentials when the optional dependency is
    # missing. The rest of the site can start; key management stays disabled.
    Fernet = None
    InvalidToken = Exception
    CRYPTO_AVAILABLE = False

from whale_screener import alchemy_key


class KeyStoreError(Exception):
    pass


def mask_key(value: str) -> str:
    """Show a short prefix and suffix without ever returning a full short key."""
    value = str(value or "")
    if len(value) <= 8:
        return "••••" + (value[-2:] if len(value) > 2 else "")
    return value[:4] + "••••" + value[-4:]


class AlchemyKeyStore:
    def __init__(self, secret: str, path: Path | None = None, env_key: str = ""):
        if not CRYPTO_AVAILABLE:
            message = "Пакет cryptography не установлен. Установите: pip install 'cryptography>=50.0.0'"
            warnings.warn("[WARNING] " + message, RuntimeWarning, stacklevel=2)
            raise KeyStoreError(message)
        if len(secret) < 16:
            raise ValueError("Stable LIQSCOPE_SECRET is required")
        self.path = path or Path(__file__).resolve().parent / "data/alchemy_keys.enc"
        material = hashlib.sha256(("liqscope:alchemy:v1:" + secret).encode()).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(material))
        # Existing environment configuration is a fallback until an admin adds
        # the first key. Never copy an environment secret into the file.
        try:
            self._env = alchemy_key(env_key) if env_key else ""
        except ValueError:
            self._env = ""  # surfaced as "no key", never log the invalid input
        self._rows = self._load()

    def _load(self) -> list[dict]:
        try:
            blob = self.path.read_bytes()
        except FileNotFoundError:
            return []
        try:
            data = json.loads(self._cipher.decrypt(blob))
            if not isinstance(data, list):
                raise ValueError
            for row in data:
                if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                        or alchemy_key(row.get("key", "")) != row["key"]):
                    raise ValueError
            return data
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
            raise KeyStoreError("Alchemy keys cannot be decrypted; restore the original LIQSCOPE_SECRET") from None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self._cipher.encrypt(json.dumps(self._rows).encode()))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temp.exists():
                temp.unlink()

    def keys(self) -> list[tuple[str, str]]:
        """Все рабочие ключи: сначала добавленные админом, затем ключ из env.

        Ключ окружения остаётся в пуле, когда админ добавляет следующий: раньше
        он исчезал из списка вместе с первым добавленным ключом, и
        «дополнительный» API становился единственным. Если новый ключ не
        годился для какой-то сети, сбор данных останавливался целиком вместо
        того, чтобы остаться на основном ключе. Дедупликация по значению: один
        и тот же ключ не считается дважды.
        """
        rows = [(r["id"], r["key"]) for r in self._rows]
        if self._env and self._env not in {key for _, key in rows}:
            rows.append(("env", self._env))
        return rows

    def public(self) -> list[dict]:
        rows = [{"id": r["id"], "hint": mask_key(r["key"]), "source": "admin"}
                for r in self._rows]
        if self._env and all(r["key"] != self._env for r in self._rows):
            rows.append({"id": "env", "hint": mask_key(self._env),
                         "source": "environment"})
        return rows

    def add(self, raw: str) -> dict:
        try:
            key = alchemy_key(raw)
        except (TypeError, ValueError):
            raise ValueError("Введите API Key или URL Alchemy /v2/…") from None
        if any(k == key for _, k in self.keys()):
            raise ValueError("Этот ключ уже добавлен")
        if len(self._rows) >= 8:
            raise ValueError("Не более 8 ключей")
        row = {"id": secrets.token_hex(8), "key": key}
        self._rows.append(row)
        try:
            self._save()
        except OSError:
            self._rows.pop()
            raise KeyStoreError("Не удалось сохранить зашифрованные ключи") from None
        return {"id": row["id"], "hint": mask_key(key), "source": "admin"}

    def get_secret(self, identifier: str) -> str | None:
        """Return a secret only to trusted server-side code; never serialize it."""
        if identifier == "env":
            return self._env or None
        for row in self._rows:
            if row["id"] == identifier:
                return row["key"]
        return None

    def remove(self, identifier: str) -> bool:
        previous = self._rows
        self._rows = [row for row in previous if row["id"] != identifier]
        if len(self._rows) == len(previous):
            return False
        try:
            self._save()
        except OSError:
            self._rows = previous
            raise KeyStoreError("Не удалось сохранить зашифрованные ключи") from None
        return True
