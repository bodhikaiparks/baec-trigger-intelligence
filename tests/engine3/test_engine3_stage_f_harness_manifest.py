"""Stage F harness manifest: complete, hash-bound, and honest that nothing has been executed."""

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "engine3"
MANIFEST_PATH = DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json"
DESIGN_MANIFEST_PATH = DOCS / "ENGINE3_STAGE_F_DESIGN_MANIFEST.json"
REPORT_PATH = DOCS / "ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
DESIGN = json.loads(DESIGN_MANIFEST_PATH.read_text(encoding="utf-8"))
GLOBS = (
    "experiments/engine3_stage_f/*.py",
    "tests/engine3/stage_f_adapter.py",
    "tests/engine3/test_engine3_stage_f_*.py",
    "tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_*.json",
    "scripts/verify_engine3_stage_f_harness.py",
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_harness_file_hash_matches_the_manifest():
    for rel, expected in MANIFEST["harness_files"].items():
        assert _sha256(REPO / rel) == expected, rel


def test_manifest_covers_every_harness_file_and_not_itself():
    found = {p.relative_to(REPO).as_posix() for pattern in GLOBS for p in REPO.glob(pattern)}
    assert set(MANIFEST["harness_files"]) == found
    assert MANIFEST_PATH.relative_to(REPO).as_posix() not in MANIFEST["harness_files"]


def test_manifest_is_bound_to_the_unchanged_locked_design():
    assert MANIFEST["stage_f_design_manifest_sha256"] == _sha256(DESIGN_MANIFEST_PATH)
    assert MANIFEST["base_commit"] == "c81d00343d96683514dd38f43d11a6ebae6d104e"
    for rel, expected in DESIGN["design_artifacts_sha256"].items():
        assert _sha256(REPO / rel) == expected, rel
    assert _sha256(REPO / "scripts" / "verify_engine3_stage_f_design.py") == DESIGN["verifier_sha256"]
    assert DESIGN["status"] == "LOCKED_DESIGN_NO_LIVE_EXECUTION"
    assert MANIFEST["planned_size"] == DESIGN["planned_size"]
    assert MANIFEST["model_controlled_fields"] == DESIGN["model_controlled_fields"]


def test_manifest_records_no_execution_and_unresolved_limitation():
    assert MANIFEST["status"] == "LOCKED_OFFLINE_HARNESS_NOT_EXECUTED"
    assert MANIFEST["lock_date"] == "2026-10-14"
    expected = {"live_model_executed": False, "api_key_used": False, "external_model_sdk_used": False,
                "network_calls": 0, "pilot_executed": False, "main_experiment_executed": False,
                "live_model_authorized": False, "outreach_authorized": False, "pre_run_records_supplied": False}
    for key, value in expected.items():
        assert type(MANIFEST[key]) is type(value) and MANIFEST[key] == value, key
    assert MANIFEST["governing_limitation"] == {
        "id": "E3-E-SEM-001", "name": "MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE", "resolved": False}
    assert {i["id"]: i["status"] for i in MANIFEST["pre_run_required"]} == {
        "O-3": "PRE_RUN_REQUIRED", "O-5": "PRE_RUN_REQUIRED", "O-6": "PRE_RUN_REQUIRED"}


def test_report_states_status_limitation_and_claim_boundary():
    report = REPORT_PATH.read_text(encoding="utf-8")
    for text in ("LOCKED_OFFLINE_HARNESS_NOT_EXECUTED", "E3-E-SEM-001", "NOT_EVALUABLE", "PRE_RUN_REQUIRED",
                 "LIVE_SYNTHETIC_EXPERIMENT", MANIFEST["claim_boundary"]):
        assert text in report, text
    lowered = report.lower()
    for overclaim in ("validated the model", "model is reliable", "100% accura", "resolves e3-e-sem-001",
                      "production ready", "production-ready"):
        assert overclaim not in lowered, overclaim
