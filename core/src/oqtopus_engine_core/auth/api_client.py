"""Build an authenticated generated ApiClient for a Cloud repository.

Shared by the Job, Device, and SLURM execution Cloud repositories so the three
clients cannot drift in how they authenticate. ``auth_mode`` selects between a
static ``x-api-key`` header and an OIDC (OAuth2 client-credentials) bearer token.
"""

from __future__ import annotations

from oqtopus_auth.client import ClientCredentialsTokenProvider

from oqtopus_engine_core.auth.bearer_api_client import BearerAuthApiClient
from oqtopus_engine_core.interfaces.oqtopus_cloud import ApiClient, Configuration

_VALID_AUTH_MODES = ("api_key", "oidc")


def resolve_auth_mode(auth_mode: str) -> str:
    """Normalize and validate the auth mode.

    ``auth_mode`` is required. An empty value, or any value other than
    ``"api_key"`` / ``"oidc"``, is rejected so that a missing value or a typo
    (e.g. ``"oidcc"``) raises an error instead of silently selecting an
    authentication method.

    Args:
        auth_mode: ``"api_key"`` or ``"oidc"`` (case-insensitive).

    Returns:
        The normalized mode, one of ``_VALID_AUTH_MODES``.

    Raises:
        ValueError: If ``auth_mode`` is empty or not a supported value.

    """
    mode = (auth_mode or "").strip().lower()
    if not mode:
        message = f"auth_mode is required; expected one of {_VALID_AUTH_MODES}"
        raise ValueError(message)
    if mode not in _VALID_AUTH_MODES:
        message = f"invalid auth_mode {mode!r}; expected one of {_VALID_AUTH_MODES}"
        raise ValueError(message)
    return mode


def build_api_client(  # noqa: PLR0913
    rest_config: Configuration,
    *,
    api_key: str,
    auth_mode: str,
    oidc_token_url: str,
    oidc_client_id: str,
    oidc_client_secret: str,
    oidc_scope: str,
    oidc_expiry_skew_seconds: int,
) -> ApiClient:
    """Build the generated ApiClient for the given auth mode.

    Args:
        rest_config: Passed through to the generated client.
        api_key: Static API key for ``auth_mode="api_key"``.
        auth_mode: ``"api_key"`` or ``"oidc"`` (see :func:`resolve_auth_mode`).
        oidc_token_url: IdP token endpoint for ``auth_mode="oidc"``.
        oidc_client_id: Confidential client id for ``auth_mode="oidc"``.
        oidc_client_secret: Confidential client secret for ``auth_mode="oidc"``.
        oidc_scope: Space-delimited scopes to request.
        oidc_expiry_skew_seconds: Refresh the token this many seconds early.

    Returns:
        A ``BearerAuthApiClient`` for ``oidc`` mode, else a plain ``ApiClient``
        carrying the static ``x-api-key`` header. ``resolve_auth_mode`` raises
        ``ValueError`` if ``auth_mode`` is empty or not a supported value.

    """
    mode = resolve_auth_mode(auth_mode)
    if mode == "oidc":
        token_provider = ClientCredentialsTokenProvider(
            token_url=oidc_token_url,
            client_id=oidc_client_id,
            client_secret=oidc_client_secret,
            scope=oidc_scope,
            expiry_skew_seconds=oidc_expiry_skew_seconds,
        )
        return BearerAuthApiClient(
            configuration=rest_config,
            token_provider=token_provider,
        )
    return ApiClient(
        configuration=rest_config,
        header_name="x-api-key",
        header_value=api_key,
    )
