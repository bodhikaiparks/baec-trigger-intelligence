"""Public-demo isolation and hard read-only checks."""

import pytest

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
        assert view.current_baec is not None
        assert view.plan is not None
        assert view.verified_correspondences()
        assert view.preparations
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
