"""The single conversion boundary between MCP wire contracts and application/domain types.

Every conversion is explicit and field by field, dispatched by exact type. No
generic serializer, reflection, duck typing, or class-name matching is used.
No BAEC, provenance, staleness, or transition rule is evaluated here: values
are only translated.

5B implements the read-side output conversions and the wire-to-domain helpers
for enums and timestamps. The preview-tool input conversions arrive in 5C.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from baec_app.application import PersistedDormancyJudgment, SourceInteraction, TransitionHistoryEntry
from baec_app.domain.models import (
    Account,
    AiDerivedText,
    BaecCandidate,
    BaecRecord,
    CriterionAssessment,
    EvaluationEvidence,
    EvidenceExcerpt,
    HumanAuthorization,
    NonEvaluationEvidence,
    StringencyExpression,
)
from baec_app.mcp.contracts import (
    AccountList,
    AccountView,
    AiDerivedTextView,
    AuthorizationView,
    BaecCandidateView,
    BaecRecordList,
    BaecRecordView,
    CriterionAssessmentView,
    DormancyJudgmentList,
    DormancyJudgmentView,
    EvidenceExcerptView,
    InteractionList,
    InteractionView,
    StateEvidenceView,
    StringencyView,
    TransitionHistoryEntryView,
    TransitionHistoryList,
)


class AdapterTypeError(TypeError):
    """An adapter received a value of a type it does not convert (an internal error)."""


def _require(value: object, expected: type) -> None:
    if type(value) is not expected:
        raise AdapterTypeError(f"expected exactly {expected.__name__}, got {type(value).__name__}")


# --- wire → domain helpers ------------------------------------------------------------


def enum_from_wire(enum_class: type[Enum], value: str) -> Enum:
    """A validated wire string to the exact domain enum member (by value)."""
    if type(value) is not str:
        raise AdapterTypeError("enum wire values are strings")
    return enum_class(value)


def datetime_from_wire(value: str) -> datetime:
    """A validated wire timestamp (explicit offset) to an aware datetime."""
    if type(value) is not str:
        raise AdapterTypeError("timestamps are strings on the wire")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterTypeError("wire timestamps must be timezone-aware")
    return parsed


# --- domain → wire scalars ---------------------------------------------------------


def _enum(value: Enum | None) -> str | None:
    return None if value is None else value.value


def _timestamp(value: datetime) -> str:
    _require(value, datetime)
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdapterTypeError("stored timestamps are timezone-aware")
    return value.isoformat()


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    _require(value, Decimal)
    return str(value)


# --- domain → wire views ------------------------------------------------------------


def account_view(account: Account) -> AccountView:
    _require(account, Account)
    return AccountView(account_id=account.account_id, name=account.name, state=_enum(account.state))


def account_list(accounts: tuple[Account, ...]) -> AccountList:
    return AccountList(accounts=[account_view(a) for a in accounts])


def interaction_view(interaction: SourceInteraction) -> InteractionView:
    _require(interaction, SourceInteraction)
    return InteractionView(
        interaction_id=interaction.interaction_id,
        account_id=interaction.account_id,
        occurred_at=_timestamp(interaction.occurred_at),
        text=interaction.text,
    )


def interaction_list(interactions: tuple[SourceInteraction, ...]) -> InteractionList:
    return InteractionList(interactions=[interaction_view(i) for i in interactions])


def evidence_view(excerpt: EvidenceExcerpt) -> EvidenceExcerptView:
    _require(excerpt, EvidenceExcerpt)
    return EvidenceExcerptView(text=excerpt.text, provenance=_enum(excerpt.provenance), source_id=excerpt.source_id)


def _assessment_view(assessment: CriterionAssessment) -> CriterionAssessmentView:
    _require(assessment, CriterionAssessment)
    return CriterionAssessmentView(
        criterion=_enum(assessment.criterion),
        finding=_enum(assessment.finding),
        evidence=[evidence_view(e) for e in assessment.evidence],
        rationale=assessment.rationale,
    )


def _stringency_view(stringency: StringencyExpression | None) -> StringencyView | None:
    if stringency is None:
        return None
    _require(stringency, StringencyExpression)
    return StringencyView(
        verbatim_text=stringency.verbatim_text,
        comparator=_enum(stringency.comparator),
        numeric_value=_decimal(stringency.numeric_value),
        unit=stringency.unit,
        qualitative_term=stringency.qualitative_term,
        recurrence_text=stringency.recurrence_text,
        timing_text=stringency.timing_text,
    )


def _candidate_view(candidate: BaecCandidate) -> BaecCandidateView:
    _require(candidate, BaecCandidate)
    return BaecCandidateView(
        account_id=candidate.account_id,
        source_interaction_id=candidate.source_interaction_id,
        source_excerpt=evidence_view(candidate.source_excerpt),
        assessments=[_assessment_view(a) for a in candidate.assessments],
        articulation_origin=_enum(candidate.articulation_origin),
        elicitation_mode=_enum(candidate.elicitation_mode),
        buyer_exact_statement=candidate.buyer_exact_statement,
        buyer_role=candidate.buyer_role,
        stringency=_stringency_view(candidate.stringency),
    )


def _authorization_view(authorization: HumanAuthorization | None) -> AuthorizationView | None:
    if authorization is None:
        return None
    _require(authorization, HumanAuthorization)
    return AuthorizationView(
        authorized_by=authorization.authorized_by,
        authorized_at=_timestamp(authorization.authorized_at),
        action=_enum(authorization.action),
        subject_id=authorization.subject_id,
        target_state=_enum(authorization.target_state),
    )


def _ai_derived_view(text: AiDerivedText | None) -> AiDerivedTextView | None:
    if text is None:
        return None
    _require(text, AiDerivedText)
    return AiDerivedTextView(text=text.text, generated_at=_timestamp(text.generated_at), model=text.model)


def baec_record_view(record: BaecRecord) -> BaecRecordView:
    _require(record, BaecRecord)
    return BaecRecordView(
        baec_id=record.baec_id,
        captured_at=_timestamp(record.captured_at),
        candidate=_candidate_view(record.candidate),
        classification=_enum(record.classification),
        classification_reason=record.classification_reason,
        staleness_status=_enum(record.staleness_status),
        confirmation=_authorization_view(record.confirmation),
        ai_derived_normalized_condition=_ai_derived_view(record.normalized_condition),
    )


def baec_record_list(records: list[BaecRecord]) -> BaecRecordList:
    return BaecRecordList(baec_records=[baec_record_view(r) for r in records])


def dormancy_judgment_view(persisted: PersistedDormancyJudgment) -> DormancyJudgmentView:
    _require(persisted, PersistedDormancyJudgment)
    judgment = persisted.judgment
    return DormancyJudgmentView(
        judgment_id=persisted.judgment_id,
        baec_id=judgment.baec_id,
        plausibility=_enum(judgment.plausibility),
        addressability=_enum(judgment.addressability),
        notes=judgment.notes,
        authorization=_authorization_view(judgment.authorization),
    )


def dormancy_judgment_list(judgments: tuple[PersistedDormancyJudgment, ...]) -> DormancyJudgmentList:
    return DormancyJudgmentList(dormancy_judgments=[dormancy_judgment_view(j) for j in judgments])


def _state_evidence_view(evidence: EvaluationEvidence | NonEvaluationEvidence | None, expected: type) -> StateEvidenceView | None:
    if evidence is None:
        return None
    _require(evidence, expected)
    return StateEvidenceView(
        account_id=evidence.account_id,
        evidence=evidence_view(evidence.evidence),
        observed_at=_timestamp(evidence.observed_at),
    )


def transition_entry_view(entry: TransitionHistoryEntry) -> TransitionHistoryEntryView:
    _require(entry, TransitionHistoryEntry)
    return TransitionHistoryEntryView(
        transition_id=entry.transition_id,
        account_id=entry.account_id,
        from_state=_enum(entry.from_state),
        to_state=_enum(entry.to_state),
        authorization=_authorization_view(entry.authorization),
        recorded_at=_timestamp(entry.recorded_at),
        baec_id=entry.baec_id,
        judgment_id=entry.judgment_id,
        evaluation_evidence=_state_evidence_view(entry.evaluation_evidence, EvaluationEvidence),
        non_evaluation_evidence=_state_evidence_view(entry.non_evaluation_evidence, NonEvaluationEvidence),
        ground=_enum(entry.ground),
        reason=entry.reason,
        basis_interaction_id=entry.basis_interaction_id,
        unresolved=[_enum(kind) for kind in entry.unresolved],
    )


def transition_history_list(entries: tuple[TransitionHistoryEntry, ...]) -> TransitionHistoryList:
    return TransitionHistoryList(transitions=[transition_entry_view(e) for e in entries])
