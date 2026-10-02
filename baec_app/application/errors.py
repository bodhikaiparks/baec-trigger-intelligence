"""Application-layer errors.

Domain and repository errors are never wrapped or reworded by this layer;
they propagate unchanged. These classes cover only what the application
layer itself decides.
"""


class ApplicationError(Exception):
    """Base class for every application-layer error."""


class ApplicationValidationError(ApplicationError, ValueError):
    """An application object (context, payload, request) would be internally inconsistent."""


class CanonicalizationError(ApplicationValidationError):
    """A value cannot be represented in the canonical request serialization."""
