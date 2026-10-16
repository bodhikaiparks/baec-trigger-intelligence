"""Public-demo isolation and hard read-only checks."""

import pytest

from baec_app.domain.enums import AccountState
from baec_console.console_service import ConsoleError
from baec_console.public_runtime import PUBLIC_ACTOR, new_public_runtime


def test_public_runtimes_are_isolated():
    first = new_public_runtime()
    second = new_public_runtime()

    try:
        assert first.directory != second.directory
        assert first.account_id != second.account_id
        assert first.console.actor == PUBLIC_ACTOR
        assert second.console.actor == PUBLIC_ACTOR
    finally:
        first.cleanup()
        second.cleanup()


def test_public_runtime_starts_with_completed_synthetic_example():
    runtime = new_public_runtime()

    try:
        view = runtime.console.synthetic.account_view(runtime.account_id)

        assert view.synthetic
        assert view.display_name == "Alder Ridge Clinics"
        assert view.account.state is AccountState.CONDITIONALLY_DORMANT
        assert view.current_baec is not None
        assert view.plan is not None

        assert len(view.observations) == 2
        assert len(view.superseded) == 1
        assert len(view.measurements) == 2
        assert {str(m.output_value) for m in view.measurements} == {"12.00", "9.00"}

        assert view.verified_correspondences()
        assert view.preparations

        request = view.requests[0]
        current_evidence = request["snapshot"]["evidence"]
        assert len(current_evidence) == 1
        assert "43,600" in current_evidence[0]["text"]
        assert "44,800" not in current_evidence[0]["text"]
    finally:
        runtime.cleanup()


def test_public_runtime_refuses_persistent_writes():
    runtime = new_public_runtime()

    try:
        with pytest.raises(ConsoleError, match="read-only"):
            runtime.console.local.create_account(
                "Blocked account",
                industry="",
                owner_note="",
                actor=PUBLIC_ACTOR,
            )
    finally:
        runtime.cleanup()
