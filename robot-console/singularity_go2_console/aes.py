"""AES-128 key loading for Unitree Go2 WebRTC (newer firmware).

Priority:
1. explicit CLI / aes_key_file argument
2. UNITREE_AES_128_KEY environment variable
3. ~/.config/go2ctl/aes_key

The key is never printed, logged, or included in exceptions/telemetry.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_AES_KEY_PATH = Path.home() / ".config" / "go2ctl" / "aes_key"
_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")

AesSource = Literal["cli_file", "env", "config_file", "none"]


class AesKeyError(ValueError):
    """Raised for missing/invalid AES keys. Message never contains the key."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AesKeyMaterial:
    """Validated key material. str() / repr() never expose the secret."""

    source: AesSource
    _value: str

    @property
    def value(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"AesKeyMaterial(source={self.source!r}, value=<redacted>)"

    def __str__(self) -> str:  # pragma: no cover - defensive
        return f"AesKeyMaterial(source={self.source}, value=<redacted>)"


def validate_aes_key(raw: str | None) -> str:
    if raw is None:
        raise AesKeyError("AES_KEY_REQUIRED", "AES-128 key is required")
    cleaned = raw.strip()
    if not cleaned:
        raise AesKeyError("AES_KEY_REQUIRED", "AES-128 key is required")
    if not _HEX32.fullmatch(cleaned):
        raise AesKeyError(
            "AES_KEY_INVALID",
            "AES-128 key must be exactly 32 hexadecimal characters",
        )
    return cleaned.lower()


def load_aes_key(
    *,
    key_file: str | Path | None = None,
    env_var: str = "UNITREE_AES_128_KEY",
    config_path: Path | None = None,
) -> AesKeyMaterial:
    """Load AES key using the documented priority order."""

    if key_file is not None:
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise AesKeyError("AES_KEY_REQUIRED", f"AES key file not found: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raise AesKeyError("AES_KEY_REQUIRED", f"AES key file unreadable: {path}") from None
        return AesKeyMaterial(source="cli_file", _value=validate_aes_key(raw))

    env_value = os.environ.get(env_var)
    if env_value is not None and env_value.strip() != "":
        return AesKeyMaterial(source="env", _value=validate_aes_key(env_value))

    cfg = (config_path or DEFAULT_AES_KEY_PATH).expanduser()
    if cfg.is_file():
        try:
            raw = cfg.read_text(encoding="utf-8")
        except OSError:
            raise AesKeyError("AES_KEY_REQUIRED", f"AES key file unreadable: {cfg}") from None
        return AesKeyMaterial(source="config_file", _value=validate_aes_key(raw))

    raise AesKeyError("AES_KEY_REQUIRED", "AES-128 key not found in file, env, or config")


def redact_secrets(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Replace known secret substrings before logging or returning errors."""
    redacted = text
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted
