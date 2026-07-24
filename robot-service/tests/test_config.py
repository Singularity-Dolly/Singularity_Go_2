"""Tests for GatewaySettings — pydantic-settings configuration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from dolly_gateway.config import GatewaySettings


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_robot_id(self) -> None:
        settings = GatewaySettings()
        assert settings.robot_id == "unknown"

    def test_default_port(self) -> None:
        settings = GatewaySettings()
        assert settings.port == 8780

    def test_default_host(self) -> None:
        settings = GatewaySettings()
        assert settings.host == "0.0.0.0"

    def test_default_auth_token(self) -> None:
        settings = GatewaySettings()
        assert settings.auth_token is None

    def test_default_robot_ip(self) -> None:
        settings = GatewaySettings()
        assert settings.robot_ip is None

    def test_default_log_level(self) -> None:
        settings = GatewaySettings()
        assert settings.log_level == "INFO"

    def test_default_command_timeout(self) -> None:
        settings = GatewaySettings()
        assert settings.command_timeout_s == 5.0

    def test_default_heartbeat_interval(self) -> None:
        settings = GatewaySettings()
        assert settings.heartbeat_interval_s == 0.5

    def test_default_heartbeat_timeout(self) -> None:
        settings = GatewaySettings()
        assert settings.heartbeat_timeout_s == 1.5

    def test_default_max_queue_size(self) -> None:
        settings = GatewaySettings()
        assert settings.max_queue_size == 8

    def test_default_frame_max_age(self) -> None:
        settings = GatewaySettings()
        assert settings.frame_max_age_s == 0.5


# ---------------------------------------------------------------------------
# Environment variable loading
# ---------------------------------------------------------------------------

class TestEnvLoading:
    def test_robot_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_ROBOT_ID", "go2-test-001")
        settings = GatewaySettings()
        assert settings.robot_id == "go2-test-001"

    def test_port_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_PORT", "9999")
        settings = GatewaySettings()
        assert settings.port == 9999

    def test_host_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_HOST", "127.0.0.1")
        settings = GatewaySettings()
        assert settings.host == "127.0.0.1"

    def test_auth_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "secret-token-123")
        settings = GatewaySettings()
        assert settings.auth_token == "secret-token-123"

    def test_robot_ip_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_ROBOT_IP", "192.168.123.161")
        settings = GatewaySettings()
        assert settings.robot_ip == "192.168.123.161"

    def test_log_level_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_LOG_LEVEL", "DEBUG")
        settings = GatewaySettings()
        assert settings.log_level == "DEBUG"

    def test_command_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_COMMAND_TIMEOUT_S", "3.0")
        settings = GatewaySettings()
        assert settings.command_timeout_s == 3.0

    def test_heartbeat_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_HEARTBEAT_INTERVAL_S", "1.0")
        settings = GatewaySettings()
        assert settings.heartbeat_interval_s == 1.0

    def test_heartbeat_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_HEARTBEAT_TIMEOUT_S", "3.0")
        settings = GatewaySettings()
        assert settings.heartbeat_timeout_s == 3.0

    def test_max_queue_size_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_MAX_QUEUE_SIZE", "16")
        settings = GatewaySettings()
        assert settings.max_queue_size == 16

    def test_frame_max_age_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_FRAME_MAX_AGE_S", "1.0")
        settings = GatewaySettings()
        assert settings.frame_max_age_s == 1.0


# ---------------------------------------------------------------------------
# .env file loading
# ---------------------------------------------------------------------------

class TestDotEnvLoading:
    def test_load_from_env_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".env",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write("GATEWAY_ROBOT_ID=go2-from-file\n")
            f.write("GATEWAY_PORT=7777\n")
            f.write("GATEWAY_LOG_LEVEL=WARNING\n")
            env_path = f.name

        try:
            settings = GatewaySettings(_env_file=env_path)  # type: ignore[call-arg]
            assert settings.robot_id == "go2-from-file"
            assert settings.port == 7777
            assert settings.log_level == "WARNING"
        finally:
            os.unlink(env_path)

    def test_env_var_overrides_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".env",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write("GATEWAY_ROBOT_ID=go2-from-file\n")
            env_path = f.name

        monkeypatch.setenv("GATEWAY_ROBOT_ID", "go2-from-env")

        try:
            settings = GatewaySettings(_env_file=env_path)  # type: ignore[call-arg]
            # Environment variable should take precedence
            assert settings.robot_id == "go2-from-env"
        finally:
            os.unlink(env_path)


# ---------------------------------------------------------------------------
# Extra fields ignored
# ---------------------------------------------------------------------------

class TestExtraFields:
    def test_extra_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_UNKNOWN_FIELD", "should-be-ignored")
        settings = GatewaySettings()
        # Should not raise — extra fields are ignored
        assert settings.robot_id == "unknown"

    def test_non_gateway_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTHER_VAR", "ignored")
        settings = GatewaySettings()
        assert settings.robot_id == "unknown"


# ---------------------------------------------------------------------------
# Model dump
# ---------------------------------------------------------------------------

class TestModelDump:
    def test_model_dump(self) -> None:
        settings = GatewaySettings()
        data = settings.model_dump()
        assert data["robot_id"] == "unknown"
        assert data["port"] == 8780
        assert data["host"] == "0.0.0.0"
        assert data["auth_token"] is None
        assert data["robot_ip"] is None
        assert data["log_level"] == "INFO"

    def test_model_dump_with_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_ROBOT_ID", "go2-custom")
        monkeypatch.setenv("GATEWAY_PORT", "8888")
        settings = GatewaySettings()
        data = settings.model_dump()
        assert data["robot_id"] == "go2-custom"
        assert data["port"] == 8888