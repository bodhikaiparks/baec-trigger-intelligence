"""Workspace behavior: timeline, queue, dashboard, persistence, and the synthetic workspace."""

from __future__ import annotations

from baec_app.engine3.enums import HumanReviewDecision
from baec_console import demo_seed
from baec_console.console_service import Console

from .conftest import ACTOR, ATTEST, StepClock, new_account, permitted_request, verified


def test_end_to_end_workflow_and_timeline(console):
    """create account -> interaction -> confirm -> monitor -> evidence -> review -> request -> generate ->
    validate -> human accept -> timeline."""
    account_id, baec_id, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result), actor=ACTOR)
    preparation_id = console.local.generate_preparation(request_id, actor=ACTOR)
    view = console.local.preparation_view(preparation_id)
    assert all(o["report"]["allowed"] for o in view["preparation"]["artifacts"])
    assert view["request"]["review_id"] == review_id  # explicitly selected correspondence
    assert view["provenance"]["bound_review_id"] == review_id
    assert view["provenance"]["console_generator_origin"] == "DETERMINISTIC_CONSOLE_TEMPLATE_V1"
    console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED, attestations=ATTEST,
                                  actor=ACTOR)
    titles = [i.title for i in console.local.timeline(account_id)]
    expected = ["Account created", "Buyer interaction recorded", "Condition captured (not yet confirmed)",
                "Buyer-articulated condition confirmed", "Monitoring plan authorized", "Monitoring activated",
                "New observation added", "Flagged for correspondence review", "Correspondence review recorded",
                "Engine 2 outcome: Human-verified correspondence",
                "Preparation requested from the correspondence verified 4 September 2026 "
                "(drafting only; not outreach authorization)",
                "Preparation generated; 4 of 4 outputs passed Engine 3 validation", "Draft accepted by human: Email draft"]
    positions = [titles.index(t) for t in expected]
    assert positions == sorted(positions)
    kinds = {i.title: i.actor_kind for i in console.local.timeline(account_id)}
    assert kinds["Monitoring activated"] == "Engine 2"
    assert kinds["Buyer-articulated condition confirmed"] == "Human"
    assert kinds["Preparation generated; 4 of 4 outputs passed Engine 3 validation"] == "Engine 3"
    remaining = [r for r in console.review_queue()["pending"] if r["type"] == "Draft review"]
    assert len(remaining) == 3  # the other drafts still wait for a human


def test_dashboard_counts_come_from_records(console):
    assert console.dashboard() == {"accounts": 0, "verified": 0, "monitoring": 0, "awaiting": 0, "ready": 0}
    verified(console)
    new_account(console, "Second account")
    assert console.dashboard() == {"accounts": 2, "verified": 1, "monitoring": 1, "awaiting": 0, "ready": 1}


def test_persistence_survives_a_fresh_console_instance(tmp_path):
    first = Console(tmp_path / "data", StepClock())
    account_id, baec_id, result, review_id = verified(first)
    request_id = first.local.request_preparation(review_id, permitted_request(result), actor=ACTOR)
    first.local.generate_preparation(request_id, actor=ACTOR)
    del first
    second = Console(tmp_path / "data", StepClock())
    view = second.local.account_view(account_id)
    assert view.current_baec.baec_id == baec_id
    assert view.plan.activation_result == "ACTIVATED"
    assert len(view.preparations) == 1 and view.next_action == "Review prepared drafts"


def test_synthetic_reset_never_touches_local_accounts(console):
    local_id = new_account(console, "My real-looking account")
    synthetic_id = demo_seed.load(console, "completed")
    assert {local_id, synthetic_id} <= {v.account.account_id for v in console.account_views()}
    console.reset_synthetic()
    ids = {v.account.account_id for v in console.account_views()}
    assert local_id in ids and synthetic_id not in ids
    assert console.local.account_view(local_id).display_name == "My real-looking account"


def test_synthetic_seed_is_marked_synthetic_and_reaches_prepared(console):
    account_id = demo_seed.load(console, "completed")
    view = console.synthetic.account_view(account_id)
    assert view.synthetic and view.account.name.endswith("(synthetic)")
    assert view.stage == "PREPARED"
    assert {i.actor for i in console.synthetic.timeline(account_id) if i.actor_kind == "Human"} == {demo_seed.SEED_ACTOR}


def test_start_from_beginning_seeds_only_account_and_interaction(console):
    account_id = demo_seed.load(console, "beginning")
    view = console.synthetic.account_view(account_id)
    assert len(view.interactions) == 1 and view.drafts == [] and view.baecs == []
    assert view.next_action == "Capture the buyer's condition"


def test_reloading_the_seed_replaces_previous_synthetic_data(console):
    demo_seed.load(console, "completed")
    demo_seed.load(console, "beginning")
    assert len(console.synthetic.account_ids()) == 1


def test_actor_preference_persists(tmp_path):
    first = Console(tmp_path / "data", StepClock())
    first.set_actor("Jamie Rivera")
    assert Console(tmp_path / "data", StepClock()).actor == "Jamie Rivera"


def test_activity_spans_workspaces(console):
    new_account(console, "Local one")
    demo_seed.load(console, "beginning")
    accounts = {i.account_id for i in console.activity()}
    assert len(accounts) == 2
