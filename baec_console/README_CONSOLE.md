# BAEC Console V1 — Trigger Intelligence

A local, offline product shell over the released BAEC Engines 1–3. A salesperson remembers what a buyer said
would make evaluation worthwhile, monitors for related evidence, recognizes possible correspondence through a
human review, and prepares a bounded re-engagement conversation for human use.

**Status:** local V1 for owner review. Not deployed. Synthetic data only.

## Launch

```
cd "/Users/bodhiparks/Downloads/baec-trigger-intelligence/baec_console"
../.venv/bin/streamlit run app.py
```

Then open http://localhost:8501. The first run creates `baec_console/.data/` (git-ignored); no manual setup is
needed. Run it from the `baec_console` directory so `baec_console/.streamlit/config.toml` applies: it keeps the
server on `localhost`, turns off usage statistics, and stops Streamlit's startup banner from looking up the
machine's public IP address. Set `BAEC_CONSOLE_DATA_DIR` to keep data somewhere else.

To try it immediately, use **Load demo: start from beginning** (Alder Ridge Clinics, with only the buyer
interaction recorded) or **Load demo: completed example** in the sidebar.

## Tests

```
cd "/Users/bodhiparks/Downloads/baec-trigger-intelligence"
.venv/bin/python -m pytest -q baec_console/tests
```

These are software tests on synthetic data. They are not evidence for the BAEC theory.

## Workflow

| Step | What the person does | What decides |
|---|---|---|
| Remember | Records a buyer interaction, captures the condition in a structured form, previews it, confirms it | Engine 1 classifier and human confirmation gate |
| Monitor | Authorizes a monitoring plan and its sources, adds observations and optional exact measurements | Engine 2 activation rule (Engine 1 currentness) |
| Recognize | Answers each required correspondence check and the sufficiency question | Engine 2 outcome rule, derived from the human findings |
| Prepare | Requests preparation with disclosure decisions, reviews each draft: accept, edit, or reject | Engine 3 eligibility and validator |

Nothing contacts the buyer. There is no send, schedule, mailbox, or CRM action anywhere in the console.

## Architecture

```
baec_console/
  app.py                       Streamlit shell and navigation
  ui_pages.py                  Dashboard, Accounts, Review Queue, Preparation, Activity
  ui_workspace.py              Account Workspace (Remember, Monitor, Recognize, Prepare, Timeline, Account state)
  ui_common.py, styles.py      shared UI helpers and styling
  console_service.py           product operations for one workspace; no Streamlit dependency
  engine1_adapter.py           form -> Engine 1 objects; HumanCommandFacade request/approve/redeem
  engine2_adapter.py           Stage K handoff, plans, observations, measurements, human review
  engine3_adapter.py           eligibility from stored records, grounding, validate_case
  deterministic_preparation.py DETERMINISTIC_CONSOLE_TEMPLATE_V1
  console_store.py             console-owned SQLite store (append-only preparation records)
  demo_seed.py                 synthetic Alder Ridge / Cobalt seed
```

### Storage

Each workspace is a directory with three SQLite files: the released Engine 1 schema, the released Engine 2
store, and a console store. Two workspaces exist: `local` (accounts you create) and `synthetic` (demo data).
Engine 1 and Engine 2 are append-only with no delete path, so synthetic data lives in separate files and
**Reset synthetic data** removes only those files. Your accounts are never touched.

The console store holds only what the engines do not: account metadata, the structured breakdown of each
interaction, unconfirmed condition drafts, console events, and Engine 3 preparation records (Engine 3 has no
persistence of its own). Preparation records and events are append-only, enforced by SQLite triggers.

## Deterministic preparation

V1 has no live model. `DETERMINISTIC_CONSOLE_TEMPLATE_V1` fills fixed sentence patterns from recorded values and
the salesperson's own paraphrase, declares each sentence's claim type and grounding, and submits every output
unchanged to `baec_app.engine3.validate_case`. A refusal is shown with its reasons and is never repaired. The
template is a local product convenience, not a research result and not an AI generator. The console labels its
output "Prepared by BAEC · Deterministic preparation".

