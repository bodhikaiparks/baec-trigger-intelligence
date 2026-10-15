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


def test_harness_amendment_001_is_recorded_and_hash_bound():
    assert [a["id"] for a in MANIFEST["harness_amendments"]] == ["HARNESS_AMENDMENT_001", "HARNESS_AMENDMENT_002"]
    amendment = MANIFEST["harness_amendments"][0]
    assert amendment["applied"] is True and amendment["date"] == "2026-10-15"
    assert amendment["files_modified"] == ["experiments/engine3_stage_f/readiness.py"]
    assert _sha256(REPO / amendment["record"]) == amendment["record_sha256"]
    assert MANIFEST["status"] == "LOCKED_OFFLINE_HARNESS_NOT_EXECUTED"
    assert "HARNESS_AMENDMENT_001" in REPORT_PATH.read_text(encoding="utf-8")


def test_harness_amendment_002_is_recorded_hash_bound_and_limited_to_the_packet_guide_examples():
    amendment = MANIFEST["harness_amendments"][1]
    assert amendment["id"] == "HARNESS_AMENDMENT_002"
    assert amendment["applied"] is True and amendment["date"] == "2026-10-15" and amendment["owner_approved"] is True
    assert amendment["files_modified"] == ["experiments/engine3_stage_f/annotation.py"]
    assert amendment["example_substitutions"] == {"9.00% -> 9%": "$46.00 -> $46",
                                                  "more than 8% -> above 8%": "over $40 -> higher than $40"}
    assert amendment["experiment_rules_changed"] is False
    assert _sha256(REPO / amendment["record"]) == amendment["record_sha256"]
    # HARNESS_AMENDMENT_001 history is kept unchanged.
    assert MANIFEST["harness_amendments"][0]["record_sha256"] == (
        "a2350ca991716b545c213b41cc1f8a56710370299a7f6c55f0ba481462977536")
    assert "HARNESS_AMENDMENT_002" in REPORT_PATH.read_text(encoding="utf-8")


def test_o6_lock_time_status_is_preserved_and_current_status_is_bound_to_the_freeze():
    # 8. Historical: O-6 was PRE_RUN_REQUIRED when the harness was locked.
    assert MANIFEST["o6_status_at_harness_lock"] == "PRE_RUN_REQUIRED"
    assert {i["id"]: i["status"] for i in MANIFEST["pre_run_required"]}["O-6"] == "PRE_RUN_REQUIRED"
    # 7. Current: O-6 is RESOLVED_FROZEN, bound to the unchanged freeze manifest; O-3 and O-5 stay unresolved.
    current = MANIFEST["pre_run_state_current"]
    assert MANIFEST["o6_current_status"] == "RESOLVED_FROZEN"
    assert current["status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "PRE_RUN_REQUIRED", "O-6": "RESOLVED_FROZEN"}
    assert current["pilot_readiness"] is False
    # 9. The O-6 freeze manifest and frozen corpus are byte-identical to the bound hashes.
    freeze_path = REPO / current["o6_freeze_manifest"]
    assert current["o6_freeze_manifest"] == "docs/engine3/ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json"
    assert _sha256(freeze_path) == current["o6_freeze_manifest_sha256"]
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert freeze["status"] == "O6_RESOLVED_FROZEN" and current["o6_frozen_file"] == freeze["frozen_file"]
    assert _sha256(REPO / freeze["frozen_file"]) == freeze["frozen_file_sha256"] == current["o6_frozen_file_sha256"]


def test_report_states_status_limitation_and_claim_boundary():
    report = REPORT_PATH.read_text(encoding="utf-8")
    for text in ("LOCKED_OFFLINE_HARNESS_NOT_EXECUTED", "E3-E-SEM-001", "NOT_EVALUABLE", "PRE_RUN_REQUIRED",
                 "LIVE_SYNTHETIC_EXPERIMENT", MANIFEST["claim_boundary"]):
        assert text in report, text
    lowered = report.lower()
    for overclaim in ("validated the model", "model is reliable", "100% accura", "resolves e3-e-sem-001",
                      "production ready", "production-ready"):
        assert overclaim not in lowered, overclaim
