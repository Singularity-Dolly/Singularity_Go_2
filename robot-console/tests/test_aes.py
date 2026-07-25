"""Unit tests for AES key loading, validation, priority, and redaction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from singularity_go2_console.aes import (
    AesKeyError,
    AesKeyMaterial,
    load_aes_key,
    redact_secrets,
    validate_aes_key,
)


def test_validate_accepts_32_hex() -> None:
    assert validate_aes_key(" 0123456789abcdef0123456789ABCDEF ") == (
        "0123456789abcdef0123456789abcdef"
    )


def test_validate_rejects_short() -> None:
    with pytest.raises(AesKeyError) as exc:
        validate_aes_key("abc")
    assert exc.value.code == "AES_KEY_INVALID"


def test_validate_rejects_too_long_no_truncate() -> None:
    # Must fail closed — never truncate a 40-char key into 32.
    with pytest.raises(AesKeyError) as exc:
        validate_aes_key("0123456789abcdef0123456789abcdefFFFF")
    assert exc.value.code == "AES_KEY_INVALID"
    assert "FFFF" not in str(exc.value)


def test_validate_rejects_empty_after_strip() -> None:
    with pytest.raises(AesKeyError) as exc:
        validate_aes_key("   \n\t  ")
    assert exc.value.code == "AES_KEY_REQUIRED"



def test_key_file_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file = tmp_path / "cli.key"
    key_file.write_text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", encoding="utf-8")
    env_file = tmp_path / "env_should_not_win.key"
    env_file.write_text("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", encoding="utf-8")
    monkeypatch.setenv("UNITREE_AES_128_KEY", "cccccccccccccccccccccccccccccccc")
    cfg = tmp_path / "aes_key"
    cfg.write_text("dddddddddddddddddddddddddddddddd", encoding="utf-8")

    material = load_aes_key(key_file=key_file, config_path=cfg)
    assert material.source == "cli_file"
    assert material.value.startswith("aaaa")


def test_env_priority_over_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNITREE_AES_128_KEY", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    cfg = tmp_path / "aes_key"
    cfg.write_text("dddddddddddddddddddddddddddddddd", encoding="utf-8")
    material = load_aes_key(config_path=cfg)
    assert material.source == "env"
    assert material.value.startswith("bbbb")


def test_config_file_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)
    cfg = tmp_path / "aes_key"
    cfg.write_text("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\n", encoding="utf-8")
    material = load_aes_key(config_path=cfg)
    assert material.source == "config_file"
    assert material.value.startswith("eeee")


def test_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)
    with pytest.raises(AesKeyError) as exc:
        load_aes_key(config_path=tmp_path / "missing")
    assert exc.value.code == "AES_KEY_REQUIRED"


def test_material_repr_redacts() -> None:
    material = AesKeyMaterial(source="env", _value="ffffffffffffffffffffffffffffffff")
    assert "ffff" not in repr(material)
    assert "ffff" not in str(material)
    assert "<redacted>" in repr(material)


def test_redact_secrets() -> None:
    secret = "0123456789abcdef0123456789abcdef"
    assert secret not in redact_secrets(f"failed key={secret}", [secret])
