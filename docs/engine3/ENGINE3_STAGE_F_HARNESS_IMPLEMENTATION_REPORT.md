# BAEC Engine 3: Stage F Experimental Harness Implementation Report

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F (experimental harness, offline)\
**Status:** LOCKED_OFFLINE_HARNESS_NOT_EXECUTED (lock date 2026-10-14). Owner approved the architecture; the zero-denominator reporting correction (section 9) was applied before the lock. No live model has been called. No pilot or main experiment has been run. No experiment result exists.\
**Date:** October 14, 2026\
**Base commit:** `c81d00343d96683514dd38f43d11a6ebae6d104e` (Stage F design, `LOCKED_DESIGN_NO_LIVE_EXECUTION`)\
**Manifest:** `ENGINE3_STAGE_F_HARNESS_MANIFEST.json`; verifier: `scripts/verify_engine3_stage_f_harness.py`

Claim boundary:

> The Stage F harness is locked offline and tested only with handwritten synthetic dry-run inputs. It has not been executed against any model. It does not resolve E3-E-SEM-001, validates no model, and authorizes no live run, pilot, or outreach. Not evidence of semantic validation, purchase prediction, sales lift, production security, or BAEC theory.

Every rule in the harness is an IMPLEMENTATION rule (`docs/RESEARCH_CONTRACT.md` §1). The tests are software tests over synthetic inputs. They are not evidence about any model, buyer, sales outcome, or the theory.

---

## 1. Execution record

| Item | Value |
|---|---|
| `live_model_executed` | false |
| `api_key_used` | false |
| `external_model_sdk_used` | false |
| `network_calls` | 0 |
| `pilot_executed` | false |
| `main_experiment_executed` | false |
| E3-E-SEM-001 `MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE` | **not resolved** |
| O-3, O-5, O-6 | **PRE_RUN_REQUIRED** (no value supplied) |

Implementing the harness does not show that any model annotates reliably. It only makes the locked experiment executable later, after separate approval.

## 2. Locked design preserved

No Stage A to F locked artifact was modified. The harness verifier checks this by hash.

- Arm G: 22 scenarios × 10 repeats = 220 proposals. Pilot: 22 × 2 = 44, operational only, never pooled, never used by any rule. Arm A: 48 fixed-prose artifacts × 5 repeats = 240 runs. The run binding refuses repeat indexes above these counts and refuses an Arm A pilot, which the design does not define.
- The eleven model-controlled fields are evaluated exactly. `origin` (fixed `AI_INFERENCE`) and `audience` (derived from artifact type) are not model authority.
- Decision rules A to G, their thresholds, and their arm scopes are copied exactly and tested against the locked spec and design manifest.
- The design spec's design-time fields (for example `authorization.harness_implemented: false`) are locked and are not amended. This report and the harness manifest are the record of the harness.

## 3. Location and boundaries

The harness lives in `experiments/engine3_stage_f/`, outside the importable `baec_app` production package, as the locked spec requires (`live_model_boundary.harness_location`). No production module imports it. It reuses, unchanged:

- Stage D `validate_case`;
- Stage E public primitive `parse_artifact` and `ProposedArtifact`;
- Stage E `EvaluationContext` and its context digest.

It does not use the Stage E loader `load_prerecorded_preparation_proposal` or its `PRERECORDED_SYNTHETIC` provenance. No Stage D or Stage E file was changed.

The harness has no model client, network access, credential read, subprocess, file I/O, persistence, UI, email, call, CRM, meeting, account-state, Engine 1, or Engine 2 path.

## 4. Architecture

| Module | Role |
|---|---|
| `envelope.py` | Experimental proposal envelope. `RunBinding` (configuration id, arm, scenario id, repeat index, run kind, bound context digest) is supplied by the harness, never by model output. The model output may contain exactly `schema_version`, `requested_action`, `requests_score`, and `artifacts`. Artifacts and segments are parsed by the Stage E `parse_artifact`. Strict JSON (no duplicate keys, no NaN). `capture_run` keeps the exact raw bytes, their SHA-256, and the parse outcome (rejection class or proposal digest). Arm A output must reproduce the fixed prose exactly. |
| `contexts.py` | Applies one locked spec delta (add one grounding item, or change one disposition) to a base context. |
| `annotation.py` | Blinded annotation packets; human reference records (primary A, primary B, adjudication); final reference; pre-adjudication primary agreement. |
| `comparison.py` | Counterfactual replay through unchanged Stage D; comparison categories; field disagreements and direction; reason-code transition and issue difference; single-field attribution; proposal-level false pass and false refusal. |
| `decision_rules.py` | Pure calculation of rules A to G over result tables. |
| `readiness.py` | Validation of O-3, O-5, O-6 before any pilot. |

