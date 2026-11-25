"""An ApiClient variant that authenticates with an OIDC bearer token."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from oqtopus_engine_core.interfaces.oqtopus_cloud import ApiClient
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException

if TYPE_CHECKING:
    from oqtopus_auth.client import ClientCredentialsTokenProvider

    from oqtopus_engine_core.interfaces.oqtopus_cloud import Configuration

logger = logging.getLogger(__name__)

_HTTP_UNAUTHORIZED = 401


class BearerAuthApiClient(ApiClient):
    """Generated ApiClient that injects a fresh ``Authorization: Bearer`` header.

    The bearer token is read from the token provider for every request and set on
    that request's own header dict (via ``update_params_for_auth``), never on the
    shared ``default_headers``. Concurrent calls and 401 retries therefore share no
    mutable state. On an unexpected ``401`` the cached token is force-refreshed and
    the request is retried exactly once, which recovers from clock skew, key
    rotation, or server-side revocation.
    """

    def __init__(
        self,
        configuration: Configuration,
        token_provider: ClientCredentialsTokenProvider,
    ) -> None:
        """Initialize the client.

        Args:
            configuration: The generated-client configuration (host, proxy, ...).
            token_provider: Supplies valid bearer access tokens.

        """
        super().__init__(configuration=configuration)
        self._token_provider = token_provider

    def call_api(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Delegate to the generated client, retrying once on ``401``.

        On ``401 Unauthorized`` the provider's cached token is force-refreshed and
        the request is sent again; the retry picks up the refreshed token in
        ``update_params_for_auth``.

        Returns:
            Whatever the generated ``ApiClient.call_api`` returns.

        Raises:
            ApiException: If the request fails (after one retry on ``401``).

        """
        try:
            return super().call_api(*args, **kwargs)
        except ApiException as exc:
            if exc.status != _HTTP_UNAUTHORIZED:
                raise
            logger.info("provider API returned 401; refreshing token and retrying")
            self._token_provider.get_token(force_refresh=True)
            return super().call_api(*args, **kwargs)

    def update_params_for_auth(
        self,
        headers: dict[str, Any],
        querys: list[tuple[str, Any]],  # noqa: ARG002
        auth_settings: list[str] | None,  # noqa: ARG002
    ) -> None:
        """Set the bearer token on this request's own ``headers`` dict."""
        token = self._token_provider.get_token()
        headers["Authorization"] = f"Bearer {token}"
