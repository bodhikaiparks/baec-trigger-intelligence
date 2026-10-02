# Project Phases

**Project:** BAEC Trigger Intelligence
**Purpose:** Records how the repository's checkpoints are numbered, what each one contains, and how phase numbers should be read.

Phase numbers are implementation-planning labels for this software. They are not research stages and make no claim about the BAEC or CEE theory. The research authority is the manuscript, then `docs/RESEARCH_CONTRACT.md`, then code (see `CLAUDE.md`).

---

## 1. Checkpoints

| Phase | Name | Git tag | Commit | Status |
|---|---|---|---|---|
| Gate 1 / Phase 1 | Deterministic domain correctness | `gate-1-domain-correctness` | `b4deb78` | Complete |
| Phase 2 | — | none | none | Not materialized (see §3) |
| Phase 3 | Persistence and synthetic data | `phase-3-persistence` | `4063c23` | Complete |
| Phase 3 hardening | RC-33 schema guards | `phase-3-persistence-hardening` | the tagged commit | Complete |
| Phase 4 | Application & Human Authorization Boundary | none | none | Planned; design not yet approved |

Existing tags and commits are not renamed, renumbered, moved, or rewritten.

## 2. Gate 1 / Phase 1: deterministic domain correctness

Tag `gate-1-domain-correctness`, commit `b4deb78`.

Contents:

- `baec_app/domain/`: enums, frozen domain models, BAEC classification rules (`classify_candidate` and the record factories), and the account-state machine.
- `docs/RESEARCH_CONTRACT.md`, approved by the project owner for Phase 1.
- `docs/PHASE1_SMOKE_MIGRATION.md`: every exploratory smoke check from Phase 1, mapped to its pytest equivalent.
- Tests: 813 passing at this tag.

Out of scope at this checkpoint: persistence, AI or model calls, MCP, UI, monitoring, and signals.

"Gate 1" and "Phase 1" name the same checkpoint. The tag uses the gate name because this checkpoint was the domain-correctness gate that later phases build on.

## 3. Phase 2: not materialized

No separate Phase 2 checkpoint exists in the repository. Work that might have been labelled Phase 2 was either part of Gate 1 / Phase 1 or delivered in Phase 3. There is no Phase 2 tag or commit, and none will be created after the fact.

The numbering goes straight from Phase 1 to Phase 3. That gap is a fact about how the repository was built. It does not mean any work is missing.

## 4. Phase 3: persistence and synthetic data

Tag `phase-3-persistence`, commit `4063c23`.

Contents:

- `baec_app/data/database.py`: SQLite connection, STRICT schema (version 2 at the tag), append-only triggers, immediate write transactions, read-only canonical copies, and working copies.
- `baec_app/data/repository.py`: stores and rehydrates the locked domain objects. On every save it re-checks the classification against the locked rules, and it re-runs the state machine on stored data before saving any transition. It fails closed on corrupt stored data.
- `baec_app/data/records.py`: persistence-only data transfer objects (`SourceInteraction`, `PersistedDormancyJudgment`, `TransitionHistoryEntry`).
- `baec_app/data/seed.py`, `scripts/seed_demo.py`, `data/demo/*.json`: a fictional three-account demo dataset built only through the domain layer.
- Tests: 1403 passing at this tag.

Out of scope at this checkpoint: application/service layer, proof that an authorization came from a real human action, AI or model calls, MCP, UI, monitoring, signals, correspondence matching, and outbound contact.

### Phase 3 hardening

Tag `phase-3-persistence-hardening`, after the `phase-3-persistence` tag. Not a new phase. It closes two RC-33 gaps found after the tag:

- `baec_records` source, provenance, and decision columns could be changed by raw SQL in ways that still passed the load-time checks.
- Rows in append-only tables could be rewritten with SQLite `REPLACE`, which deletes and re-inserts a row without firing DELETE triggers.

Schema version becomes 3. Version 2 databases are refused and must be rebuilt from the seed files. Tests: 1548 passing.

How Phase 3 and its hardening map to the Research Contract is recorded in `docs/PHASE3_PERSISTENCE_TRACEABILITY.md`.

## 5. Future phases

Phase 4 and any later numbers are planning labels only. A phase is defined by its approved design document, not by its number. A future phase may be renumbered, split, or dropped before it is approved. Nothing is built ahead of the approved phase (`CLAUDE.md`).

Phase 4 (Application & Human Authorization Boundary) has a working title only. Its scope will be fixed by a design document that the project owner approves before any implementation begins.

## 6. What a checkpoint does and does not show

A checkpoint shows that the software at that commit passed its own tests on synthetic data. It does not show that BAEC or CEE is valid, that BAECs predict evaluation or purchase, that the software improves sales performance, or that the software is secure for production use (Research Contract §13).