### Provenance (V1 compatibility limitation)

Engine 3 V1 knows only two artifact origins, `AI_INFERENCE` and `HUMAN_EDITED`, and the console does not change
that locked enum. Template output is machine-produced and not human-written, so it is submitted to the validator
under `AI_INFERENCE`, the existing validator-compatible value. That value does not mean a model generated it.
Each preparation stores its provenance separately, in a table whose constraints refuse any other value:

| Field | Value |
|---|---|
| `console_generator_origin` | `DETERMINISTIC_CONSOLE_TEMPLATE_V1` |
| `engine3_validator_origin` | `AI_INFERENCE` (compatibility value) |
| `origin_compatibility_note` | `E3-ORIGIN-COMPAT-V1`: AI_INFERENCE is the existing Engine 3 validator-compatible origin value for machine-produced drafts. No model was used to generate this preparation. |
| `model_used` | always `0` |

A human edit carries its own provenance (`HUMAN_EDIT`, validated as `HUMAN_EDITED`, with the editor and the
parent draft) and never changes the original preparation's record. The main UI says "Prepared by BAEC ·
Deterministic preparation"; these fields appear under Technical details.

## Preparing from a specific correspondence

Preparation is bound to one human-verified correspondence, chosen explicitly. The Prepare section lists every
verified correspondence (newest shown first, for display only); the salesperson prepares from one with
**Prepare from this correspondence**. The Preparation Request stores the exact Engine 2 records it is bound to:
the review id (`HCR-...`), its correspondence candidate, the monitoring plan and version, and the activation. Each
preparation keeps that review id permanently in its provenance record, and the timeline names the correspondence
a request came from.

The console never chooses a review by recency. Engine 2 records no supersession of a review, so a later review of
the same evidence does not replace the bound one; it is shown to the salesperson as a warning instead. If the
bound review stops satisfying Engine 3 eligibility (its evidence is corrected, the condition is no longer current,
revalidation is flagged, or the records change), Engine 3 refuses and nothing is generated. Possible and no
correspondence can never be selected.

## Blocking preparation

**Block preparation for this correspondence** records a human decision that nothing may be drafted from that
correspondence. It uses Engine 3's existing `human_preparation_block` input, so Engine 3 itself refuses
(`E3C-IN-007 HUMAN_PREPARATION_BLOCK`; if a request already existed, Engine 3 refuses it as a changed context).
Decisions are append-only: lifting a block is a new, separate decision, and both stay in the timeline. A block
applies only to the correspondence it was placed on.

## Boundaries the console keeps visible

- Correspondence is not intent. Monitoring is not buyer evaluation.
- A buyer condition is not an active opportunity; account state changes only through Engine 1's separate,
  human-authorized transitions.
- A Preparation Request authorizes drafting only, never outreach.
- Engine output is not a human decision; every human step records who acted.
- BAEC prepares the conversation. The salesperson decides whether to have it.

The actor name in the sidebar is self-asserted. It is recorded on every human action; it is not authentication.

## Implementation choices (not research findings)

- Evidence excerpts must be copied exactly from the interaction; text inside the buyer's statement is recorded
  as a buyer fact, text in the seller notes as a seller observation.
- When Engine 3 refuses preparation for a request (eligibility, a human block, or a changed context), nothing is
  generated; the refusal is recorded as an event.
- The console maps the human action "Not enough evidence" to the complete Engine 2 review vocabulary required by
  the existing engine. Unanswered checks are recorded as Unresolved and an unanswered sufficiency decision as No.
  This is a console V1 mapping rule, not an Engine 2 research claim.
- The arithmetic check shown during review is display-only; the human's findings decide.
- One monitoring plan per confirmed condition in V1.

## V1 limitations (kept, not fixed by changing the engines)

- Engine 1 has no product path to make a confirmed condition stale, so confirmed conditions stay current.
- Engine 3 has no native deterministic-template origin (see Provenance above).
- Engine 3 has no native persistence; preparation records live in the console store.
- The typed actor name is not authentication.
