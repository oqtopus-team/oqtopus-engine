from unittest.mock import MagicMock, patch

import pytest

from oqtopus_engine_core.auth.bearer_api_client import BearerAuthApiClient
from oqtopus_engine_core.interfaces.oqtopus_cloud import ApiClient, Configuration
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException


def _make_client(token_provider: MagicMock) -> BearerAuthApiClient:
    return BearerAuthApiClient(
        configuration=Configuration(),
        token_provider=token_provider,
    )


def test_injects_bearer_header_and_delegates():
    """A successful call sends the bearer token and returns the parent result."""
    token_provider = MagicMock()
    token_provider.get_token.return_value = "tok-1"
    client = _make_client(token_provider)

    sentinel = object()
    with patch.object(ApiClient, "call_api", return_value=sentinel) as parent:
        result = client.call_api("/devices", "GET")

    assert result is sentinel
    parent.assert_called_once()
    token_provider.get_token.assert_called_once_with(force_refresh=False)
    assert client.default_headers["Authorization"] == "Bearer tok-1"


def test_retries_once_with_refreshed_token_on_401():
    """A 401 triggers a force-refresh and exactly one retry."""
    token_provider = MagicMock()
    token_provider.get_token.side_effect = ["tok-1", "tok-2"]
    client = _make_client(token_provider)

    sentinel = object()
    with patch.object(
        ApiClient,
        "call_api",
        side_effect=[ApiException(status=401, reason="Unauthorized"), sentinel],
    ) as parent:
        result = client.call_api("/devices", "GET")

    assert result is sentinel
    assert parent.call_count == 2
    assert token_provider.get_token.call_args_list[0].kwargs == {"force_refresh": False}
    assert token_provider.get_token.call_args_list[1].kwargs == {"force_refresh": True}
    assert client.default_headers["Authorization"] == "Bearer tok-2"


def test_non_401_error_propagates_without_retry():
    """A non-401 ApiException is re-raised and not retried."""
    token_provider = MagicMock()
    token_provider.get_token.return_value = "tok-1"
    client = _make_client(token_provider)

    with patch.object(
        ApiClient,
        "call_api",
        side_effect=ApiException(status=500, reason="Server Error"),
    ) as parent:
        with pytest.raises(ApiException):
            client.call_api("/devices", "GET")

    parent.assert_called_once()
    token_provider.get_token.assert_called_once_with(force_refresh=False)
