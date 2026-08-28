"""Sanitized exception hierarchy for the Tigo Energy cloud client.

Exceptions deliberately contain an endpoint *label*, never a complete URL,
query string, response body, credential, token, or CCA identifier.  This makes
them safe for Home Assistant's logs and downloadable diagnostics.
"""

from __future__ import annotations


class TigoError(Exception):
    """Base class for errors raised by the integration."""


class TigoAPIError(TigoError):
    """A Tigo endpoint returned an unexpected response."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status = status


class TigoAuthenticationError(TigoAPIError):
    """Authentication failed or a bearer token was rejected."""


class TigoConnectionError(TigoAPIError):
    """The cloud service could not be reached."""


class TigoDataError(TigoAPIError):
    """The cloud service returned a malformed or unsupported payload."""


class TigoRetryableError(TigoAPIError):
    """A transient service failure that should be retried later."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, endpoint=endpoint, status=status)
        self.retry_after = retry_after


class TigoRateLimitError(TigoRetryableError):
    """Tigo asked the client to slow down (normally HTTP 429)."""


class TigoServiceUnavailableError(TigoRetryableError):
    """Tigo's service is temporarily unavailable."""


class TigoFeatureUnavailableError(TigoAPIError):
    """An optional endpoint is unavailable for this system/account tier."""


# Concise aliases used by a few Home Assistant integration conventions.  They
# are aliases, rather than parallel classes, so exception matching is exact.
TigoApiError = TigoAPIError
TigoAuthError = TigoAuthenticationError
TigoThrottleError = TigoRateLimitError


__all__ = [
    "TigoAPIError",
    "TigoApiError",
    "TigoAuthError",
    "TigoAuthenticationError",
    "TigoConnectionError",
    "TigoDataError",
    "TigoError",
    "TigoFeatureUnavailableError",
    "TigoRateLimitError",
    "TigoRetryableError",
    "TigoServiceUnavailableError",
    "TigoThrottleError",
]
