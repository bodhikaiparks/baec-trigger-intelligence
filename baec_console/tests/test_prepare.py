"""PREPARE: Engine 3 eligibility, deterministic preparation, validation, and human preparation review."""

from __future__ import annotations

import sqlite3

import pytest

from baec_app.engine3.enums import HumanReviewDecision
from baec_app.engine3.reason_codes import ReasonCode
from baec_console import engine3_adapter
from baec_console.console_service import ConsoleError, PreparationRefused
from baec_console.deterministic_preparation import GENERATOR, G_STATEMENT
from baec_console.engine3_adapter import segment_to_dict

from .conftest import ACTOR, ATTEST, monitored, observe, permitted_request, review, verified


def _outputs(console, preparation_id):
    return {o["artifact"]["artifact_type"]: o for o in
            console.local.preparation_view(preparation_id)["preparation"]["artifacts"]}


def _prepared(console, **overrides):
    account_id, baec_id, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result, **overrides), actor=ACTOR)
    return account_id, result, request_id, console.local.generate_preparation(request_id, actor=ACTOR)


def test_unverified_correspondence_cannot_request_preparation(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    from baec_app.engine2.domain import DimensionFinding, SufficiencyFinding
    recorded = review(console, result.candidate_id, DimensionFinding.UNRESOLVED, SufficiencyFinding.UNKNOWN)
    with pytest.raises(ConsoleError, match="Only human-verified correspondence"):
        console.local.request_preparation(recorded.review.review_id, permitted_request(result), actor=ACTOR)
    _, report = console.local.preparation_eligibility(recorded.review.review_id)
    assert report.issues[0].reason_code is ReasonCode.OUTCOME_NOT_HUMAN_VERIFIED  # Engine 3 agrees


def test_verified_correspondence_reaches_a_preparation_request_that_authorizes_nothing_else(console):
    account_id, baec_id, result, review_id = verified(console)
    snap, report = console.local.preparation_eligibility(review_id)
    assert report.allowed and snap["lineage_reconstructable"]
    request_id = console.local.request_preparation(review_id, permitted_request(result), actor=ACTOR)
    view = console.local.account_view(account_id)
    assert [r["request_id"] for r in view.requests] == [request_id]
    assert view.preparations == []  # a request is not a preparation
    assert view.account.state is None  # and never an account-state change
    assert view.next_action == "Generate preparation"


def test_preparation_goes_through_the_engine3_validator(console, monkeypatch):
    calls = []
    real = engine3_adapter.validate_case

    def spy(case):
        calls.append(case)
        return real(case)

    monkeypatch.setattr(engine3_adapter, "validate_case", spy)
    _, _, _, preparation_id = _prepared(console)
    artifact_cases = [c for c in calls if c.artifact is not None]
    assert {c.artifact.artifact_id for c in artifact_cases} == {"WHY-NOW", "EMAIL", "CALL", "QUESTIONS"}
    view = console.local.preparation_view(preparation_id)
    assert view["preparation"]["generator"] == GENERATOR
    assert view["status"] == "Needs review"


def test_valid_preparation_reaches_human_review(console):
    _, _, _, preparation_id = _prepared(console)
    outputs = _outputs(console, preparation_id)
    assert all(o["report"]["allowed"] for o in outputs.values())
    pending = [r for r in console.review_queue()["pending"] if r["type"] == "Draft review"]
    assert len(pending) == 4


def test_missing_disclosure_permission_is_refused_not_repaired(console):
    _, _, _, preparation_id = _prepared(console, dispositions={})  # every fact UNKNOWN: Engine 3's default
    outputs = _outputs(console, preparation_id)
    email = outputs["FOLLOW_UP_EMAIL"]
    assert not email["report"]["allowed"]
    assert email["report"]["issues"][0]["reason_code"] == ReasonCode.BUYER_STATEMENT_NOT_PERMITTED.value
    assert "When we last spoke" in email["artifact"]["rendered_text"]  # stored exactly as built
    assert outputs["INTERNAL_WHY_NOW_SUMMARY"]["report"]["allowed"]  # internal output may use internal grounding
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST, actor=ACTOR)


def test_quote_where_only_paraphrase_is_permitted_is_refused(console):
    _, _, _, preparation_id = _prepared(console, statement_use="QUOTE")
    email = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]
    assert email["report"]["issues"][0]["reason_code"] == ReasonCode.DIRECT_QUOTE_EXCEEDS_PARAPHRASE_PERMISSION.value


def test_internal_only_evidence_never_reaches_buyer_facing_outputs(console):
    account_id, baec_id, result, review_id = verified(console)
    request = permitted_request(result)
    request["dispositions"] = {**request["dispositions"],
                               f"G-MEASUREMENT-{result.measurement_id}": "INTERNAL_ONLY"}
    request_id = console.local.request_preparation(review_id, request, actor=ACTOR)
    outputs = _outputs(console, console.local.generate_preparation(request_id, actor=ACTOR))
    assert outputs["FOLLOW_UP_EMAIL"]["report"]["issues"][0]["reason_code"] == ReasonCode.FACT_INTERNAL_ONLY.value


def test_accepted_draft_is_prepared_for_human_use_only(console):
    account_id, _, _, preparation_id = _prepared(console)
    for artifact_id in ("WHY-NOW", "EMAIL", "CALL", "QUESTIONS"):
        console.local.review_artifact(preparation_id, artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST, actor=ACTOR)
    view = console.local.account_view(account_id)
    assert view.stage == "PREPARED"
    assert view.account.state is None  # preparation never changes account state
    assert console.local.preparation_view(preparation_id)["status"] == "Accepted"


