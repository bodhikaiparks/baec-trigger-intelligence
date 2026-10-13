# BAEC Engine 2: Technical and Research Brief

**Project:** BAEC Trigger Intelligence
**Engine:** BAEC Engine 2: Monitoring + Correspondence
**Status:** Release candidate of an offline research prototype. Synthetic data only. Not tagged.
**Governing sources:** the manuscript, then `docs/RESEARCH_CONTRACT.md`, then the locked Stage A contract (`docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md`), then code. If this brief conflicts with any of them, they win.

> **Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.**

---

## 1. Purpose

Two engines answer two different questions.

- **Engine 1:** What did the buyer say would make evaluation worthwhile, and can that condition be verified and confirmed?
- **Engine 2:** Has new evidence appeared that may correspond to that verified buyer-defined condition?

Engine 2 begins only after Engine 1 has confirmed a BAEC. It watches authorized sources for evidence, prepares that evidence for a person, and records that person's judgment. It does not rank or rate accounts, predict purchases, or contact anyone.

Every concept in Engine 2 is an implementation of a managerial implication proposed in an unpublished conceptual working paper. Nothing here is a research finding.

## 2. Core architecture

| Concept | What it is | What it is not |
|---|---|---|
| Confirmed BAEC handoff | A read-only, point-in-time copy of one confirmed Engine 1 BAEC (Stage K) | A new BAEC, a re-confirmation, or a live link to Engine 1 |
| Monitoring Plan | A human-authorized plan for one BAEC: required and not-applicable dimensions, authorized sources, plan facts and rules. Each version is immutable | Evidence that the condition occurred |
| Activation | The plan becomes active only when Engine 1 reports the BAEC `CURRENT` | A judgment of currentness by Engine 2 |
| Authorized Source | A source a human approved for one plan, with its basis and limitations | Permission to contact anyone |
| Observation | Exact captured source text with provenance and a content hash | An interpretation; it is never edited |
| Supersession and retraction | A later Observation that corrects or withdraws an earlier one, on a recorded basis | Deletion: the earlier Observation stays in the record, no longer usable |
| Derived Measurement | A value computed with exact decimal arithmetic from Observations | Usable once any input is superseded |
| Signal Candidate | Observations selected as possibly relevant | A finding |
| Correspondence Candidate | A hypothesis that signals may bear on the BAEC's condition and warrant human review | An opportunity, intent, or a lead |
| Human Correspondence Review | A person's finding on each of 11 dimensions, 4 cross-cutting checks, sufficiency, and a BAEC revalidation flag | An account-state change or an authorization to act |
| Correspondence classification | A deterministic rule that derives the outcome from the human review | Something a caller can choose |
| Advisory AI (optional) | A validated AI proposal, kept in a separate audit store | A human finding or an outcome |
| Persistence | Two separate append-only SQLite stores: `BAEC_ENGINE_2` (v1, 30 tables) and `BAEC_ENGINE_2_AI` (v1, 7 tables) | A place where records are edited or repaired |
| Operator workflow | A Streamlit research prototype over the review application layer (Stages I and J) | A production interface |

## 3. Authority model

| Record | Who is authoritative | Authority it carries |
|---|---|---|
| Engine 1 buyer evidence | Engine 1 | Exact buyer text with provenance |
| Engine 1 confirmed BAEC | Engine 1, through a human confirmation path | That the condition meets the BAEC criteria, as confirmed by a human |
| Engine 2 Observation | Engine 2 capture under an active plan | That this exact text was captured from an authorized source |
| Signal Candidate | Engine 2 selection | None beyond "possibly relevant" |
| Correspondence Candidate | Engine 2 | None beyond "warrants human review" |
| AI advisory proposal | None | Advisory only, origin `AI_INFERENCE` |
| Human Correspondence Review | The human reviewer | The human's findings on this evidence |
| Correspondence outcome | The locked classifier, from the human review | A narrow correspondence result |

**AI output is advisory and cannot create a human finding or authoritative correspondence outcome.**

## 4. Outcome vocabulary

The locked outcomes are:

- `NO_CORRESPONDENCE`: at least one required dimension was found contradicted, so the evidence does not correspond under the locked rule.
- `POSSIBLE_CORRESPONDENCE`: some required dimension remains unresolved, or the evidence was not judged sufficient, so correspondence is not verified.
- `HUMAN_VERIFIED_CORRESPONDENCE`: every required dimension was found supported, the evidence was judged sufficient, and no BAEC revalidation was flagged.

`BAEC_REVALIDATION_REQUIRED` is a flag, not an outcome; it blocks `HUMAN_VERIFIED_CORRESPONDENCE`.

