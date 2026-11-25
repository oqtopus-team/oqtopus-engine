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


def test_update_params_for_auth_sets_token_on_request_headers_only():
    """The token goes into the per-request dict, never into default_headers."""
    token_provider = MagicMock()
    token_provider.get_token.return_value = "tok-1"
    client = _make_client(token_provider)

    headers = {"Accept": "application/json"}
    client.update_params_for_auth(headers, [], ["bearer"])

    assert headers == {"Accept": "application/json", "Authorization": "Bearer tok-1"}
    assert "Authorization" not in client.default_headers
    token_provider.get_token.assert_called_once_with()


def test_call_api_delegates_args_unchanged():
    """A successful call forwards args/kwargs untouched and returns the result."""
    client = _make_client(MagicMock())

    sentinel = object()
    with patch.object(ApiClient, "call_api", return_value=sentinel) as parent:
        result = client.call_api("/devices", "GET", None, None, {"A": "b"}, async_req=False)

    assert result is sentinel
    parent.assert_called_once_with(
        "/devices", "GET", None, None, {"A": "b"}, async_req=False
    )


def test_retries_once_after_force_refresh_on_401():
    """A 401 force-refreshes the cached token and retries exactly once."""
    token_provider = MagicMock()
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
    token_provider.get_token.assert_called_once_with(force_refresh=True)
    assert "Authorization" not in client.default_headers


def test_second_401_is_not_retried_again():
    """If the retry also fails with 401, the error propagates."""
    client = _make_client(MagicMock())

    with patch.object(
        ApiClient,
        "call_api",
        side_effect=ApiException(status=401, reason="Unauthorized"),
    ) as parent:
        with pytest.raises(ApiException):
            client.call_api("/devices", "GET")

    assert parent.call_count == 2


def test_non_401_error_propagates_without_retry():
    """A non-401 ApiException is re-raised and not retried."""
    token_provider = MagicMock()
    client = _make_client(token_provider)

    with patch.object(
        ApiClient,
        "call_api",
        side_effect=ApiException(status=500, reason="Server Error"),
    ) as parent:
        with pytest.raises(ApiException):
            client.call_api("/devices", "GET")

    parent.assert_called_once()
    token_provider.get_token.assert_not_called()


def test_end_to_end_request_carries_bearer_header():
    """The real generated __call_api sends the bearer header, retry uses new token."""
    token_provider = MagicMock()
    token_provider.get_token.side_effect = ["tok-1", "tok-2", "tok-2"]
    client = _make_client(token_provider)

    sent = []

    def fake_request(method, url, **kw):
        sent.append(kw["headers"]["Authorization"])
        if len(sent) == 1:
            raise ApiException(status=401, reason="Unauthorized")
        return MagicMock(status=200, getheaders=lambda: {}, data="{}")

    with patch.object(client, "request", side_effect=fake_request):
        client.call_api("/devices", "GET", None, None, {}, _return_http_data_only=True)

    assert sent == ["Bearer tok-1", "Bearer tok-2"]
    assert "Authorization" not in client.default_headers
