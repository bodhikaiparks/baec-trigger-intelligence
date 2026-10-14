"""Verify the locked BAEC Engine 3 Stage F experimental harness (LOCKED_OFFLINE_HARNESS_NOT_EXECUTED).

Offline only: no model, no network call, no key. Confirms the locked Stage F design
artifacts and the locked Stage A-E artifacts are unchanged, checks the harness manifest
hashes and coverage, checks that dry-run inputs and the oracle stay separate, checks the
no-execution record and that O-3, O-5, and O-6 remain unresolved, then runs the harness
tests, which include the static audit. Separate from scripts/verify_engine3_stage_f_design.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "engine3"
HARNESS_MANIFEST = DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json"
DESIGN_MANIFEST = DOCS / "ENGINE3_STAGE_F_DESIGN_MANIFEST.json"
SPEC = DOCS / "ENGINE3_STAGE_F_EXPERIMENT_SPEC.json"
STAGE_E_MANIFEST = DOCS / "ENGINE3_STAGE_E_IMPLEMENTATION_MANIFEST.json"
STAGE_D_MANIFEST = DOCS / "ENGINE3_STAGE_D_IMPLEMENTATION_MANIFEST.json"
DESIGN_VERIFIER = REPO / "scripts" / "verify_engine3_stage_f_design.py"
HARNESS_DIR = REPO / "experiments" / "engine3_stage_f"
INPUTS = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_INPUTS.json"
ORACLE = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_ORACLE.json"

STATUS = "LOCKED_OFFLINE_HARNESS_NOT_EXECUTED"
LOCK_DATE = "2026-10-14"
NO_EXECUTION = {
    "live_model_executed": False,
    "api_key_used": False,
    "external_model_sdk_used": False,
    "network_calls": 0,
    "pilot_executed": False,
    "main_experiment_executed": False,
}
PRE_RUN_REQUIRED = ("O-3", "O-5", "O-6")
HARNESS_GLOBS = (
    "experiments/engine3_stage_f/*.py",
    "tests/engine3/stage_f_adapter.py",
    "tests/engine3/test_engine3_stage_f_*.py",
    "tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_*.json",
    "scripts/verify_engine3_stage_f_harness.py",
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md",
)

# Independent cross-check of the static audit: no model SDK or network client import.
_NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:anthropic|openai|mcp|requests|httpx|aiohttp|urllib|http\.client|socket|smtplib)\b",
    re.MULTILINE,
)
_TEST_IMPORT = re.compile(r"^\s*(?:import|from)\s+tests\b", re.MULTILINE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_hashes(expected: dict[str, str], label: str, failures: list[str]) -> None:
    for rel, digest in expected.items():
        path = REPO / rel
        if not path.exists():
            failures.append(f"{label}: missing {rel}")
        elif _sha256(path) != digest:
            failures.append(f"{label}: hash mismatch {rel}")


def _check_locked(harness: dict, failures: list[str]) -> int:
    if _sha256(DESIGN_MANIFEST) != harness.get("stage_f_design_manifest_sha256"):
        failures.append("Stage F design manifest changed")
        return 0
    design = _load(DESIGN_MANIFEST)
    _check_hashes(design["design_artifacts_sha256"], "Stage F design", failures)
    if _sha256(DESIGN_VERIFIER) != design["verifier_sha256"]:
        failures.append("Stage F design verifier changed")
    if _sha256(STAGE_E_MANIFEST) != design["stage_e_manifest_sha256"]:
        failures.append("Stage E manifest changed")
        return 0
    stage_e = _load(STAGE_E_MANIFEST)
    _check_hashes(stage_e["stage_e_files"], "Stage E", failures)
    if _sha256(STAGE_D_MANIFEST) != stage_e["stage_d_manifest_sha256"]:
        failures.append("Stage D manifest changed")
        return 0
    stage_d = _load(STAGE_D_MANIFEST)
    locked: dict[str, str] = {}
    for group in ("locked_artifact_hashes", "production_files", "test_files"):
        locked.update(stage_d[group])
    _check_hashes(locked, "Stage D", failures)
    spec_inputs = _load(SPEC)["locked_inputs_sha256"]
    _check_hashes(spec_inputs, "Stage A-E input", failures)
    return len(design["design_artifacts_sha256"]) + 1 + len(stage_e["stage_e_files"]) + len(locked) + len(spec_inputs)


def _check_manifest(harness: dict, failures: list[str]) -> None:
    if harness.get("status") != STATUS or harness.get("lock_date") != LOCK_DATE:
        failures.append(f"harness manifest: status must be {STATUS} locked {LOCK_DATE}")
    for key, value in NO_EXECUTION.items():
        if key not in harness or type(harness[key]) is not type(value) or harness[key] != value:
            failures.append(f"harness manifest: {key} must be {value!r}")
    limitation = harness.get("governing_limitation", {})
    if limitation.get("id") != "E3-E-SEM-001" or limitation.get("resolved") is not False:
        failures.append("harness manifest: E3-E-SEM-001 must remain unresolved")
    items = {i.get("id"): i.get("status") for i in harness.get("pre_run_required", [])}
    if items != {i: "PRE_RUN_REQUIRED" for i in PRE_RUN_REQUIRED}:
        failures.append("harness manifest: O-3, O-5, O-6 must remain PRE_RUN_REQUIRED")
    if harness.get("pre_run_records_supplied") is not False:
        failures.append("harness manifest: no pre-run record may be recorded as supplied")
    files = harness.get("harness_files", {})
    _check_hashes(files, "harness", failures)
    found = {p.relative_to(REPO).as_posix() for pattern in HARNESS_GLOBS for p in REPO.glob(pattern)}
    if set(files) != found:
        failures.append(f"harness manifest coverage: unlisted={sorted(found - set(files))} "
                        f"missing={sorted(set(files) - found)}")
    if HARNESS_MANIFEST.relative_to(REPO).as_posix() in files:
        failures.append("harness manifest must not hash itself")


def _keys(value):
    if isinstance(value, dict):
        yield from value
        for v in value.values():
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def _check_separation_and_boundaries(failures: list[str]) -> None:
    inputs = _load(INPUTS)
    if [k for k in _keys(inputs) if k.startswith("expected")]:
        failures.append("dry-run inputs contain expected results")
    if inputs.get("all_data_synthetic") is not True or inputs.get("dry_run_only") is not True:
        failures.append("dry-run inputs must be marked synthetic and dry-run only")
    oracle = _load(ORACLE)
    if [c["case_id"] for c in inputs["cases"]] != [c["case_id"] for c in oracle["cases"]]:
        failures.append("dry-run oracle does not align with the inputs")
    for path in sorted(HARNESS_DIR.glob("*.py")) + [Path(__file__).resolve()]:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        if _NETWORK_IMPORT.search(text):
            failures.append(f"model SDK or network import: {rel}")
        if path.parent == HARNESS_DIR and (_TEST_IMPORT.search(text) or INPUTS.name in text or ORACLE.name in text):
            failures.append(f"harness code references dry-run inputs or the oracle: {rel}")


def main() -> int:
    failures: list[str] = []
    harness = _load(HARNESS_MANIFEST)
    print(f"Stage F harness status: {harness.get('status')} (manifest sha256 {_sha256(HARNESS_MANIFEST)})")
    locked = _check_locked(harness, failures)
    _check_manifest(harness, failures)
    _check_separation_and_boundaries(failures)
    if failures:
        for failure in failures:
            print("FAIL ", failure)
        return 1
    print(f"PASS  Locked Stage F design and Stage A-E artifacts unchanged (hash checks): {locked}")
    print(f"PASS  Harness file hashes and coverage: {len(harness['harness_files'])}")
    print("PASS  Dry-run inputs and oracle are separate; harness code references neither")
    print("PASS  No-execution record: " + ", ".join(f"{k}={v}" for k, v in NO_EXECUTION.items()))
    print(f"PASS  Pre-run items remain PRE_RUN_REQUIRED: {', '.join(PRE_RUN_REQUIRED)}")
    print("PASS  No model SDK or network import in harness code or this verifier")

    tests = sorted(rel for rel in harness["harness_files"] if Path(rel).name.startswith("test_"))
    sys.stdout.flush()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=REPO)
    if result.returncode:
        print("FAIL  Stage F harness tests (including the static audit)")
        return result.returncode
    print(f"PASS  Stage F harness tests, including the static audit: {len(tests)} files")
    print("LIMIT E3-E-SEM-001 MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE remains NOT RESOLVED. The harness is")
    print("      locked offline and has not been executed; it validates no model and authorizes no run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