None of these outcomes measures buyer intent, likelihood, or readiness, and none ranks an account.

## 5. Critical boundary

Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.

- A confirmed BAEC does not automatically create an active opportunity.
- A `HUMAN_VERIFIED_CORRESPONDENCE` outcome does not automatically create an active opportunity.
- No Engine 2 operation changes account state, contacts a buyer, writes to a CRM, or starts any seller action.

## 6. Engine 1 to Engine 2 handoff (Stage K)

- **Read-only.** The handoff reads Engine 1 only through Engine 1's query-only connection and the locked Engine 1 repository, which rebuilds the record through the Engine 1 domain and re-verifies it. It writes nothing, consumes no grant, and reruns no confirmation.
- **Eligibility.** Exactly Engine 1's own: a persisted `CONFIRMED_BAEC` record carrying its human confirmation, with a stored staleness status. An AI draft, an accepted review, or an unexecuted grant creates no such record and cannot cross.
- **Exact buyer statement.** The Engine 2 condition reference is the buyer's exact statement, never a paraphrase. A confirmed BAEC without one is refused for monitoring.
- **Threshold and timing.** A stated number with its unit becomes the plan threshold exactly. A number without a unit is refused rather than given an invented unit. Qualitative or unstated stringency yields no numeric threshold; the verbatim wording stays in the handoff. Timing is the buyer's own timing text.
- **Currentness.** The stored Engine 1 staleness status is passed to Engine 2's locked activation rule, which activates only a `CURRENT` BAEC.
- **Point in time.** The handoff is a snapshot. There is no synchronization: a later Engine 1 change is seen only by a new read.
- **Not carried.** AI-derived normalized wording, and the Phase 7 human-reviewed normalized wording held in Engine 1's proposal-bridge review store, do not cross.

## 7. AI boundary (Stages F, G, and H-A)

- **Stage F.** A model receives a curated, canonical input built from one review context: the buyer's exact statement, the plan, and the evidence with its usability. It may answer only in a proposal vocabulary (`PROPOSED_SUPPORTED`, `PROPOSED_CONTRADICTED`, `PROPOSED_UNRESOLVED`, `PROPOSED_NOT_APPLICABLE`), and its output is validated strictly and rejected on any defect. A proposal has no outcome, no sufficiency, and no authoritative finding.
- **Stage G.** Inputs, attempts, exact responses, validations, and accepted proposals are stored in the separate `BAEC_ENGINE_2_AI` store, never in the authoritative store.
- **Stage I.** An accepted proposal is shown only on explicit request, only when its stored input is exactly the input built from the review packet, and only beside the human workflow, never inside it.
- **Stage H-A.** A controlled live harness exists for one pinned model, one fixed synthetic input, and one attempt, behind explicit gates. It is opt-in and deselected by default.
- **Stage H-B has not been executed.** No real model has been run against Engine 2, and no claim is made about any model's behavior on it.

## 8. Operator workflow (Stage J)

The Streamlit page (`streamlit run streamlit_engine2_app.py`) walks one fixed synthetic context: overview, monitoring context, evidence, candidate, review packet, human review, result. Current and historical evidence are shown separately. The review packet carries a context token; a review is refused if the stored record changed after the packet was opened. Every judgment starts unselected and must be answered; no control submits an outcome. Drafts are kept for the session only, bound to their exact review context, and never transferred to a changed context. Reviewer identity is self-asserted in this research prototype.

## 9. Limitations

- The conceptual basis is an unpublished working paper that has not been peer reviewed.
- All demonstration and test data is synthetic.
- Corpus results and test counts are software verification, not empirical validation of the BAEC construct or of Engine 2.
- There is no evidence that correspondence predicts revenue, purchases, or conversion.
- Buyer conditions can become stale; Engine 2 relies on Engine 1's stored staleness status and does not judge currentness itself.
- The Engine 1 to Engine 2 handoff is point in time and not continuously synchronized.
- There is no automatic outreach, no CRM automation, and no automatic account-state transition.
- The advisory AI path has not been qualified by a live run (Stage H-B).
- Reviewer and operator identities are self-asserted; nothing authenticates a person.
- This is a research prototype. No claim of production readiness or production security is made.

## 10. Reproducing the release candidate

- `python3 scripts/engine2_release_demo.py` runs the deterministic Engine 1 to Engine 2 demonstration and prints its summary.
- `python3 scripts/verify_engine2_release.py` verifies identity, integrity, schemas, the demonstration, and the focused suites; `--full` adds the full suite.
- The exact values are recorded in `docs/engine2/ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json` and explained in `docs/engine2/ENGINE2_RELEASE_CANDIDATE_RECORD.md`.
