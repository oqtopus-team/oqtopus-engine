"""Tests for the shared Cloud ApiClient builder and auth-mode validation."""

from unittest.mock import patch

import pytest

from oqtopus_engine_core.auth import api_client as mod
from oqtopus_engine_core.auth.api_client import build_api_client, resolve_auth_mode
from oqtopus_engine_core.interfaces.oqtopus_cloud import Configuration


def _config() -> Configuration:
    config = Configuration()
    config.host = "http://localhost:8888"
    return config


def _oidc_kwargs() -> dict:
    return {
        "api_key": "k",
        "oidc_token_url": "https://idp.example/token",
        "oidc_client_id": "c",
        "oidc_client_secret": "s",
        "oidc_scope": "provider.write",
        "oidc_expiry_skew_seconds": 60,
    }


class TestResolveAuthMode:
    def test_empty_defaults_to_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTH_MODE", raising=False)
        assert resolve_auth_mode("") == "api_key"

    def test_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "oidc")
        assert resolve_auth_mode("") == "oidc"

    def test_explicit_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "oidc")
        assert resolve_auth_mode("api_key") == "api_key"

    def test_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTH_MODE", raising=False)
        assert resolve_auth_mode("OIDC") == "oidc"

    def test_unknown_explicit_value_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A typo must NOT silently downgrade to the legacy x-api-key path.
        monkeypatch.delenv("AUTH_MODE", raising=False)
        with pytest.raises(ValueError, match="invalid auth_mode"):
            resolve_auth_mode("oidcc")

    def test_unknown_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "cognito")
        with pytest.raises(ValueError, match="invalid auth_mode"):
            resolve_auth_mode("")


class TestBuildApiClient:
    def test_api_key_builds_plain_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTH_MODE", raising=False)
        with (
            patch.object(mod, "ApiClient") as api_cls,
            patch.object(mod, "BearerAuthApiClient") as bearer_cls,
        ):
            build_api_client(_config(), auth_mode="api_key", **_oidc_kwargs())
        api_cls.assert_called_once()
        bearer_cls.assert_not_called()

    def test_oidc_builds_bearer_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTH_MODE", raising=False)
        with (
            patch.object(mod, "ApiClient") as api_cls,
            patch.object(mod, "BearerAuthApiClient") as bearer_cls,
            patch.object(mod, "ClientCredentialsTokenProvider"),
        ):
            build_api_client(_config(), auth_mode="oidc", **_oidc_kwargs())
        bearer_cls.assert_called_once()
        api_cls.assert_not_called()

    def test_unknown_mode_raises_before_building(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTH_MODE", raising=False)
        with (
            patch.object(mod, "ApiClient") as api_cls,
            patch.object(mod, "BearerAuthApiClient") as bearer_cls,
        ):
            with pytest.raises(ValueError, match="invalid auth_mode"):
                build_api_client(_config(), auth_mode="oidcc", **_oidc_kwargs())
        api_cls.assert_not_called()
        bearer_cls.assert_not_called()
