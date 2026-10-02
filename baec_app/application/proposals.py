"""Proposal origins and proposal objects.

Phase 4 has exactly two origins. AI_MODEL is deliberately absent: model or
MCP proposal ingestion may not be enabled until persistent AI-origin
provenance is designed and implemented (design §18).

A proposal grants nothing. It reaches the command side only when the UI
turns it into a request (HumanCommandFacade.request_from_proposal), and that
request still needs an explicit human approval before any authoritative
write. Every field is validated by exact type, so no field can hold an
authorization, approval, request, mapping, or flag. There is no parser from
mappings or other external input in Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from baec_app.application.errors import ApplicationValidationError
from baec_app.domain.enums import NoPlausiblePathGround, ReviewAnswer
from baec_app.domain.models import BaecCandidate, EvaluationEvidence, NonEvaluationEvidence


class ProposalOrigin(Enum):
    HUMAN_DRAFT = "HUMAN_DRAFT"  # values entered by a human through the UI
    DETERMINISTIC = "DETERMINISTIC"  # produced by deterministic application code


def _text(value: object, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise ApplicationValidationError(f"{field} must be a non-blank string")


def _optional_text(value: object, field: str) -> None:
    if value is not None and type(value) is not str:
        raise ApplicationValidationError(f"{field} must be a string or None")


def _exact(value: object, expected: type, field: str) -> None:
    if type(value) is not expected:
        raise ApplicationValidationError(f"{field} must be exactly {expected.__name__}")


def _optional_exact(value: object, expected: type, field: str) -> None:
    if value is not None and type(value) is not expected:
        raise ApplicationValidationError(f"{field} must be exactly {expected.__name__} or None")


def _aware(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationValidationError(f"{field} must be a timezone-aware datetime")


@dataclass(frozen=True)
class ConfirmationProposal:
    candidate: BaecCandidate
    captured_at: datetime
    origin: ProposalOrigin

    def __post_init__(self) -> None:
        _exact(self.candidate, BaecCandidate, "ConfirmationProposal.candidate")
        _aware(self.captured_at, "ConfirmationProposal.captured_at")
        _exact(self.origin, ProposalOrigin, "ConfirmationProposal.origin")


@dataclass(frozen=True)
class DormancyJudgmentProposal:
    baec_id: str
    plausibility: ReviewAnswer
    addressability: ReviewAnswer
    notes: str | None
    origin: ProposalOrigin

    def __post_init__(self) -> None:
        _text(self.baec_id, "DormancyJudgmentProposal.baec_id")
        _exact(self.plausibility, ReviewAnswer, "DormancyJudgmentProposal.plausibility")
        _exact(self.addressability, ReviewAnswer, "DormancyJudgmentProposal.addressability")
        _optional_text(self.notes, "DormancyJudgmentProposal.notes")
        _exact(self.origin, ProposalOrigin, "DormancyJudgmentProposal.origin")


@dataclass(frozen=True)
class MoveToDormantProposal:
    account_id: str
    baec_id: str
    judgment_id: int
    non_evaluation_evidence: NonEvaluationEvidence | None
    origin: ProposalOrigin

    def __post_init__(self) -> None:
        _text(self.account_id, "MoveToDormantProposal.account_id")
        _text(self.baec_id, "MoveToDormantProposal.baec_id")
        _exact(self.judgment_id, int, "MoveToDormantProposal.judgment_id")
        _optional_exact(self.non_evaluation_evidence, NonEvaluationEvidence, "MoveToDormantProposal.non_evaluation_evidence")
        _exact(self.origin, ProposalOrigin, "MoveToDormantProposal.origin")


@dataclass(frozen=True)
class MoveToActiveProposal:
    account_id: str
    evaluation_evidence: EvaluationEvidence
    origin: ProposalOrigin

    def __post_init__(self) -> None:
        _text(self.account_id, "MoveToActiveProposal.account_id")
        _exact(self.evaluation_evidence, EvaluationEvidence, "MoveToActiveProposal.evaluation_evidence")
        _exact(self.origin, ProposalOrigin, "MoveToActiveProposal.origin")


@dataclass(frozen=True)
class MoveToNoPlausiblePathProposal:
    account_id: str
    ground: NoPlausiblePathGround
    reason: str
    non_evaluation_evidence: NonEvaluationEvidence | None
    basis_interaction_id: str | None
    origin: ProposalOrigin

    def __post_init__(self) -> None:
        _text(self.account_id, "MoveToNoPlausiblePathProposal.account_id")
        _exact(self.ground, NoPlausiblePathGround, "MoveToNoPlausiblePathProposal.ground")
        # Only the type: whether the reason is acceptable is the locked state machine's call.
        _exact(self.reason, str, "MoveToNoPlausiblePathProposal.reason")
        _optional_exact(
            self.non_evaluation_evidence, NonEvaluationEvidence, "MoveToNoPlausiblePathProposal.non_evaluation_evidence"
        )
        _optional_text(self.basis_interaction_id, "MoveToNoPlausiblePathProposal.basis_interaction_id")
        _exact(self.origin, ProposalOrigin, "MoveToNoPlausiblePathProposal.origin")


PROPOSAL_TYPES = (
    ConfirmationProposal,
    DormancyJudgmentProposal,
    MoveToDormantProposal,
    MoveToActiveProposal,
    MoveToNoPlausiblePathProposal,
)