def test_acceptance_requires_every_attestation(console):
    _, _, _, preparation_id = _prepared(console)
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations={**ATTEST, "permitted": False}, actor=ACTOR)


def test_rejected_draft_stays_rejected(console):
    _, _, _, preparation_id = _prepared(console)
    console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.REJECT, attestations={},
                                  reason="Not the right moment", actor=ACTOR)
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST, actor=ACTOR)
    assert console.local.preparation_view(preparation_id)["reviews"]["EMAIL"]["decision"] == "REJECT"


def test_edit_preserves_the_original_and_is_validated_again(console):
    _, _, _, preparation_id = _prepared(console)
    original = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]["artifact"]
    segments = [dict(s) for s in original["segments"]]
    segments[-1] = {**segments[-1], "text": "Would a short call next week be useful?"}
    edit = console.local.edit_artifact(preparation_id, "EMAIL", segments, actor=ACTOR)
    assert edit["report"]["allowed"]
    assert edit["artifact"]["artifact_id"] != "EMAIL"
    console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.EDIT, attestations=ATTEST,
                                  edit_id=edit["edit_id"], actor=ACTOR)
    after = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]["artifact"]
    assert after == original  # the generated draft is unchanged
    view = console.local.preparation_view(preparation_id)
    assert view["edits"][0]["artifact"]["artifact"]["segments"][-1]["text"] == "Would a short call next week be useful?"
    assert view["reviews"]["EMAIL"]["edit_id"] == edit["edit_id"]


def test_ungrounded_human_edit_is_refused(console):
    _, _, _, preparation_id = _prepared(console)
    original = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]["artifact"]
    from baec_app.engine3.enums import ClaimSemanticClass, ClaimType
    from baec_app.engine3.models import ClaimField, DraftSegment
    added = segment_to_dict(DraftSegment("H1", "We can guarantee a 10% saving at renewal.",
                                         ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.SELLER_CAPABILITY,
                                         claim_field=ClaimField.SELLER_CAPABILITY))
    edit = console.local.edit_artifact(preparation_id, "EMAIL", original["segments"] + [added], actor=ACTOR)
    assert not edit["report"]["allowed"]
    assert edit["report"]["issues"][0]["reason_code"] == ReasonCode.HUMAN_EDIT_UNGROUNDED.value
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.EDIT, attestations=ATTEST,
                                      edit_id=edit["edit_id"], actor=ACTOR)


def test_corrected_evidence_makes_the_request_stale(console):
    account_id, baec_id, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result), actor=ACTOR)
    observe(console, account_id, baec_id, new_price="41000", content="Correction: the fee changes to USD 41,000.",
            corrects=result.observation_id, flag=False)
    with pytest.raises(PreparationRefused) as refused:
        console.local.generate_preparation(request_id, actor=ACTOR)
    codes = {i.reason_code for i in refused.value.report.issues}
    assert ReasonCode.EVIDENCE_CONTEXT_CHANGED in codes  # Engine 3 refuses (context first); nothing generated
    assert console.local.account_view(account_id).preparations == []
    assert "Preparation refused by Engine 3" in " ".join(i.title for i in console.local.timeline(account_id))
    _, report = console.local.preparation_eligibility(review_id)
    assert ReasonCode.REQUIRED_EVIDENCE_NOT_USABLE in {i.reason_code for i in report.issues}


def test_a_later_review_is_reported_not_treated_as_superseding(console):
    """Engine 2 records no review supersession, so recency is never used; the later review is shown to the human."""
    from baec_app.engine2.domain import DimensionFinding, SufficiencyFinding
    account_id, baec_id, result, review_id = verified(console)
    later = review(console, result.candidate_id, DimensionFinding.UNRESOLVED, SufficiencyFinding.NO)
    snap, report = console.local.preparation_eligibility(review_id)
    assert report.allowed
    assert snap["later_reviews"] == [{"review_id": later.review.review_id, "outcome": "POSSIBLE_CORRESPONDENCE"}]
    (entry,) = console.local.account_view(account_id).verified_correspondences()
    assert [r.review_id for r, _ in entry["later_reviews"]] == [later.review.review_id]


def test_paraphrase_is_required_when_paraphrasing(console):
    account_id, baec_id, result, review_id = verified(console)
    with pytest.raises(ConsoleError):
        console.local.request_preparation(review_id, permitted_request(result, paraphrase=" "), actor=ACTOR)


def test_preparation_records_are_append_only(console):
    _, _, _, preparation_id = _prepared(console)
    connection = sqlite3.connect(console.local.console_path)
    try:
        for sql in ("UPDATE preparations SET generator = 'x'", "DELETE FROM preparations",
                    "UPDATE preparation_requests SET context_digest = 'x'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
    finally:
        connection.close()


def test_buyer_statement_grounding_is_marked_as_buyer_statement(console):
    account_id, baec_id, result, review_id = verified(console)
    snap = console.local.preparation_snapshot(review_id)
    items = {g.grounding_item_id: g for g in engine3_adapter.grounding_items(snap, permitted_request(result))}
    assert items[G_STATEMENT].is_buyer_statement and items[G_STATEMENT].exact_value == snap["buyer_statement"]
    assert items["G-CORRESPONDENCE"].disposition.value == "INTERNAL_ONLY"