**Provenance.** Provenance is derived from the run kind and cannot be supplied:

| Run kind | Experiment provenance |
|---|---|
| `PILOT`, `FULL_RUN` | `LIVE_SYNTHETIC_EXPERIMENT` |
| `DRY_RUN` | `OFFLINE_HANDWRITTEN_DRY_RUN` |

No run kind maps to `PRERECORDED_SYNTHETIC`, so live output can never be labelled prerecorded. Handwritten dry-run inputs can never be labelled live. Both values are experiment metadata, not Engine 3 domain states.

## 5. Dry-run inputs and oracle

`tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_INPUTS.json` holds **12** handwritten synthetic cases (11 Arm G, 1 Arm A). No model produced any of them. `ENGINE3_STAGE_F_DRY_RUN_ORACLE.json` holds the expected results, derived by hand from the locked Stage D rules, in a separate file. Harness code references neither file; the static audit and the verifier check this.

| Case | Purpose | Expected comparison |
|---|---|---|
| F-DRY-01 | Safe prose, accurate labels | CONCORDANT_PASS |
| F-DRY-02 | Unsafe prose (current-evaluation overclaim), accurate labels | CONCORDANT_REFUSAL_SAME_PRIMARY (`E3C-CLM-001`), H-unacceptable |
| F-DRY-03 | Safe prose, wrong labels, no outcome change | CONCORDANT_PASS with permissive and neutral disagreements |
| F-DRY-04 | Unsafe prose (switch intent), misleading labels | FALSE_PASS; `claim_type` and `semantic_class` individually sufficient |
| F-DRY-05 | Safe prose (courtesy), misleading labels | FALSE_REFUSAL; `claim_type` individually sufficient |
| F-DRY-06 | Structurally invalid (proposal carries a disclosure override) | Rejected, `INVALID_STRUCTURE` |
| F-DRY-07 | Unscorable segment (assertion plus question) | Not reference-complete; `D_model` reported alone |
| F-DRY-08 | Formatting variant 9.00% → 9% | CONCORDANT_REFUSAL_SAME_PRIMARY (`E3C-CLM-008`), human-safe: Stage D strictness, not model error; also a mixed-reference-modes segment |
| F-DRY-09 | Semantic paraphrase "more than 8%" → "above 8%"; primaries disagree, adjudicated | FALSE_REFUSAL (`E3C-DISC-005` → allowed); `reference_use_mode` sufficient |
| F-DRY-10 | Prompt-injection-like source content; two artifacts | Summary CONCORDANT_PASS; email FALSE_PASS (`E3C-DISC-001`); proposal-level false pass |
| F-DRY-11 | Stale-context control | `E3C-CTX-006`; annotation-insensitive, excluded from categories |
| F-DRY-12 | Arm A annotation of fixed prose | CONCORDANT_PASS |

Annotator aliases in the inputs are test placeholders (`DRY-RUN-PLACEHOLDER-*`). They are not O-3 staffing.

## 6. Blinded annotation packets

`build_annotation_packet(artifact, context, preparation_goal, channel_scope)` takes nothing else, so run identity, arm, repeat, model identity, and Stage D results cannot reach it. A packet contains exactly:

- rendered text;
- ordered segments: opaque packet segment id (`PS-n`), ordinal, exact text;
- artifact type and derived audience;
- every grounding item with its nine human disclosure fields, marked as untrusted quoted data;
- Preparation Goal and channel scope (Arm G only);
- the annotation guide: the existing Engine 3 vocabularies for the eleven fields, the Stage F specificity convention (0/10/90; 50 excluded), the reference-use convention, the declared-value convention, the experiment flags, the five policy attestations, and the statuses `SCORABLE`, `ADJUDICATION_REQUIRED`, `UNSCORABLE_SEGMENT_STRUCTURE`.

It never contains model annotations, model segment ids, any Stage D result or reason code, the scenario id or family, the arm, the repeat index, the configuration id, the context id (an opaque label is shown), or model identity. Tests check this for every dry-run packet and with planted canary values. The packet id is the deduplication key (same context, artifact type, segment boundaries, and texts). `annotator_order` gives a deterministic per-annotator order from a recorded seed.

