"""Offline verifier for the Engine 3 Stage F O-5 frozen run configuration.

Checks, without any model call, credential read, or network access:

1. frozen-record status, pre-run statuses, and the no-execution record;
2. prompt, schema, and configuration hashes recomputed from the frozen record;
3. the freeze manifest's bindings, including every locked input recorded at freeze;
4. the review document's prompt copies are byte-identical to the frozen record;
5. the thinking instruction is present exactly once, immediately before the JSON instruction, and the
   schema has no reasoning field;
6. structured-output schema semantic neutrality (structural keywords only, full locked vocabularies,
   no cross-field rule);
7. protocol normative definitions copied verbatim into the prompt;
8. worked-example and whole-prompt contamination against every evaluation prose source;
9. the materialized pilot run order recomputed from the frozen algorithm E3F_RUN_ORDER_V1;
10. the human-reference contamination issue record exists with its blocker status.

Exit status 0 only if every check passes. This is a software check of documents. It is not a test of
any model and not evidence about annotation reliability.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from baec_app.engine3.enums import (  # noqa: E402
    ActionAttempt,
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    ReferenceUseMode,
)
from baec_app.engine3.models import ClaimField  # noqa: E402
from experiments.engine3_stage_f.envelope import OUTPUT_SCHEMA_VERSION  # noqa: E402

DOCS = REPO / "docs" / "engine3"
FROZEN = DOCS / "ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json"
MANIFEST = DOCS / "ENGINE3_STAGE_F_O5_FREEZE_MANIFEST.json"
REVIEW = DOCS / "ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_REVIEW.md"
ISSUE = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_ISSUE.md"
PROTOCOL = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md"
SPEC = DOCS / "ENGINE3_STAGE_F_EXPERIMENT_SPEC.json"
O6_FROZEN = DOCS / "ENGINE3_STAGE_F_ARM_A_ADDITIONAL_ARTIFACTS_FROZEN.json"
STAGE_E_FIXTURE = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_E_PRERECORDED_PROPOSALS.json"
DRY_RUN_INPUTS = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_INPUTS.json"

STATUS = "O5_RESOLVED_FROZEN"
PRE_RUN = {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
ISSUE_STATUS = "PRE_HUMAN_ANNOTATION_BLOCKER"
THINKING_SENTENCE = "Think the problem through before you answer."
JSON_INSTRUCTION = "Respond with one JSON object that follows the output format below, and nothing else."

# Stage F design section 5: the 32 Stage E seed artifacts of Arm A.
ARM_A_SEED_CATEGORIES = ("VALID", "DISCLOSURE", "CLAIM", "STATE", "ALDER_RIDGE_NEGATIVE", "INJECTION")
ARM_A_SEED_EXCLUDED = ("E3E-I03", "E3E-N17")

# --- run order (frozen algorithm) -----------------------------------------------------------------
RUN_ORDER_ALGORITHM = "E3F_RUN_ORDER_V1"
RUN_ORDER_SEPARATOR = "\n"

# --- near-duplicate rule (deterministic) ----------------------------------------------------------
MIN_TOKENS = 4
JACCARD_NEAR = 0.5
SHINGLE = 5
_TOKEN = re.compile(r"\$?[a-z0-9]+(?:\.[0-9]+)?%?")
_SENTENCE = re.compile(r"(?<=[.?!])\s+")
BANNED_TOKENS = ("alder", "ridge", "cobalt", "july")
BANNED_PATTERNS = (r"\b8\s*%", r"\b9(?:\.00)?\s*%", r"\bmore than 8\b", r"\babove 8\b")

# --- schema neutrality rule -----------------------------------------------------------------------
STRUCTURAL_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf"})
NULLABLE_STRING = [{"type": "string"}, {"type": "null"}]
VOCABULARIES = {
    "schema_version": [OUTPUT_SCHEMA_VERSION],
    "requested_action": [e.value for e in ActionAttempt],
    "artifact_type": [e.value for e in ArtifactType],
    "claim_type": [e.value for e in ClaimType],
    "semantic_class": [e.value for e in ClaimSemanticClass],
    "reference_use_mode": [e.value for e in ReferenceUseMode],
    "claim_field": [e.value for e in ClaimField],
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENTENCE.split(text)) if s]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- evaluation prose corpus ----------------------------------------------------------------------


def _artifact_texts(artifact: dict) -> list[str]:
    out = [s["text"] for s in artifact.get("segments", []) if isinstance(s, dict) and "text" in s]
    out += [s[1] for s in artifact.get("segments", []) if isinstance(s, list) and len(s) == 2]
    if isinstance(artifact.get("rendered_text"), str):
        out.append(artifact["rendered_text"])
    return out


def _dedupe(corpus: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen, unique = set(), []
    for item in corpus:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def arm_a_corpus() -> list[tuple[str, str]]:
    """(source label, sentence) for the 48 Arm A fixed-prose artifacts only."""
    corpus: list[tuple[str, str]] = []
    stage_e = _load(STAGE_E_FIXTURE)
    for rec in stage_e["recordings"]:
        if rec["category"] in ARM_A_SEED_CATEGORIES and rec["recording_id"] not in ARM_A_SEED_EXCLUDED:
            for art in rec["proposal"]["artifacts"]:
                for t in _artifact_texts(art):
                    corpus += [(f"Arm A seed {rec['recording_id']} {art['artifact_id']}", s) for s in sentences(t)]
    for art in _load(O6_FROZEN)["artifacts"]:
        for t in _artifact_texts(art["fixed_prose"]) + [art["rendered_text"]]:
            corpus += [(f"Arm A O-6 {art['artifact_id']}", s) for s in sentences(t)]
    return _dedupe(corpus)


def evaluation_corpus() -> list[tuple[str, str]]:
    """(source label, sentence) for every evaluation prose source named by the owner."""
    corpus: list[tuple[str, str]] = []

    def add(label: str, text: str) -> None:
        corpus.extend((label, s) for s in sentences(text))

    stage_e = _load(STAGE_E_FIXTURE)
    for context in stage_e["contexts"]:
        for item in context["grounding_items"]:
            add(f"Stage E context {context['context_id']} {item['grounding_item_id']}", item["exact_value"])
    for rec in stage_e["recordings"]:
        for art in rec["proposal"]["artifacts"]:
            for t in _artifact_texts(art):
                add(f"Stage E {rec['recording_id']} {art['artifact_id']}", t)
    for edit in stage_e["human_edits"]:
        edited = edit["edited"]
        for t in _artifact_texts(edited if isinstance(edited, dict) else {}):
            add(f"Stage E human edit {edit['edit_id']}", t)

    for art in _load(O6_FROZEN)["artifacts"]:
        for t in _artifact_texts(art["fixed_prose"]) + [art["rendered_text"]]:
            add(f"Arm A O-6 {art['artifact_id']}", t)

    spec = _load(SPEC)
    for variant in spec["contexts"]["stage_f_variants"]:
        item = variant["delta"].get("add_grounding_item")
        if item:
            add(f"Arm G context {variant['context_id']} {item['grounding_item_id']}", item["exact_value"])
    for scenario in spec["scenarios"]:
        for key in ("goal", "channel", "tone_preference"):
            if isinstance(scenario.get(key), str):
                add(f"Arm G scenario {scenario['id']} {key}", scenario[key])

    for case in _load(DRY_RUN_INPUTS)["cases"]:
        raw = case.get("raw_model_output")
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except json.JSONDecodeError:
            value = None
        for art in value.get("artifacts", []) if isinstance(value, dict) else []:
            for t in _artifact_texts(art):
                add(f"Stage F dry run {case['case_id']}", t)
    return _dedupe(corpus)


def compare(text: str, corpus: list[tuple[str, str]]) -> list[dict]:
    """Exact and near-duplicate findings for one sentence against the corpus."""
    a = tokens(text)
    if len(a) < MIN_TOKENS:
        return []
    a_set = set(a)
    a_sh = {tuple(a[i:i + SHINGLE]) for i in range(len(a) - SHINGLE + 1)}
    findings = []
    for label, s in corpus:
        b = tokens(s)
        if len(b) < MIN_TOKENS:
            continue
        if a == b:
            findings.append({"kind": "EXACT", "source": label, "evaluation_text": s})
            continue
        b_set = set(b)
        jac = len(a_set & b_set) / len(a_set | b_set)
        shared = a_sh & {tuple(b[i:i + SHINGLE]) for i in range(len(b) - SHINGLE + 1)}
        if jac >= JACCARD_NEAR or shared:
            findings.append({
                "kind": "NEAR",
                "source": label,
                "evaluation_text": s,
                "jaccard": round(jac, 3),
                "shared_5_token_sequences": sorted(" ".join(x) for x in shared),
            })
    return findings


def banned_terms(text: str) -> list[str]:
    toks = set(tokens(text))
    hits = [t for t in BANNED_TOKENS if t in toks]
    hits += [p for p in BANNED_PATTERNS if re.search(p, text.casefold())]
    return hits


# --- schema neutrality ----------------------------------------------------------------------------


def schema_findings(schema: dict) -> list[str]:
    problems: list[str] = []

    def walk(node: object, path: str, name: str | None) -> None:
        if not isinstance(node, dict):
            problems.append(f"{path}: schema node is not an object")
            return
        extra = sorted(set(node) - STRUCTURAL_KEYWORDS)
        if extra:
            problems.append(f"{path}: non-structural keyword(s) {extra}")
        if "anyOf" in node and node["anyOf"] != NULLABLE_STRING:
            problems.append(f"{path}: anyOf is allowed only as nullable string")
        if "enum" in node:
            if name not in VOCABULARIES:
                problems.append(f"{path}: enum on a field without a locked vocabulary")
            elif node["enum"] != VOCABULARIES[name]:
                problems.append(f"{path}: enum is not exactly the full locked vocabulary for {name}")
        if node.get("type") == "object":
            props = node.get("properties", {})
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            if sorted(node.get("required", [])) != sorted(props):
                problems.append(f"{path}: required must list exactly the object's keys")
            for key, child in props.items():
                walk(child, f"{path}.{key}", key)
        if "items" in node:
            walk(node["items"], f"{path}[]", None)

    walk(schema, "$", None)
    return problems


def _flat(text: str) -> str:
    return " ".join(text.replace("`", "").replace("**", "").replace("*", "").split())


# --- run order ------------------------------------------------------------------------------------


def order_key(arm: str, unit_id: str, repeat_number: int, prompt_bundle_sha256: str, schema_sha256: str) -> str:
    text = RUN_ORDER_SEPARATOR.join(
        [RUN_ORDER_ALGORITHM, arm, unit_id, str(repeat_number), prompt_bundle_sha256, schema_sha256])
    return sha(text)


def materialize_run_order(configuration_id: str, run_kind: str, slots: list[tuple[str, str, int]],
                          prompt_bundle_sha256: str, schema_sha256: str) -> list[dict]:
    rows = []
    for arm, unit_id, repeat_number in slots:
        slot_id = "|".join([configuration_id, run_kind, arm, unit_id, str(repeat_number)])
        rows.append({
            "run_slot_id": slot_id,
            "run_id": sha(slot_id)[:32],
            "arm": arm,
            "unit_id": unit_id,
            "repeat_number": repeat_number,
            "order_key_sha256": order_key(arm, unit_id, repeat_number, prompt_bundle_sha256, schema_sha256),
        })
    rows.sort(key=lambda r: (r["order_key_sha256"], r["run_slot_id"].encode("utf-8")))
    for i, r in enumerate(rows, start=1):
        r["order_index"] = i
    return rows


def pilot_slots() -> list[tuple[str, str, int]]:
    """Locked pilot: Arm G only, every spec scenario, repeats 1 and 2 (design section 9.4)."""
    return [("G", s["id"], r) for s in _load(SPEC)["scenarios"] for r in (1, 2)]


# --- main -----------------------------------------------------------------------------------------


def main() -> int:
    failures: list[str] = []

    def report(ok: bool, line: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {line}")

    f = _load(FROZEN)
    m = _load(MANIFEST)
    p = f["prompt"]
    cfg = f["frozen_configuration"]
    texts = {"system_prompt": p["system_prompt"], "arm_g_user_template": p["arm_g_user_template"],
             "arm_a_user_template": p["arm_a_user_template"]}

    # 1. status
    n = len(failures)
    for rec, name in ((f, "frozen record"), (m, "freeze manifest")):
        if rec["status"] != STATUS or rec["pre_run_status"] != PRE_RUN or rec["pilot_readiness"] is not False:
            failures.append(f"status: {name}")
        if any(v not in (False, 0) for v in rec["execution_record"].values()):
            failures.append(f"status: {name} shows execution")
        if rec["governing_limitation"]["resolved"] is not False:
            failures.append(f"status: {name} marks E3-E-SEM-001 resolved")
    if f["frozen"] is not True:
        failures.append("status: frozen flag")
    report(len(failures) == n, f"status {STATUS}; O-3 {f['pre_run_status']['O-3']}, O-5 {f['pre_run_status']['O-5']}, O-6 {f['pre_run_status']['O-6']}; pilot_readiness false; no execution; E3-E-SEM-001 unresolved")

    # 2. hashes
    n = len(failures)
    for key, value in texts.items():
        if sha(value) != p[f"{key}_sha256"]:
            failures.append(f"hash: {key}")
    bundle = sha(canon(texts))
    schema_sha = sha(canon(f["output_format_json_schema"]))
    config_sha = sha(canon(cfg))
    if bundle != p["prompt_bundle_sha256"] or bundle != cfg["prompt"]["prompt_bundle_sha256"]:
        failures.append("hash: prompt bundle")
    if schema_sha != f["output_format_json_schema_sha256"] or schema_sha != cfg["structured_output"]["schema_sha256"]:
        failures.append("hash: output format schema")
    if config_sha != f["configuration_sha256"]:
        failures.append("hash: frozen configuration")
    report(len(failures) == n, f"hashes recomputed: bundle {bundle[:12]}, schema {schema_sha[:12]}, configuration {config_sha[:12]}")

    # 3. manifest bindings and locked inputs
    n = len(failures)
    bindings = {
        "frozen_record_sha256": file_sha(FROZEN), "review_sha256": file_sha(REVIEW),
        "human_reference_issue_sha256": file_sha(ISSUE), "verifier_sha256": file_sha(Path(__file__).resolve()),
        "configuration_sha256": config_sha, "prompt_bundle_sha256": bundle, "schema_sha256": schema_sha,
        "pilot_run_order_sha256": f["run_order"]["pilot_run_order_sha256"],
    }
    for key, value in bindings.items():
        if m[key] != value:
            failures.append(f"manifest binding: {key}")
    changed = [rel for rel, h in m["locked_inputs_sha256"].items() if file_sha(REPO / rel) != h]
    failures += [f"locked input changed since freeze: {rel}" for rel in changed]
    report(len(failures) == n, f"freeze manifest bindings ({len(bindings)}) and locked inputs unchanged ({len(m['locked_inputs_sha256']) - len(changed)}/{len(m['locked_inputs_sha256'])})")

    # 4. review copies
    n = len(failures)
    blocks = [b.split("\n```", 1)[0] for b in REVIEW.read_text(encoding="utf-8").split("```text\n")[1:]]
    if blocks != list(texts.values()):
        failures.append("review: prompt copies differ from the frozen record")
    report(len(failures) == n, f"review prompt copies byte-identical ({len(blocks)} blocks)")

    # 5. thinking instruction
    n = len(failures)
    system = p["system_prompt"]
    if system.count(THINKING_SENTENCE) != 1 or (THINKING_SENTENCE + " " + JSON_INSTRUCTION) not in system:
        failures.append("thinking instruction: not exactly once immediately before the JSON instruction")
    seg_keys = set(f["output_format_json_schema"]["properties"]["artifacts"]["items"]["properties"]["segments"]["items"]["properties"])
    top_keys = set(f["output_format_json_schema"]["properties"])
    if any(re.search("reason|think|thought|rationale", k) for k in seg_keys | top_keys):
        failures.append("thinking instruction: schema contains a reasoning field")
    if re.search(r"(?i)(explain|show|reveal|describe) (your )?(reasoning|thinking)", system + p["arm_g_user_template"] + p["arm_a_user_template"]):
        failures.append("thinking instruction: prompt asks the model to reveal reasoning")
    report(len(failures) == n, "thinking instruction once, immediately before the JSON instruction; no reasoning field; no request to reveal reasoning")

    # 6. schema neutrality
    problems = schema_findings(f["output_format_json_schema"])
    failures += [f"schema: {x}" for x in problems]
    report(not problems, f"schema semantic neutrality: keywords {sorted(STRUCTURAL_KEYWORDS)} only; full locked vocabularies; no cross-field rule")
    for x in problems:
        print(f"      {x}")

    # 7. normative definitions verbatim
    protocol = _flat(PROTOCOL.read_text(encoding="utf-8"))
    prompt_flat = _flat(system + "\n" + p["arm_g_user_template"])
    missing = [s for s in p["normative_definitions_verbatim"] if _flat(s) not in protocol or _flat(s) not in prompt_flat]
    failures += [f"normative text not verbatim: {s}" for s in missing]
    report(not missing, f"protocol normative definitions verbatim in prompt: {len(p['normative_definitions_verbatim']) - len(missing)}/{len(p['normative_definitions_verbatim'])}")

    # 8. contamination
    corpus = evaluation_corpus()
    n = len(failures)
    example_findings = 0
    for ex in p["worked_examples"]:
        if ex["text"] not in system:
            failures.append(f"worked example not in system prompt: {ex['id']}")
        hits = banned_terms(ex["text"])
        found = [x for s in sentences(ex["text"]) for x in compare(s, corpus)]
        example_findings += len(found) + len(hits)
        if hits or found:
            failures.append(f"worked example contaminated: {ex['id']}")
    banned_any = {k: banned_terms(v) for k, v in texts.items() if banned_terms(v)}
    if banned_any:
        failures.append(f"banned terms in prompt: {banned_any}")
    allow = {a["prompt_sentence"] for a in p["prompt_scan_allowlist"]}
    unexplained, allowed = [], 0
    for key, value in texts.items():
        for line in value.splitlines():
            for s in sentences(line):
                if compare(s, corpus):
                    if s in allow:
                        allowed += 1
                    else:
                        unexplained.append(s)
    failures += [f"prompt sentence near evaluation prose: {s}" for s in unexplained]
    report(len(failures) == n, f"contamination: {len(p['worked_examples'])} worked examples vs {len(corpus)} evaluation sentences, {example_findings} findings; banned terms {banned_any or 'none'}; whole-prompt scan {allowed} allowlisted, {len(unexplained)} unexplained")
    for s in unexplained:
        print(f"      unexplained: {s}")

    # 9. pilot run order
    n = len(failures)
    ro = f["run_order"]
    rows = materialize_run_order(cfg["experiment_configuration_id"], "PILOT", pilot_slots(), bundle, schema_sha)
    if ro["algorithm"] != RUN_ORDER_ALGORITHM or ro["pilot_run_order"] != rows:
        failures.append("run order: pilot list does not match E3F_RUN_ORDER_V1")
    if sha(canon(rows)) != ro["pilot_run_order_sha256"] or len(rows) != 44:
        failures.append("run order: pilot list hash or size")
    report(len(failures) == n, f"pilot run order {RUN_ORDER_ALGORITHM}: {len(rows)} slots, sha256 {sha(canon(rows))[:12]}")

    # 10. human-reference issue
    n = len(failures)
    issue = ISSUE.read_text(encoding="utf-8")
    if f"`{ISSUE_STATUS}`" not in issue or m["human_reference_issue_status"] != ISSUE_STATUS:
        failures.append("issue record: status missing")
    report(len(failures) == n, f"human-reference contamination issue recorded: {ISSUE_STATUS}")

    print(f"\nRESULT {'FAIL' if failures else 'PASS'}: {len(failures)} failure(s). Offline document verification; no model, credential, or network used.")
    for x in failures:
        print(f"  - {x}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
