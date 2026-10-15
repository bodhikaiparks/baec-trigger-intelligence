"""O5_POSTFREEZE_REBINDING_001: the O-5 verifier accepts exactly the approved rebinding and nothing else."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "verify_engine3_stage_f_o5_freeze", REPO / "scripts" / "verify_engine3_stage_f_o5_freeze.py")
o5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o5)

FROZEN = json.loads(o5.FROZEN.read_text(encoding="utf-8"))
MANIFEST = json.loads(o5.MANIFEST.read_text(encoding="utf-8"))
REBINDING = json.loads(o5.REBINDING.read_text(encoding="utf-8"))
VERIFIER_SHA = o5.file_sha(Path(o5.__file__).resolve())


def _findings(manifest=MANIFEST, frozen=FROZEN, verifier_sha=VERIFIER_SHA):
    return o5.rebinding_findings(manifest, frozen, verifier_sha)


def _with_record(tmp_path, monkeypatch, record):
    path = tmp_path / "rebinding.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(o5, "REBINDING", path)


def test_verifier_passes():
    assert o5.main() == 0


def test_original_o5_records_are_byte_identical():
    assert o5.file_sha(o5.MANIFEST) == o5.ORIGINAL_MANIFEST_SHA256
    assert o5.file_sha(o5.FROZEN) == o5.ORIGINAL_FROZEN_SHA256 == MANIFEST["frozen_record_sha256"]


def test_approved_rebinding_is_accepted_and_the_original_hashes_are_still_checked():
    problems, counts = _findings()
    assert problems == []
    assert counts == {"unchanged": len(MANIFEST["locked_inputs_sha256"]) - 6, "rebound": 6}
    for rel, (original, _) in o5.REBOUND_LOCKED_INPUTS.items():
        assert MANIFEST["locked_inputs_sha256"][rel] == original


def test_any_other_locked_input_divergence_fails():
    manifest = copy.deepcopy(MANIFEST)
    manifest["locked_inputs_sha256"]["experiments/engine3_stage_f/readiness.py"] = "0" * 64
    problems, _ = _findings(manifest=manifest)
    assert "locked input changed since freeze: experiments/engine3_stage_f/readiness.py" in problems


def test_a_permitted_file_at_an_unapproved_hash_fails(monkeypatch):
    rebound = dict(o5.REBOUND_LOCKED_INPUTS)
    rel = "experiments/engine3_stage_f/annotation.py"
    rebound[rel] = (rebound[rel][0], "f" * 64)
    monkeypatch.setattr(o5, "REBOUND_LOCKED_INPUTS", rebound)
    problems, _ = _findings()
    assert f"locked input changed since freeze: {rel}" in problems


def test_a_rebinding_record_that_adds_a_file_fails(tmp_path, monkeypatch):
    record = copy.deepcopy(REBINDING)
    record["rebound_locked_inputs"]["experiments/engine3_stage_f/readiness.py"] = {
        "original_sha256": MANIFEST["locked_inputs_sha256"]["experiments/engine3_stage_f/readiness.py"],
        "post_sha256": "0" * 64}
    _with_record(tmp_path, monkeypatch, record)
    problems, _ = _findings()
    assert "rebinding locked-input list or hashes differ from the approved set" in problems


@pytest.mark.parametrize("key, value", [
    ("rebinding_id", "O5_POSTFREEZE_REBINDING_002"),
    ("amendment", "HARNESS_AMENDMENT_003"),
    ("original_o5_records_modified", True),
])
def test_a_different_rebinding_or_amendment_id_fails(tmp_path, monkeypatch, key, value):
    record = copy.deepcopy(REBINDING)
    record[key] = value
    _with_record(tmp_path, monkeypatch, record)
    problems, _ = _findings()
    assert problems


def test_a_missing_rebinding_record_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(o5, "REBINDING", tmp_path / "absent.json")
    problems, _ = _findings()
    assert problems


def test_an_unrecorded_verifier_change_fails():
    problems, _ = _findings(verifier_sha="0" * 64)
    assert "O-5 verifier hash is neither the original nor the rebound hash" in problems


def test_unchanged_configuration_attestation_must_match_the_frozen_record():
    frozen = copy.deepcopy(FROZEN)
    frozen["frozen_configuration"]["generation"]["max_tokens"] = 1
    problems, _ = _findings(frozen=frozen)
    assert "rebinding's unchanged O-5 configuration does not match the original frozen record" in problems
    assert REBINDING["unchanged_o5_configuration"]["pilot_run_order_size"] == 44
    assert REBINDING["unchanged_o5_configuration"]["e3_e_sem_001_resolved"] is False
