"""Safe, credential-free exceptions for Nugen API failures."""


class NugenConfigurationError(ValueError):
    """Raised when a required local Nugen setting is missing or invalid."""


class NugenAPIError(RuntimeError):
    """Base error that deliberately excludes request headers and response bodies."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class NugenAuthenticationError(NugenAPIError):
    """The API key was rejected or lacks permission for the requested resource."""


class NugenRateLimitError(NugenAPIError):
    """Nugen refused the request due to a rate limit."""


class NugenNotFoundError(NugenAPIError):
    """The requested Nugen resource was not found for this account."""


class NugenRequestError(NugenAPIError):
    """Nugen rejected a request with a client-side HTTP status."""


class NugenServerError(NugenAPIError):
    """Nugen returned a server-side HTTP error."""


class NugenTimeoutError(NugenAPIError):
    """The Nugen request or gateway timed out."""


class NugenTransportError(NugenAPIError):
    """A network or TLS failure prevented a Nugen request from completing."""


class NugenProtocolError(NugenAPIError):
    """Nugen returned a response that does not match the documented schema."""
