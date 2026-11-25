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
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="auth_mode is required"):
            resolve_auth_mode("")

    def test_env_var_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "oidc")
        with pytest.raises(ValueError, match="auth_mode is required"):
            resolve_auth_mode("")

    def test_explicit_value_is_used(self) -> None:
        assert resolve_auth_mode("api_key") == "api_key"
        assert resolve_auth_mode("oidc") == "oidc"

    def test_is_case_insensitive(self) -> None:
        assert resolve_auth_mode("OIDC") == "oidc"

    def test_unknown_value_raises(self) -> None:
        # A typo must NOT silently downgrade to the legacy x-api-key path.
        with pytest.raises(ValueError, match="invalid auth_mode"):
            resolve_auth_mode("oidcc")


class TestBuildApiClient:
    def test_api_key_builds_plain_client(self) -> None:
        with (
            patch.object(mod, "ApiClient") as api_cls,
            patch.object(mod, "BearerAuthApiClient") as bearer_cls,
        ):
            build_api_client(_config(), auth_mode="api_key", **_oidc_kwargs())
        api_cls.assert_called_once()
        bearer_cls.assert_not_called()

    def test_oidc_builds_bearer_client(self) -> None:
        with (
            patch.object(mod, "ApiClient") as api_cls,
            patch.object(mod, "BearerAuthApiClient") as bearer_cls,
            patch.object(mod, "ClientCredentialsTokenProvider"),
        ):
            build_api_client(_config(), auth_mode="oidc", **_oidc_kwargs())
        bearer_cls.assert_called_once()
        api_cls.assert_not_called()

    def test_unknown_mode_raises_before_building(self) -> None:
        with (
            patch.object(mod, "ApiClient") as api_cls,
            patch.object(mod, "BearerAuthApiClient") as bearer_cls,
        ):
            with pytest.raises(ValueError, match="invalid auth_mode"):
                build_api_client(_config(), auth_mode="oidcc", **_oidc_kwargs())
        api_cls.assert_not_called()
        bearer_cls.assert_not_called()
