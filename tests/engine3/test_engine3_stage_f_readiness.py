"""O-3, O-5, and O-6 readiness: validation only. Nothing is filled in and nothing executes.

Every value below is a test placeholder, not a frozen configuration, staffing decision, or supplement.
"""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from experiments.engine3_stage_f.envelope import OUTPUT_SCHEMA_VERSION
from experiments.engine3_stage_f.readiness import (
    ARM_A_SUPPLEMENT_COUNT,
    AnnotatorStaffing,
    FrozenRunConfiguration,
    PilotReadiness,
    SupplementArtifact,
    check_pilot_readiness,
)

REPO = Path(__file__).resolve().parents[2]
STAFF = AnnotatorStaffing("TEST-ONLY-A1", "TEST-ONLY-A2", "TEST-ONLY-ADJ", frozen=True)
CONFIG = FrozenRunConfiguration("TEST-ONLY-CONFIG", "TEST-ONLY-MODEL-ID", "TEST-ONLY-VERSION", "c" * 64,
                                (("temperature", 0),), OUTPUT_SCHEMA_VERSION, frozen=True)
SUPPLEMENT = tuple(SupplementArtifact(f"TEST-ONLY-SUPP-{n:02d}", f"{n:064x}", ("TEST-ONLY-A1",), True, True)
                   for n in range(1, ARM_A_SUPPLEMENT_COUNT + 1))


def _items(readiness: PilotReadiness) -> set[str]:
    return {item for item, _ in readiness.unresolved}


def test_nothing_supplied_leaves_o3_o5_o6_unresolved():
    readiness = check_pilot_readiness()
    assert not readiness.preconditions_met and _items(readiness) == {"O-3", "O-5", "O-6"}


def test_complete_inputs_meet_preconditions_but_never_authorize_a_run():
    readiness = check_pilot_readiness(STAFF, CONFIG, SUPPLEMENT)
    assert readiness.preconditions_met and readiness.unresolved == ()
    assert readiness.owner_approval_still_required is True
    public = {n for n in dir(readiness) if not n.startswith("_")}
    assert public == {"unresolved", "preconditions_met", "owner_approval_still_required"}


@pytest.mark.parametrize("staffing", [
    replace(STAFF, frozen=False), replace(STAFF, primary_b_alias="TEST-ONLY-A1"),
    replace(STAFF, adjudicator_alias="TEST-ONLY-A2"), replace(STAFF, adjudicator_alias=" "), "A1,A2,ADJ",
])
def test_o3_requires_two_distinct_primaries_and_a_separate_frozen_adjudicator(staffing):
    assert "O-3" in _items(check_pilot_readiness(staffing, CONFIG, SUPPLEMENT))


@pytest.mark.parametrize("configuration", [
    replace(CONFIG, frozen=False), replace(CONFIG, model_identifier=""), replace(CONFIG, model_version=""),
    replace(CONFIG, experiment_configuration_id=""), replace(CONFIG, prompt_sha256="C" * 64),
    replace(CONFIG, sampling_configuration=()), replace(CONFIG, sampling_configuration=(("t", 0), ("t", 1))),
    replace(CONFIG, output_schema_version="engine3.ai_preparation_proposal.v1"),
])
def test_o5_requires_an_exact_frozen_configuration(configuration):
    assert _items(check_pilot_readiness(STAFF, configuration, SUPPLEMENT)) == {"O-5"}


@pytest.mark.parametrize("supplement", [
    SUPPLEMENT[:15], SUPPLEMENT + (replace(SUPPLEMENT[0], artifact_id="X"),),
    (replace(SUPPLEMENT[0], human_approved=False),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], frozen=False),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], artifact_sha256="not-a-hash"),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], author_aliases=()),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], author_aliases=("TEST-ONLY-A1", "TEST-ONLY-A2")),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[1], artifact_id=SUPPLEMENT[0].artifact_id),) + SUPPLEMENT[1:],
])
def test_o6_requires_sixteen_approved_frozen_independently_annotated_artifacts(supplement):
    assert _items(check_pilot_readiness(STAFF, CONFIG, supplement)) == {"O-6"}


def test_o6_author_independence_cannot_be_checked_without_o3():
    assert _items(check_pilot_readiness(None, CONFIG, SUPPLEMENT)) == {"O-3", "O-6"}


def test_repository_supplies_no_readiness_record():
    """No harness or script constructs a staffing, configuration, or supplement record."""
    records = {"AnnotatorStaffing", "FrozenRunConfiguration", "SupplementArtifact"}
    paths = sorted((REPO / "experiments").rglob("*.py")) + sorted((REPO / "scripts").glob("*.py"))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in records, path.relative_to(REPO).as_posix()
