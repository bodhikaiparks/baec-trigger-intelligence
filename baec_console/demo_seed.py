"""Synthetic demo data for BAEC Console: Alder Ridge Clinics and the Cobalt Imaging Supply scenario.

Every name, statement, notice, and figure here is fictional. The seed writes only to the synthetic workspace,
and only through the same ConsoleService methods a salesperson uses, so every engine rule applies: Engine 1
classifies and its gate records the confirmation, Engine 2 activates the plan and derives the correspondence
outcome, and Engine 3 validates every prepared draft. Seeded human actions are attributed to SEED_ACTOR, a
label that names the seed, not a real person.

Two modes:
    "beginning"  the account and the buyer interaction only; the user walks the whole workflow;
    "completed"  the whole workflow, through human acceptance of the prepared drafts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    ReviewAnswer,
    ThresholdComparator,
)
from baec_app.engine2.domain import CorrespondenceDimension, DimensionFinding, SufficiencyFinding
from baec_app.engine2.measurement import Transformation
from baec_app.engine3.enums import HumanReviewDecision, PreparationChannel, PreparationGoal

from .console_service import ATTESTATIONS, Console
from .deterministic_preparation import G_STATEMENT, G_THRESHOLD, G_TIMING, g_evidence, g_measurement
from .engine1_adapter import ConditionForm
from .engine2_adapter import ObservationForm, SourceForm

SEED_ACTOR = "Synthetic demo seed (not a real person)"
ACCOUNT = "Alder Ridge Clinics"
INDUSTRY = "Outpatient imaging (synthetic)"
SUPPLIER = "Cobalt Imaging Supply"
BUYER_ROLE = "Director of Operations"
BUYER_STATEMENT = ("If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, "
                   "we would put the contract out for review.")
SELLER_NOTES = ("Asked what would make it worth revisiting the imaging service contract. The team is not looking at "
                "other imaging service providers right now; the current Cobalt contract runs to the July renewal.")
INITIAL_NOTICE = (
    "Renewal notice for contract AR-2207: from 1 July 2027 the annual imaging service fee changes from "
    "USD 40,000 to USD 44,800."
)
CORRECTED_NOTICE = (
    "Correction to renewal notice for contract AR-2207: the previously reported amount was incorrect. "
    "From 1 July 2027 the annual imaging service fee changes from USD 40,000 to USD 43,600."
)
PARAPHRASE = "a price increase above 8% around the July renewal could make it worth revisiting the contract"


class ScriptedClock:
    """A fixed clock the seed moves forward step by step, so the seeded history is deterministic."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _at(month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def condition_form() -> ConditionForm:
    met = CriterionFinding.MET.value
    return ConditionForm(
        criteria={
            BaecCriterion.PRESENT_NON_EVALUATION.value: {
                "finding": met, "excerpt": "The team is not looking at other imaging service providers right now",
                "rationale": "Seller notes record that the buyer is not evaluating alternatives."},
            BaecCriterion.PROSPECTIVE_CONDITION.value: {
                "finding": met,
                "excerpt": "If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal",
                "rationale": "The renewal has not happened yet."},
            BaecCriterion.BUYER_ARTICULATION.value: {"finding": met, "excerpt": BUYER_STATEMENT, "rationale": ""},
            BaecCriterion.EVALUATION_LINKAGE.value: {
                "finding": met, "excerpt": "we would put the contract out for review", "rationale": ""},
        },
        origin=ArticulationOrigin.BUYER_GENERATED.value,
        elicitation=ElicitationMode.CEE_ELICITED.value,
        threshold_text="by more than 8%", comparator=ThresholdComparator.GREATER_THAN.value, threshold_value="8",
        threshold_unit="%", timing_text="at the July renewal",
    )


def load(console: Console, mode: str) -> str:
    """Reset the synthetic workspace, then seed it. Returns the synthetic account id. Local data is untouched."""
    if mode not in ("beginning", "completed"):
        raise ValueError(f"unknown seed mode {mode!r}")
    console.reset_synthetic()
    service = console.synthetic
    original_clock = service.clock
    clock = ScriptedClock(_at(9, 4, 15, 0))
    service.clock = clock
    try:
        account_id = service.create_account(ACCOUNT, industry=INDUSTRY, owner_note="Synthetic example account",
                                            actor=SEED_ACTOR, synthetic=True)
        clock.at = _at(9, 4, 15, 30)
        interaction_id = service.record_interaction(account_id, buyer_role=BUYER_ROLE, occurred_at=_at(9, 4, 14, 0),
                                                    buyer_statement=BUYER_STATEMENT, seller_notes=SELLER_NOTES,
                                                    actor=SEED_ACTOR)
        if mode == "beginning":
            return account_id
        _complete(service, clock, account_id, interaction_id)
        return account_id
    finally:
        service.clock = original_clock


