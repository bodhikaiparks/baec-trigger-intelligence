"""Public Console remains complete while persistent writes are locked."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = str(Path(__file__).resolve().parents[1] / "public_app.py")


def _button(at: AppTest, label: str):
    matches = [b for b in at.button if b.label == label]
    assert matches, label
    return matches[0]


def test_public_console_exposes_full_navigation_and_workspace():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()

    assert not at.exception

    assert _button(at, "Research Overview").disabled is False
    assert _button(at, "Accounts").disabled is False
    assert "When the Buyer Is Not In-Market" in " ".join(m.value for m in at.markdown)

    _button(at, "Open full system walkthrough").click().run()
    assert not at.exception

    assert at.segmented_control[0].options == [
        "Remember",
        "Monitor",
        "Recognize",
        "Prepare",
        "Timeline",
        "Account state",
    ]

    assert _button(at, "All accounts").disabled is False


def test_remember_forms_are_visible_but_writes_are_locked():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()

    assert not at.exception
    _button(at, "Guided walkthrough").click().run()
    assert not at.exception

    add_interaction = _button(at, "+ Add buyer interaction")
    assert not add_interaction.disabled
    add_interaction.click().run()
    assert not at.exception
    assert _button(at, "🔒 Record interaction").disabled

    capture = _button(at, "+ Capture buyer-articulated condition")
    assert not capture.disabled
    capture.click().run()
    assert not at.exception
    assert _button(at, "🔒 Capture condition").disabled


def test_monitor_write_is_visible_but_locked():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()

    _button(at, "Guided walkthrough").click().run()
    assert not at.exception

    at.segmented_control[0].set_value("Monitor").run()
    assert not at.exception

    assert _button(at, "🔒 Add observation").disabled


def test_accounts_page_is_real_but_account_creation_is_locked():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()

    _button(at, "Accounts").click().run()
    assert not at.exception

    add_account = _button(at, "+ Add account")
    assert not add_account.disabled
    add_account.click().run()
    assert not at.exception

    assert _button(at, "🔒 Add account").disabled
