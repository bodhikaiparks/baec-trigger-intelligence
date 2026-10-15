"""The Streamlit shell renders every page and workspace section without error (in-process, no browser, no server)."""

from __future__ import annotations

from html import escape
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _click(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def _texts(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def test_empty_dashboard_renders(app):
    assert "Accounts tracked" in _texts(app)
    assert "No accounts yet" in _texts(app)


@pytest.mark.parametrize("page", ["Accounts", "Review Queue", "Preparation", "Activity", "Dashboard"])
def test_every_page_renders_with_the_completed_example(app, page):
    _click(app, "seed-completed")
    _click(app, f"nav-{page}")


def test_completed_example_workspace_sections_render(app):
    _click(app, "seed-completed")
    text = _texts(app)
    assert "BAEC prepares the conversation. The salesperson decides whether to have it." in text
    assert "Prepared for human use" in text
    for label in ("Send", "Ready to send", "Approved for outreach"):
        assert label not in [b.label for b in app.button]
    for section in ("Remember", "Monitor", "Recognize", "Timeline", "Account state", "Prepare"):
        app.segmented_control[0].set_value(section).run()
        assert not app.exception, (section, app.exception)


def test_start_from_beginning_shows_capture(app):
    _click(app, "seed-beginning")
    assert "Buyer interactions" in _texts(app)
    app.segmented_control[0].set_value("Monitor").run()
    assert "Condition not confirmed" in _texts(app)
    app.segmented_control[0].set_value("Prepare").run()
    assert "Preparation not eligible" in _texts(app)


def test_reset_returns_to_an_empty_dashboard(app):
    _click(app, "seed-completed")
    _click(app, "seed-reset")
    assert "No accounts yet" in _texts(app)


def _open(tmp_path, monkeypatch, account_id: str, section: str) -> AppTest:
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["account"] = account_id
    at.query_params["section"] = section
    at.run()
    assert not at.exception, at.exception
    return at


def test_ui_preview_then_human_confirmation(tmp_path, monkeypatch):
    from baec_console import demo_seed
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, add_interaction, new_account
    console = Console(tmp_path / "data", StepClock())
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    draft_id = console.local.capture_condition(account_id, interaction_id, demo_seed.condition_form(), actor=ACTOR)
    at = _open(tmp_path, monkeypatch, account_id, "Remember")
    assert "Captured · not yet confirmed" in _texts(at)
    assert not [b for b in at.button if b.label == "Confirm buyer-articulated condition"]  # preview comes first
    _click(at, f"pv-{draft_id}")
    assert "Exactly what will be recorded" in _texts(at)
    _click(at, f"cf-{draft_id}")
    assert console.local.account_view(account_id).current_baec is not None


def test_ui_not_enough_evidence_records_a_review_that_does_not_verify(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, monitored, observe
    console = Console(tmp_path / "data", StepClock())
    account_id, baec_id = monitored(console)
    candidate_id = observe(console, account_id, baec_id).candidate_id
    at = _open(tmp_path, monkeypatch, account_id, "Recognize")
    assert at.button(key=f"{candidate_id}-verify").disabled  # nothing answered: cannot verify
    _click(at, f"{candidate_id}-insufficient")
    (review, outcome), = console.local.account_view(account_id).candidates[0]["reviews"]
    assert outcome.value == "POSSIBLE_CORRESPONDENCE"
    assert {f.finding.value for f in review.findings} <= {"UNRESOLVED", "NOT_APPLICABLE"}
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert "Preparation not eligible" in _texts(at)


def test_ui_refused_draft_is_shown_with_reasons_and_can_be_re_requested(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, permitted_request, verified
    console = Console(tmp_path / "data", StepClock())
    account_id, _, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result, dispositions={}), actor=ACTOR)
    console.local.generate_preparation(request_id, actor=ACTOR)
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    text = _texts(at)
    assert "Engine 3 validation refused this draft" in text
    assert escape("Refers to the buyer's statement without permission to do so.") in text
    assert any(x.label == "Request preparation again with different choices" for x in at.expander)
    assert not [b for b in at.button if b.label == "Accept as drafted" and "EMAIL" in (b.key or "")]


def test_ui_block_control_records_a_human_block_and_stops_preparation(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, verified
    console = Console(tmp_path / "data", StepClock())
    account_id, _, _, review_id = verified(console)
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert any(b.label == "Prepare from this correspondence" for b in at.button)
    _click(at, f"block-{review_id}")
    assert console.local.account_view(account_id).blocks[review_id][-1]["action"] == "BLOCK"
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert "Preparation is blocked by human decision" in _texts(at)
    assert not any(b.label == "Prepare from this correspondence" for b in at.button)
    _click(at, f"unblock-{review_id}")
    assert [d["action"] for d in console.local.account_view(account_id).blocks[review_id]] == ["BLOCK", "UNBLOCK"]


def test_ui_lists_each_verified_correspondence_for_explicit_selection(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, observe, review, verified
    console = Console(tmp_path / "data", StepClock())
    account_id, baec_id, _, first_review = verified(console)
    second = observe(console, account_id, baec_id, new_price="44000")
    second_review = review(console, second.candidate_id).review.review_id
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert len(box.options) == 2
    assert box.value == second_review  # newest shown first; nothing is recorded until the human requests
    box.set_value(first_review).run()
    assert not at.exception
    assert console.local.account_view(account_id).requests == []