def _complete(service, clock: ScriptedClock, account_id: str, interaction_id: str) -> None:
    D = CorrespondenceDimension
    clock.at = _at(9, 4, 15, 40)
    draft_id = service.capture_condition(account_id, interaction_id, condition_form(), actor=SEED_ACTOR)
    clock.at = _at(9, 4, 15, 45)
    preview = service.preview_condition(draft_id)
    baec_id = service.confirm_condition(draft_id, displayed_fingerprint=preview["fingerprint"], actor=SEED_ACTOR)

    clock.at = _at(9, 4, 15, 50)
    judgment_id = service.record_dormancy_judgment(
        baec_id,
        plausibility=ReviewAnswer.YES,
        addressability=ReviewAnswer.YES,
        notes="Synthetic demo: human judged the buyer-defined condition plausible and addressable.",
        actor=SEED_ACTOR,
    )

    clock.at = _at(9, 4, 15, 55)
    service.change_account_state(
        account_id,
        AccountState.CONDITIONALLY_DORMANT,
        {"baec_id": baec_id, "judgment_id": judgment_id},
        actor=SEED_ACTOR,
    )

    clock.at = _at(9, 4, 16, 0)
    service.start_monitoring(
        account_id, baec_id, target_entities=(f"{SUPPLIER} (synthetic)", f"{ACCOUNT} (synthetic)"),
        required=frozenset({D.ENTITY_MATCH, D.SOURCE_RELEVANCE, D.EVENT_OR_CONDITION_TYPE, D.THRESHOLD_MATCH,
                            D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}),
        sources=(SourceForm("Supplier renewal notice", f"{SUPPLIER} (synthetic)",
                            "Forwarded by the buyer under the account agreement", "Buyer forwards notices by email",
                            "Notice text and dates"),),
        actor=SEED_ACTOR)

    source_id = next(iter(service.account_view(account_id).sources))

    clock.at = _at(10, 12, 10, 45)
    initial = service.add_observation(baec_id, ObservationForm(
        source_id=source_id,
        observed_at=_at(10, 12, 10, 30),
        content=INITIAL_NOTICE,
        effective_at=datetime(2027, 7, 1, tzinfo=timezone.utc),
        source_locator="Forwarded renewal notice, 12 October 2026",
        transformation=Transformation.PERCENT_CHANGE_FROM_TWO_PRICES.value,
        baseline_value="40000",
        new_value="44800",
        value_unit="USD per year",
        flag_for_review=False,
    ), actor=SEED_ACTOR)

    clock.at = _at(10, 12, 11, 0)
    result = service.add_observation(baec_id, ObservationForm(
        source_id=source_id,
        observed_at=_at(10, 12, 10, 50),
        content=CORRECTED_NOTICE,
        effective_at=datetime(2027, 7, 1, tzinfo=timezone.utc),
        source_locator="Forwarded correction, 12 October 2026",
        corrects_observation_id=initial.observation_id,
        transformation=Transformation.PERCENT_CHANGE_FROM_TWO_PRICES.value,
        baseline_value="40000",
        new_value="43600",
        value_unit="USD per year",
    ), actor=SEED_ACTOR)

    clock.at = _at(10, 15, 9, 30)
    packet = service.review_packet(result.candidate_id)
    supported = DimensionFinding.SUPPORTED.value
    review = service.submit_correspondence_review(
        result.candidate_id, displayed_context_digest=packet.context_digest,
        findings={d: supported for d in packet.plan.required_dimensions}, notes={},
        sufficiency=SufficiencyFinding.YES.value, actor=SEED_ACTOR)

    clock.at = _at(10, 15, 9, 45)
    request_id = service.request_preparation(review.review.review_id, {
        "channel": PreparationChannel.EMAIL_AND_CALL.value,
        "goal": PreparationGoal.ASK_WHETHER_TO_REVISIT_PRIOR_DISCUSSION.value,
        "statement_use": "PARAPHRASE", "paraphrase": PARAPHRASE,
        # The Engine 3 Stage A section 14 example disclosure choices.
        "dispositions": {G_STATEMENT: "PARAPHRASE_ALLOWED", G_THRESHOLD: "DIRECT_REFERENCE_ALLOWED",
                         G_TIMING: "DIRECT_REFERENCE_ALLOWED", g_evidence(result.observation_id): "INTERNAL_ONLY",
                         g_measurement(result.measurement_id): "PARAPHRASE_ALLOWED"},
        "source_identity": {}, "adversity": {},
    }, actor=SEED_ACTOR)
    clock.at = _at(10, 15, 9, 46)
    preparation_id = service.generate_preparation(request_id, actor=SEED_ACTOR)

    clock.at = _at(10, 15, 10, 0)
    attest = {key: True for key, _ in ATTESTATIONS}
    for output in service.preparation_view(preparation_id)["preparation"]["artifacts"]:
        if output["artifact"] and output["report"]["allowed"]:
            service.review_artifact(preparation_id, output["artifact"]["artifact_id"],
                                    HumanReviewDecision.ACCEPT_AS_DRAFTED, attestations=attest, actor=SEED_ACTOR)
