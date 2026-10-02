"""The only place in the application layer that constructs HumanAuthorization.

Every field comes from a redeemed request and the approval the gate issued
for it: who and when from the approval, what from the stored request. A
caller cannot supply or override any of them.

The resulting HumanAuthorization still expresses a domain requirement, not
proof of identity: authorized_by is the session's self-asserted actor.
"""

from __future__ import annotations

from baec_app.application.approval import HumanApproval
from baec_app.application.errors import ApplicationValidationError, RequestNotRecognized
from baec_app.application.requests import ApprovalRequest
from baec_app.domain.models import HumanAuthorization


def authorize(request: ApprovalRequest, approval: HumanApproval) -> HumanAuthorization:
    """Build the authorization for a request that has just been redeemed with this approval."""
    if type(request) is not ApprovalRequest:
        raise ApplicationValidationError("authorize requires the redeemed ApprovalRequest")
    if type(approval) is not HumanApproval:
        raise ApplicationValidationError("authorize requires the HumanApproval that was redeemed")
    if approval.request_id != request.request_id or approval.digest != request.digest:
        raise RequestNotRecognized("the approval was not issued for this request")
    # Session binding is enforced by the gate registry, not the content digest;
    # keep it when turning the redeemed request into a domain authorization.
    if approval.session_id != request.session_id:
        raise RequestNotRecognized("the approval was issued in a different session from the request")
    return HumanAuthorization(
        authorized_by=approval.actor_id,
        authorized_at=approval.approved_at,
        action=request.action,
        subject_id=request.subject_id,
        target_state=request.target_state,
    )
