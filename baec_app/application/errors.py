"""Application-layer errors.

Domain and repository errors are never wrapped or reworded by this layer;
they propagate unchanged. These classes cover only what the application
layer itself decides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from baec_app.domain.baec_rules import ClassificationResult


class ApplicationError(Exception):
    """Base class for every application-layer error."""


class ApplicationValidationError(ApplicationError, ValueError):
    """An application object (context, payload, request) would be internally inconsistent."""


class CanonicalizationError(ApplicationValidationError):
    """A value cannot be represented in the canonical request serialization."""


class HumanActionError(ApplicationError):
    """The human-action context is invalid: nothing is approved, redeemed, or consumed."""


class SessionNotRecognized(HumanActionError):
    """The session is unknown, closed, or not the exact object this gate issued."""


class RequestNotRecognized(HumanActionError):
    """The request is unknown, invalidated, already redeemed, or belongs to another session."""


class RequestAlreadyRegistered(HumanActionError):
    """A request with this identifier is already registered."""


class RequestAlreadyApproved(HumanActionError):
    """The request already has its one approval."""


class DigestMismatch(HumanActionError):
    """The displayed, stored, recomputed, or approved digest does not match."""


class ApprovalNotRecognized(HumanActionError):
    """The approval is not the exact object this gate issued."""


class ApprovalAlreadyUsed(HumanActionError):
    """The approval has already been redeemed."""


class ApprovalKindMismatch(HumanActionError):
    """The approval is for a different kind of request than the one being executed."""


class ProposalNotAuthoritative(ApplicationError):
    """Something other than a gate-issued HumanApproval was presented as authority.

    An authority-boundary violation, not a failure of an otherwise legitimate
    human-action context, so it is not a HumanActionError.
    """


class NotConfirmable(ApplicationError):
    """The locked classifier did not return CONFIRMED_BAEC, so no confirmation request is opened.

    Carries the classifier's own result; the message is its canonical reason text, unchanged.
    """

    def __init__(self, result: ClassificationResult) -> None:
        super().__init__(result.reason_text)
        self.result = result


class ReferenceMismatch(ApplicationError):
    """A referenced object exists but is not related as the request claims.

    Used only for relations the locked domain cannot judge (interaction-to-account
    ownership; judgment-to-BAEC membership). Everything the domain judges stays with it.
    """