## 7. Human reference records

`PrimaryAnnotation` (roles `PRIMARY_A`, `PRIMARY_B`), `Adjudication`, `SegmentAnnotation`, `ReferenceFields`, and `HumanPolicyAttestation` are strict, experiment-only records. Each carries alias, packet id, protocol version SHA-256, and an RFC 3339 UTC timestamp.

- A segment that is not `SCORABLE` receives no field values and no flags. Nothing is forced.
- `finalize_reference` applies the protocol section 8 triggers. Without an adjudication, a disputed segment stays `ADJUDICATION_REQUIRED` and its artifact is not reference-complete.
- Primaries must be two different people; the adjudicator must be neither. Records must cite only ids shown in the packet.
- An unscorable or unfinalized segment is never sent to Stage D. Only reference-complete artifacts are replayed.
- No record takes model output as input. A model never creates, edits, or initializes a reference record.

## 8. Comparison

For identical prose and context, `D_model` and `D_ref` are computed by unchanged `validate_case`. The case is built as Stage E builds it. `case_ref` differs only in the eleven fields. Nothing is normalized.

- **FALSE PASS:** annotation-sensitive, reference-complete, `D_model` allowed, `D_ref` refused.
- **FALSE REFUSAL:** annotation-sensitive, reference-complete, `D_model` refused, `D_ref` allowed.
- Proposal level as defined in design sections 8.3 and 8.4.
- Annotation-insensitive artifacts (`E3C-CTX-*`, `E3C-IN-*` under `D_model`) receive no comparison category.
- Also reported per artifact: the reason-code transition, the exact issue-tuple difference in both directions, per-segment field disagreements with direction, single-field attribution (individually sufficient fields, `JOINT_ONLY`, contributing fields), H human-safe status, unscorable and unfinalized ordinals, `formatting_variant` and `mixed_reference_modes` ordinals, and nonexistent grounding-id citations.

No composite score, rate, or accuracy figure is produced.

## 9. Decision rules

`decision_rules.py` computes A, B, B′, C, D, E, F, and G from result rows. Every result carries numerator and denominator.

- A, B, B′, E read Arm G only. Arm A cannot satisfy B.
- C and D are computed per arm, never pooled.
- F fires on a confirmed case in either arm.
- G is computed per field and per arm, never pooled to reach 30. `SINGLE_ANNOTATOR` mode fires G for every field.
- `INSUFFICIENT_INSTANCES` is reported for C and G when an arm has fewer than 30 instances for a field.
- Rules D and E report `NOT_EVALUABLE` when their eligible denominator is zero, with numerator 0, denominator 0, and rate `null`. A 0/0 result is never reported as 0% or as `NOT_FIRED`. A zero numerator over a positive denominator is `NOT_FIRED` with rate 0. The locked ≥10% (D) and ≥5% (E) thresholds are unchanged.
- Reporting statuses are `FIRED`, `NOT_FIRED`, `INSUFFICIENT_INSTANCES`, and `NOT_EVALUABLE`. They are experiment reporting statuses only, not Engine 3 states.
- Rule D's denominator is the human-safe, reference-complete artifacts of that arm. Rule E's denominator is all structurally valid Arm G artifacts, as locked in design section 11; context and eligibility refusals are excluded from its numerator only.
- Pilot and dry-run rows are refused.

Tests use only tiny synthetic tables. No experiment result has been computed.

## 10. Readiness gate

`check_pilot_readiness(staffing, configuration, supplement)` returns unresolved items unless:

- **O-3:** two distinct primary aliases and a separate adjudicator alias, frozen;
- **O-5:** configuration id, model identifier, model version, prompt SHA-256, a non-empty sampling configuration, and the harness output-schema version, frozen;
- **O-6:** exactly 16 Arm A supplement artifacts, each with a SHA-256, human-approved, frozen, with authors recorded, and with at least one primary annotator who is not an author.

With no inputs, all three are unresolved. The repository supplies no readiness record, and a test checks that no harness or script constructs one. Met preconditions do not authorize a pilot: `owner_approval_still_required` is always true, and no executor exists.

## 11. Static audit and verifier

