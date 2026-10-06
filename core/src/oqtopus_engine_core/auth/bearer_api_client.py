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

    The bearer token is supplied by a token provider on every request, so token
    rotation/expiry is handled transparently (the generated client reads
    ``default_headers`` on each call). On an unexpected ``401`` the token is
    force-refreshed and the request is retried exactly once, which recovers from
    clock skew, key rotation, or server-side revocation.
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
        """Inject the bearer token, then delegate to the generated client.

        Retries once with a force-refreshed token if the first attempt fails
        with ``401 Unauthorized``.

        Returns:
            Whatever the generated ``ApiClient.call_api`` returns.

        Raises:
            ApiException: If the request fails (after one retry on ``401``).

        """
        self._apply_bearer(force_refresh=False)
        try:
            return super().call_api(*args, **kwargs)
        except ApiException as exc:
            if exc.status != _HTTP_UNAUTHORIZED:
                raise
            logger.info("provider API returned 401; refreshing token and retrying")
            self._apply_bearer(force_refresh=True)
            return super().call_api(*args, **kwargs)

    def _apply_bearer(self, *, force_refresh: bool) -> None:
        token = self._token_provider.get_token(force_refresh=force_refresh)
        self.set_default_header("Authorization", f"Bearer {token}")
