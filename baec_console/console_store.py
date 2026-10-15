"""The console-owned SQLite store: product metadata and Engine 3 preparation records.

What lives here, and why it is not a second copy of an engine record:

* account metadata (industry, owner note, synthetic flag, creation time). Engine 1 accounts carry only an id,
  a name, and a state;
* interaction metadata (buyer role, exact statement, seller notes) for the structured form a salesperson filled
  in. The authoritative interaction text is Engine 1's; this is the breakdown of how the console composed it;
* condition drafts: what a salesperson captured before human confirmation. Engine 1 has no draft concept; a
  confirmed draft points at the Engine 1 BAEC that became authoritative;
* Engine 3 preparation records. Engine 3 has no persistence of its own (it is a pure validator), so the
  Preparation Request, the generated outputs with their validation reports, human edits, and human review
  decisions are kept here, append-only;
* console events for console-only actions (account created, preparation requested, and so on). Engine 1 and
  Engine 2 history is read from those engines' own records, never copied here.

Preparation tables and events are append-only: triggers refuse UPDATE and DELETE.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

SCHEMA_VERSION = 2

# Fixed, versioned provenance values for console-generated preparations (see engine3_adapter and README_CONSOLE.md).
# The CHECK constraints below make it impossible to store a console preparation that claims any other origin.
CONSOLE_GENERATOR_ORIGIN = "DETERMINISTIC_CONSOLE_TEMPLATE_V1"
ENGINE3_VALIDATOR_ORIGIN = "AI_INFERENCE"
ORIGIN_COMPATIBILITY_NOTE = "E3-ORIGIN-COMPAT-V1"
ORIGIN_COMPATIBILITY_TEXT = (
    "AI_INFERENCE is the existing Engine 3 validator-compatible origin value for machine-produced drafts. "
    "No model was used to generate this preparation.")

_TABLES = {
    "console_meta": "(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "prefs": "(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "account_meta": (
        "(account_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, industry TEXT, owner_note TEXT, "
        "synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)), created_at TEXT NOT NULL, created_by TEXT NOT NULL)"
    ),
    "interaction_meta": (
        "(interaction_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, buyer_role TEXT, buyer_statement TEXT NOT NULL, "
        "seller_notes TEXT, recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL)"
    ),
    "condition_drafts": (
        "(draft_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, interaction_id TEXT NOT NULL, form_json TEXT NOT NULL, "
        "captured_at TEXT NOT NULL, captured_by TEXT NOT NULL)"
    ),
    # One row per resolved draft: the draft became an Engine 1 record (confirmed or not confirmable), or was discarded.
    "draft_resolutions": (
        "(draft_id TEXT PRIMARY KEY REFERENCES condition_drafts (draft_id), resolution TEXT NOT NULL "
        "CHECK (resolution IN ('CONFIRMED', 'RECORDED_NOT_CONFIRMABLE', 'DISCARDED')), baec_id TEXT, "
        "resolved_at TEXT NOT NULL, resolved_by TEXT NOT NULL)"
    ),
    "preparation_requests": (
        "(request_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, baec_id TEXT NOT NULL, review_id TEXT NOT NULL, "
        "candidate_id TEXT NOT NULL, request_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, "
        "context_digest TEXT NOT NULL, requested_at TEXT NOT NULL, requested_by TEXT NOT NULL)"
    ),
    "preparations": (
        "(preparation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES preparation_requests (request_id), "
        "generator TEXT NOT NULL, artifacts_json TEXT NOT NULL, generated_at TEXT NOT NULL, generated_by TEXT NOT NULL)"
    ),
    "preparation_edits": (
        "(edit_id TEXT PRIMARY KEY, preparation_id TEXT NOT NULL REFERENCES preparations (preparation_id), "
        "parent_artifact_id TEXT NOT NULL, artifact_json TEXT NOT NULL, edited_at TEXT NOT NULL, edited_by TEXT NOT NULL)"
    ),
    "preparation_reviews": (
        "(preparation_id TEXT NOT NULL REFERENCES preparations (preparation_id), artifact_id TEXT NOT NULL, "
        "decision TEXT NOT NULL CHECK (decision IN ('ACCEPT_AS_DRAFTED', 'EDIT', 'REJECT')), "
        "edit_id TEXT REFERENCES preparation_edits (edit_id), attestations_json TEXT NOT NULL, reason TEXT, "
        "reviewed_at TEXT NOT NULL, reviewed_by TEXT NOT NULL, PRIMARY KEY (preparation_id, artifact_id))"
    ),
    # V2. One row per generated preparation, written in the same transaction as the preparation.
    "preparation_provenance": (
        "(preparation_id TEXT PRIMARY KEY REFERENCES preparations (preparation_id), "
        "bound_review_id TEXT NOT NULL, bound_candidate_id TEXT NOT NULL, "
        f"console_generator_origin TEXT NOT NULL CHECK (console_generator_origin = '{CONSOLE_GENERATOR_ORIGIN}'), "
        f"engine3_validator_origin TEXT NOT NULL CHECK (engine3_validator_origin = '{ENGINE3_VALIDATOR_ORIGIN}'), "
        f"origin_compatibility_note TEXT NOT NULL CHECK (origin_compatibility_note = '{ORIGIN_COMPATIBILITY_NOTE}'), "
        "model_used INTEGER NOT NULL CHECK (model_used = 0))"
    ),
    # V2. Human decisions to block or unblock preparation from one verified correspondence (Engine 2 review).
    # Append-only: the current state is the latest decision; earlier decisions are never changed.
    "preparation_blocks": (
        "(decision_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, review_id TEXT NOT NULL, "
        "action TEXT NOT NULL CHECK (action IN ('BLOCK', 'UNBLOCK')), reason TEXT, decided_at TEXT NOT NULL, "
        "decided_by TEXT NOT NULL)"
    ),
    "events": (
        "(event_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, event_type TEXT NOT NULL, "
        "actor_kind TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL, summary TEXT NOT NULL, "
        "ref_id TEXT)"
    ),
}

_APPEND_ONLY = ("interaction_meta", "condition_drafts", "draft_resolutions", "preparation_requests", "preparations",
                "preparation_edits", "preparation_reviews", "events", "preparation_provenance", "preparation_blocks")
_V2_TABLES = ("preparation_provenance", "preparation_blocks")


class ConsoleStoreError(Exception):
    """The console store refused an operation or holds data it cannot read."""


def _schema(tables=None) -> list[str]:
    names = tuple(_TABLES) if tables is None else tables
    statements = [f"CREATE TABLE {name} {_TABLES[name]}" for name in names]
    for table in _APPEND_ONLY:
        if table not in names:
            continue
        for verb in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )
    return statements


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Additive: create the two V2 tables and record provenance for V1 preparations. Nothing existing changes.

    Every V1 preparation was produced by the same deterministic template and validated under AI_INFERENCE, and
    its Preparation Request already stores the exact Engine 2 review it was requested from, so the backfilled
    provenance is a statement of fact, not an inference.
    """
    with transaction(connection):
        for statement in _schema(_V2_TABLES):
            connection.execute(statement)
        rows = connection.execute(
            "SELECT p.preparation_id, p.generator, r.review_id, r.candidate_id FROM preparations p "
            "JOIN preparation_requests r ON r.request_id = p.request_id ORDER BY p.rowid").fetchall()
        for preparation_id, generator, review_id, candidate_id in rows:
            if generator != CONSOLE_GENERATOR_ORIGIN:
                raise ConsoleStoreError(f"preparation {preparation_id} has unknown generator {generator!r}")
            connection.execute("INSERT INTO preparation_provenance VALUES (?, ?, ?, ?, ?, ?, 0)",
                               (preparation_id, review_id, candidate_id, CONSOLE_GENERATOR_ORIGIN,
                                ENGINE3_VALIDATOR_ORIGIN, ORIGIN_COMPATIBILITY_NOTE))
        connection.execute("UPDATE console_meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))


def open_console_store(path: str) -> sqlite3.Connection:
    """Open (creating if new) a console store. The caller closes it."""
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        empty = connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
        if empty:
            with transaction(connection):
                for statement in _schema():
                    connection.execute(statement)
                connection.execute("INSERT INTO console_meta VALUES ('engine', 'BAEC_CONSOLE'), ('schema_version', ?)",
                                   (str(SCHEMA_VERSION),))
        meta = dict(connection.execute("SELECT key, value FROM console_meta").fetchall())
        if meta == {"engine": "BAEC_CONSOLE", "schema_version": "1"}:
            _migrate_v1_to_v2(connection)
            meta = dict(connection.execute("SELECT key, value FROM console_meta").fetchall())
        if meta != {"engine": "BAEC_CONSOLE", "schema_version": str(SCHEMA_VERSION)}:
            raise ConsoleStoreError(f"unsupported console store: {meta}")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConsoleStoreError("timestamps must be timezone-aware")
    return value.isoformat()


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class ConsoleEvent:
    event_id: int
    account_id: str
    event_type: str
    actor_kind: str
    actor: str
    occurred_at: datetime
    summary: str
    ref_id: str | None


class ConsoleStore:
    """Thin reads and writes over one open console-store connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection

    # --- preferences -------------------------------------------------------------

    def get_pref(self, key: str, default: str) -> str:
        row = self.db.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
        return default if row is None else row[0]

    def set_pref(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO prefs (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                        (key, value))

    # --- events ------------------------------------------------------------------

    def add_event(self, account_id: str, event_type: str, actor_kind: str, actor: str, occurred_at: datetime,
                  summary: str, ref_id: str | None = None) -> None:
        self.db.execute("INSERT INTO events (account_id, event_type, actor_kind, actor, occurred_at, summary, ref_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (account_id, event_type, actor_kind, actor, ts(occurred_at), summary, ref_id))

    def events(self, account_id: str) -> list[ConsoleEvent]:
        rows = self.db.execute("SELECT event_id, account_id, event_type, actor_kind, actor, occurred_at, summary, ref_id "
                               "FROM events WHERE account_id = ? ORDER BY event_id", (account_id,)).fetchall()
        return [ConsoleEvent(r[0], r[1], r[2], r[3], r[4], dt(r[5]), r[6], r[7]) for r in rows]

    # --- accounts and interactions -------------------------------------------------

    def add_account_meta(self, account_id: str, display_name: str, industry: str | None, owner_note: str | None,
                         synthetic: bool, created_at: datetime, created_by: str) -> None:
        self.db.execute("INSERT INTO account_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (account_id, display_name, industry, owner_note, int(synthetic), ts(created_at), created_by))

    def account_meta(self, account_id: str) -> dict | None:
        row = self.db.execute("SELECT account_id, display_name, industry, owner_note, synthetic, created_at, created_by "
                              "FROM account_meta WHERE account_id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "display_name": row[1], "industry": row[2], "owner_note": row[3],
                "synthetic": bool(row[4]), "created_at": dt(row[5]), "created_by": row[6]}

    def add_interaction_meta(self, interaction_id: str, account_id: str, buyer_role: str | None, buyer_statement: str,
                             seller_notes: str | None, recorded_at: datetime, recorded_by: str) -> None:
        self.db.execute("INSERT INTO interaction_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (interaction_id, account_id, buyer_role, buyer_statement, seller_notes, ts(recorded_at),
                         recorded_by))

    def interaction_meta(self, interaction_id: str) -> dict | None:
        row = self.db.execute("SELECT interaction_id, account_id, buyer_role, buyer_statement, seller_notes, recorded_at, "
                              "recorded_by FROM interaction_meta WHERE interaction_id = ?", (interaction_id,)).fetchone()
        if row is None:
            return None
        return {"interaction_id": row[0], "account_id": row[1], "buyer_role": row[2], "buyer_statement": row[3],
                "seller_notes": row[4], "recorded_at": dt(row[5]), "recorded_by": row[6]}

    # --- condition drafts ------------------------------------------------------------

    def add_draft(self, draft_id: str, account_id: str, interaction_id: str, form: dict, captured_at: datetime,
                  captured_by: str) -> None:
        self.db.execute("INSERT INTO condition_drafts VALUES (?, ?, ?, ?, ?, ?)",
                        (draft_id, account_id, interaction_id, json.dumps(form, sort_keys=True), ts(captured_at),
                         captured_by))

    def resolve_draft(self, draft_id: str, resolution: str, baec_id: str | None, resolved_at: datetime,
                      resolved_by: str) -> None:
        self.db.execute("INSERT INTO draft_resolutions VALUES (?, ?, ?, ?, ?)",
                        (draft_id, resolution, baec_id, ts(resolved_at), resolved_by))

    def drafts(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT d.draft_id, d.account_id, d.interaction_id, d.form_json, d.captured_at, d.captured_by, "
               "r.resolution, r.baec_id, r.resolved_at, r.resolved_by FROM condition_drafts d "
               "LEFT JOIN draft_resolutions r ON r.draft_id = d.draft_id")
        args: tuple = ()
        if account_id is not None:
            sql += " WHERE d.account_id = ?"
            args = (account_id,)
        rows = self.db.execute(sql + " ORDER BY d.rowid", args).fetchall()
        return [{"draft_id": r[0], "account_id": r[1], "interaction_id": r[2], "form": json.loads(r[3]),
                 "captured_at": dt(r[4]), "captured_by": r[5], "resolution": r[6], "baec_id": r[7],
                 "resolved_at": dt(r[8]) if r[8] else None, "resolved_by": r[9]} for r in rows]

    def draft(self, draft_id: str) -> dict:
        for item in self.drafts():
            if item["draft_id"] == draft_id:
                return item
        raise ConsoleStoreError(f"no condition draft {draft_id}")

    # --- preparation -----------------------------------------------------------------

    def add_preparation_request(self, request_id: str, account_id: str, baec_id: str, review_id: str,
                                candidate_id: str, request: dict, snapshot: dict, context_digest: str,
                                requested_at: datetime, requested_by: str) -> None:
        self.db.execute("INSERT INTO preparation_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (request_id, account_id, baec_id, review_id, candidate_id, json.dumps(request, sort_keys=True),
                         json.dumps(snapshot, sort_keys=True), context_digest, ts(requested_at), requested_by))

    def preparation_requests(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT request_id, account_id, baec_id, review_id, candidate_id, request_json, snapshot_json, "
               "context_digest, requested_at, requested_by FROM preparation_requests")
        args: tuple = ()
        if account_id is not None:
            sql += " WHERE account_id = ?"
            args = (account_id,)
        rows = self.db.execute(sql + " ORDER BY rowid", args).fetchall()
        return [{"request_id": r[0], "account_id": r[1], "baec_id": r[2], "review_id": r[3], "candidate_id": r[4],
                 "request": json.loads(r[5]), "snapshot": json.loads(r[6]), "context_digest": r[7],
                 "requested_at": dt(r[8]), "requested_by": r[9]} for r in rows]

    def preparation_request(self, request_id: str) -> dict:
        for item in self.preparation_requests():
            if item["request_id"] == request_id:
                return item
        raise ConsoleStoreError(f"no preparation request {request_id}")

    def add_preparation(self, preparation_id: str, request_id: str, generator: str, artifacts: list[dict],
                        generated_at: datetime, generated_by: str) -> None:
        """Store a console-generated preparation with its fixed provenance and correspondence binding.

        Call inside a transaction: the preparation and its provenance row are written together.
        """
        request = self.preparation_request(request_id)
        self.db.execute("INSERT INTO preparations VALUES (?, ?, ?, ?, ?, ?)",
                        (preparation_id, request_id, generator, json.dumps(artifacts, sort_keys=True), ts(generated_at),
                         generated_by))
        self.db.execute("INSERT INTO preparation_provenance VALUES (?, ?, ?, ?, ?, ?, 0)",
                        (preparation_id, request["review_id"], request["candidate_id"], generator,
                         ENGINE3_VALIDATOR_ORIGIN, ORIGIN_COMPATIBILITY_NOTE))

    def provenance(self, preparation_id: str) -> dict:
        row = self.db.execute("SELECT preparation_id, bound_review_id, bound_candidate_id, console_generator_origin, "
                              "engine3_validator_origin, origin_compatibility_note, model_used "
                              "FROM preparation_provenance WHERE preparation_id = ?", (preparation_id,)).fetchone()
        if row is None:
            raise ConsoleStoreError(f"preparation {preparation_id} has no provenance record")
        return {"preparation_id": row[0], "bound_review_id": row[1], "bound_candidate_id": row[2],
                "console_generator_origin": row[3], "engine3_validator_origin": row[4],
                "origin_compatibility_note": row[5], "model_used": bool(row[6])}

    # --- human preparation blocks ------------------------------------------------------

    def add_block_decision(self, account_id: str, review_id: str, action: str, reason: str | None,
                           decided_at: datetime, decided_by: str) -> None:
        self.db.execute("INSERT INTO preparation_blocks (account_id, review_id, action, reason, decided_at, decided_by) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (account_id, review_id, action, reason, ts(decided_at), decided_by))

    def block_decisions(self, review_id: str) -> list[dict]:
        rows = self.db.execute("SELECT decision_id, account_id, review_id, action, reason, decided_at, decided_by "
                               "FROM preparation_blocks WHERE review_id = ? ORDER BY decision_id", (review_id,)).fetchall()
        return [{"decision_id": r[0], "account_id": r[1], "review_id": r[2], "action": r[3], "reason": r[4],
                 "decided_at": dt(r[5]), "decided_by": r[6]} for r in rows]

    def is_blocked(self, review_id: str) -> bool:
        decisions = self.block_decisions(review_id)
        return bool(decisions) and decisions[-1]["action"] == "BLOCK"

    def preparations(self) -> list[dict]:
        rows = self.db.execute("SELECT preparation_id, request_id, generator, artifacts_json, generated_at, generated_by "
                               "FROM preparations ORDER BY rowid").fetchall()
        return [{"preparation_id": r[0], "request_id": r[1], "generator": r[2], "artifacts": json.loads(r[3]),
                 "generated_at": dt(r[4]), "generated_by": r[5]} for r in rows]

    def preparation(self, preparation_id: str) -> dict:
        for item in self.preparations():
            if item["preparation_id"] == preparation_id:
                return item
        raise ConsoleStoreError(f"no preparation {preparation_id}")

    def add_edit(self, edit_id: str, preparation_id: str, parent_artifact_id: str, artifact: dict,
                 edited_at: datetime, edited_by: str) -> None:
        self.db.execute("INSERT INTO preparation_edits VALUES (?, ?, ?, ?, ?, ?)",
                        (edit_id, preparation_id, parent_artifact_id, json.dumps(artifact, sort_keys=True),
                         ts(edited_at), edited_by))

    def edits(self, preparation_id: str) -> list[dict]:
        rows = self.db.execute("SELECT edit_id, preparation_id, parent_artifact_id, artifact_json, edited_at, edited_by "
                               "FROM preparation_edits WHERE preparation_id = ? ORDER BY rowid",
                               (preparation_id,)).fetchall()
        return [{"edit_id": r[0], "preparation_id": r[1], "parent_artifact_id": r[2], "artifact": json.loads(r[3]),
                 "edited_at": dt(r[4]), "edited_by": r[5]} for r in rows]

    def add_review(self, preparation_id: str, artifact_id: str, decision: str, edit_id: str | None,
                   attestations: dict, reason: str | None, reviewed_at: datetime, reviewed_by: str) -> None:
        self.db.execute("INSERT INTO preparation_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (preparation_id, artifact_id, decision, edit_id, json.dumps(attestations, sort_keys=True),
                         reason, ts(reviewed_at), reviewed_by))

    def reviews(self, preparation_id: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT artifact_id, decision, edit_id, attestations_json, reason, reviewed_at, reviewed_by "
                               "FROM preparation_reviews WHERE preparation_id = ? ORDER BY rowid",
                               (preparation_id,)).fetchall()
        return {r[0]: {"artifact_id": r[0], "decision": r[1], "edit_id": r[2], "attestations": json.loads(r[3]),
                       "reason": r[4], "reviewed_at": dt(r[5]), "reviewed_by": r[6]} for r in rows}

    def account_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT account_id FROM account_meta ORDER BY rowid").fetchall()]