`tests/engine3/test_engine3_stage_f_static_audit.py` is an AST audit of every harness module and of the harness verifier. It does not rely on the legacy Stage D regex test. Categories: model SDK import, network import, process or dynamic import, credential access, model or network call surface, outbound action definitions (and any `ActionAttempt` member other than `NONE`), Engine 1/2 coupling and write calls, test-fixture imports, fixture and oracle tokens, dynamic code, file I/O, and unlisted imports. It includes planted-violation self-tests for every category. The verifier may start only `sys.executable -m pytest`, without a shell.

`scripts/verify_engine3_stage_f_harness.py` is separate from the design verifier, which is unchanged. It checks the Stage F design artifacts and design verifier, the Stage A to E locked hashes, the harness manifest hashes and coverage, input and oracle separation, the no-execution record, that O-3, O-5, and O-6 remain `PRE_RUN_REQUIRED`, and then runs the harness tests.

## 12. Implementation choices

These are implementation choices, not research findings. None changes a locked threshold, field, or definition.

**Owner-approved (October 14, 2026):**

1. **Flag disagreement.** If the primaries agree on status and all eleven fields but disagree on `formatting_variant` or `mixed_reference_modes`, the segment requires adjudication.
2. **Rule C precedence.** Contribution to a confirmed false pass in an arm fires rule C for that arm even when the field has fewer than 30 instances, as locked. `INSUFFICIENT_INSTANCES` is reported only when neither condition fires.
3. **Dry-run provenance.** `OFFLINE_HANDWRITTEN_DRY_RUN` is experiment-only provenance. It can never be confused with `LIVE_SYNTHETIC_EXPERIMENT` or `PRERECORDED_SYNTHETIC` (section 4).

**Owner-corrected before lock:** zero denominators in rules D and E are `NOT_EVALUABLE` (section 9). The earlier `NOT_FIRED` with 0/0 behaviour was removed.

**Recorded, not separately reviewed:**

4. **Direction.** "Restrictive" is the mirror of each locked permissive rule. For id sets, a permissive omission takes precedence over a restrictive addition in the same segment.
5. **Output schema.** The model output carries no proposal id or provenance; the harness binding identifies the run. The schema version `engine3.stage_f.experimental_proposal_output.v1` must be frozen as part of O-5.
6. **Adjudication reason.** One reason per adjudicated segment, not one per changed field.
7. **Reference consistency.** Records enforce protocol rules: `claim_field` is `NONE` exactly for `QUESTION` and `COURTESY_OR_TRANSITION`; `declared_value` only for specific-value fields; `question_has_premise` only for questions; `INTERNAL` exactly for internal artifacts; specificity in {0, 10, 90}.
8. **Packet context.** Packets show the evaluation context. The stale-context control is judged against the current context.
9. **Confirmed.** A false pass counts as confirmed when its artifact is reference-complete, which requires final values after adjudication.
10. **Rule F.** Requires a final H record.

## 13. Not implemented (planned, requires separate approval)

- Any model adapter, prompt, or live loader call path.
- Storage for `raw_runs`, `annotation_packets`, `human_reference`, and `comparisons`. Locations remain undecided; records exist in memory only.
- Annotation tooling and the adjudicator's view.
- Aggregate reporting of design outputs 1 to 23 (confusion matrices, transition tables, per-scenario variability). The harness provides the per-artifact and per-rule building blocks.
- Calibration sets and the Arm A supplement (O-6).

## 14. Test results

| Suite | Result |
|---|---|
| Stage F harness tests (`tests/engine3/test_engine3_stage_f_*.py`) | 253 passed |
| `tests/engine3` | 487 passed |
| Full repository (`pytest -q`) | 7284 passed, 2 deselected, 0 failed |
| Locked Engine 1/2 public tests | 183 passed |

These are software tests over synthetic inputs. They are not experiment results.

## 15. Stop-condition check

| Stop condition | Status |
|---|---|
| A locked Stage A to F artifact must change | Not triggered |
| The experiment cannot be represented with existing Stage C/D fields | Not triggered. Unrepresentable segments are `UNSCORABLE_SEGMENT_STRUCTURE`. |
| A live model, API key, or network access is needed | Not triggered |
| A locked test would have to be weakened | Not triggered |
| Model annotations treated as human reference | Not triggered. No reference record takes model output as input. |
| Harness acquires seller-action capability | Not triggered. The static audit checks this. |
| The harness implies E3-E-SEM-001 is resolved | Not triggered |
| Design thresholds need changing | Not triggered |
