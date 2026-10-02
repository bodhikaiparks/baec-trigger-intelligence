"""SQLite connection, schema, versioning, and transactions.

This module knows table shapes and nothing about BAEC meaning. Decisions
stay in the domain layer; conversion between rows and objects is in
repository.py.

STRICT tables stop a column from holding the wrong storage type. They are a
backstop only: repository serialization and fail-closed rehydration through
the domain constructors remain the authority on what stored data means.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from enum import Enum
from typing import Iterator

from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    ThresholdComparator,
)

# Version 2 added composite keys binding a transition's BAEC to its account
# and its judgment to that BAEC.
SCHEMA_VERSION = 2
MINIMUM_SQLITE_VERSION = (3, 37, 0)  # first version with STRICT tables


class PersistenceError(Exception):
    """Base class for every storage-layer error."""


class DatabaseVersionError(PersistenceError):
    """SQLite is too old, or the database schema version is not the expected one."""


class PersistenceIntegrityError(PersistenceError):
    """Stored data is corrupt or contradictory. Nothing is returned."""


class RepositoryConflictError(PersistenceError):
    """A write collided with existing data: duplicate identifier or stale state."""


class RepositoryNotFoundError(PersistenceError):
    """A supplied identifier does not exist in storage."""


class RepositoryVerificationError(PersistenceError):
    """A save was refused because it disagrees with the locked domain rules."""


# Evidence stored against an interaction is only ever what the buyer said or
# what the seller documented. External evidence (signals) never enters here.
INTERACTION_EVIDENCE_PROVENANCE = (
    ProvenanceCategory.BUYER_FACT,
    ProvenanceCategory.SELLER_OBSERVATION,
)

DATA_TABLES = (
    "accounts",
    "interactions",
    "interaction_evidence",
    "human_authorizations",
    "baec_records",
    "criterion_assessments",
    "criterion_evidence",
    "stringency_expressions",
    "dormancy_judgments",
    "evaluation_evidence",
    "non_evaluation_evidence",
    "account_state_transitions",
)
ALL_TABLES = ("schema_meta",) + DATA_TABLES

APPEND_ONLY_TABLES = (
    "interactions",
    "interaction_evidence",
    "human_authorizations",
    "criterion_assessments",
    "criterion_evidence",
    "stringency_expressions",
    "dormancy_judgments",
    "evaluation_evidence",
    "non_evaluation_evidence",
    "account_state_transitions",
)


def _values(members) -> str:
    """SQL value list for an enum column, generated from the enum itself."""
    return ", ".join(f"'{m.value}'" for m in members)


def _in(column: str, enum_or_members) -> str:
    members = list(enum_or_members) if isinstance(enum_or_members, type) else enum_or_members
    return f"CHECK ({column} IN ({_values(members)}))"


def _state_evidence_table(name: str, key: str) -> str:
    return f"""
    CREATE TABLE {name} (
        {key} INTEGER PRIMARY KEY,
        account_id TEXT NOT NULL,
        interaction_id TEXT NOT NULL,
        evidence_id INTEGER NOT NULL,
        observed_at TEXT NOT NULL,
        FOREIGN KEY (interaction_id, account_id)
            REFERENCES interactions (interaction_id, account_id) ON DELETE RESTRICT,
        FOREIGN KEY (evidence_id, interaction_id)
            REFERENCES interaction_evidence (evidence_id, interaction_id) ON DELETE RESTRICT
    ) STRICT"""


def _schema_statements() -> list[str]:
    statements = [
        """
    CREATE TABLE schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) STRICT""",
        f"""
    CREATE TABLE accounts (
        account_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        state TEXT {_in('state', AccountState)}
    ) STRICT""",
        """
    CREATE TABLE interactions (
        interaction_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts (account_id) ON DELETE RESTRICT,
        occurred_at TEXT NOT NULL,
        text TEXT NOT NULL,
        UNIQUE (interaction_id, account_id)
    ) STRICT""",
        f"""
    CREATE TABLE interaction_evidence (
        evidence_id INTEGER PRIMARY KEY,
        interaction_id TEXT NOT NULL REFERENCES interactions (interaction_id) ON DELETE RESTRICT,
        provenance TEXT NOT NULL {_in('provenance', INTERACTION_EVIDENCE_PROVENANCE)},
        text TEXT NOT NULL,
        UNIQUE (interaction_id, provenance, text),
        UNIQUE (evidence_id, interaction_id)
    ) STRICT""",
        f"""
    CREATE TABLE human_authorizations (
        authorization_id INTEGER PRIMARY KEY,
        authorized_by TEXT NOT NULL,
        authorized_at TEXT NOT NULL,
        action TEXT NOT NULL {_in('action', AuthorizationAction)},
        subject_id TEXT NOT NULL,
        target_state TEXT {_in('target_state', AccountState)},
        CHECK ((action = '{AuthorizationAction.CHANGE_ACCOUNT_STATE.value}') = (target_state IS NOT NULL))
    ) STRICT""",
        f"""
    CREATE TABLE baec_records (
        baec_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        source_interaction_id TEXT NOT NULL,
        source_excerpt_id INTEGER NOT NULL,
        captured_at TEXT NOT NULL,
        buyer_role TEXT,
        buyer_exact_statement TEXT,
        articulation_origin TEXT NOT NULL {_in('articulation_origin', ArticulationOrigin)},
        elicitation_mode TEXT NOT NULL {_in('elicitation_mode', ElicitationMode)},
        classification TEXT NOT NULL {_in('classification', BaecClassification)},
        classification_reason TEXT,
        staleness_status TEXT {_in('staleness_status', StalenessStatus)},
        confirmation_authorization_id INTEGER UNIQUE
            REFERENCES human_authorizations (authorization_id) ON DELETE RESTRICT,
        normalized_text TEXT,
        normalized_generated_at TEXT,
        normalized_model TEXT,
        UNIQUE (baec_id, source_interaction_id),
        UNIQUE (baec_id, account_id),
        FOREIGN KEY (source_interaction_id, account_id)
            REFERENCES interactions (interaction_id, account_id) ON DELETE RESTRICT,
        FOREIGN KEY (source_excerpt_id, source_interaction_id)
            REFERENCES interaction_evidence (evidence_id, interaction_id) ON DELETE RESTRICT,
        CHECK (
            (normalized_text IS NULL AND normalized_generated_at IS NULL AND normalized_model IS NULL)
            OR (normalized_text IS NOT NULL AND normalized_generated_at IS NOT NULL)
        )
    ) STRICT""",
        f"""
    CREATE TABLE criterion_assessments (
        baec_id TEXT NOT NULL REFERENCES baec_records (baec_id) ON DELETE RESTRICT,
        criterion TEXT NOT NULL {_in('criterion', BaecCriterion)},
        position INTEGER NOT NULL CHECK (position >= 0),
        finding TEXT NOT NULL {_in('finding', CriterionFinding)},
        rationale TEXT,
        PRIMARY KEY (baec_id, criterion),
        UNIQUE (baec_id, position)
    ) STRICT""",
        """
    CREATE TABLE criterion_evidence (
        baec_id TEXT NOT NULL,
        criterion TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (position >= 0),
        evidence_id INTEGER NOT NULL,
        source_interaction_id TEXT NOT NULL,
        PRIMARY KEY (baec_id, criterion, position),
        FOREIGN KEY (baec_id, criterion)
            REFERENCES criterion_assessments (baec_id, criterion) ON DELETE RESTRICT,
        FOREIGN KEY (baec_id, source_interaction_id)
            REFERENCES baec_records (baec_id, source_interaction_id) ON DELETE RESTRICT,
        FOREIGN KEY (evidence_id, source_interaction_id)
            REFERENCES interaction_evidence (evidence_id, interaction_id) ON DELETE RESTRICT
    ) STRICT""",
        f"""
    CREATE TABLE stringency_expressions (
        baec_id TEXT PRIMARY KEY REFERENCES baec_records (baec_id) ON DELETE RESTRICT,
        verbatim_text TEXT NOT NULL,
        comparator TEXT {_in('comparator', ThresholdComparator)},
        numeric_value TEXT,
        unit TEXT,
        qualitative_term TEXT,
        recurrence_text TEXT,
        timing_text TEXT
    ) STRICT""",
        f"""
    CREATE TABLE dormancy_judgments (
        judgment_id INTEGER PRIMARY KEY,
        baec_id TEXT NOT NULL REFERENCES baec_records (baec_id) ON DELETE RESTRICT,
        plausibility TEXT NOT NULL {_in('plausibility', ReviewAnswer)},
        addressability TEXT NOT NULL {_in('addressability', ReviewAnswer)},
        notes TEXT,
        authorization_id INTEGER NOT NULL UNIQUE
            REFERENCES human_authorizations (authorization_id) ON DELETE RESTRICT,
        UNIQUE (judgment_id, baec_id)
    ) STRICT""",
        _state_evidence_table("evaluation_evidence", "evaluation_evidence_id"),
        _state_evidence_table("non_evaluation_evidence", "non_evaluation_evidence_id"),
        f"""
    CREATE TABLE account_state_transitions (
        transition_id INTEGER PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts (account_id) ON DELETE RESTRICT,
        from_state TEXT {_in('from_state', AccountState)},
        to_state TEXT NOT NULL {_in('to_state', AccountState)},
        authorization_id INTEGER NOT NULL UNIQUE
            REFERENCES human_authorizations (authorization_id) ON DELETE RESTRICT,
        baec_id TEXT REFERENCES baec_records (baec_id) ON DELETE RESTRICT,
        judgment_id INTEGER REFERENCES dormancy_judgments (judgment_id) ON DELETE RESTRICT,
        evaluation_evidence_id INTEGER
            REFERENCES evaluation_evidence (evaluation_evidence_id) ON DELETE RESTRICT,
        non_evaluation_evidence_id INTEGER
            REFERENCES non_evaluation_evidence (non_evaluation_evidence_id) ON DELETE RESTRICT,
        ground TEXT {_in('ground', NoPlausiblePathGround)},
        reason TEXT,
        basis_interaction_id TEXT,
        unresolved TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        FOREIGN KEY (basis_interaction_id, account_id)
            REFERENCES interactions (interaction_id, account_id) ON DELETE RESTRICT,
        -- A transition's BAEC must belong to the transition's account, and
        -- its judgment must be a judgment of that same BAEC.
        FOREIGN KEY (baec_id, account_id)
            REFERENCES baec_records (baec_id, account_id) ON DELETE RESTRICT,
        FOREIGN KEY (judgment_id, baec_id)
            REFERENCES dormancy_judgments (judgment_id, baec_id) ON DELETE RESTRICT
    ) STRICT""",
    ]
    for table in APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"""
    CREATE TRIGGER {table}_no_{operation.lower()}
    BEFORE {operation} ON {table}
    BEGIN
        SELECT RAISE(ABORT, '{table} is append-only');
    END"""
            )
    return statements


def check_sqlite_version(version_info: tuple[int, ...] | None = None) -> None:
    """Refuse to run on an SQLite too old for STRICT tables."""
    found = sqlite3.sqlite_version_info if version_info is None else version_info
    if tuple(found) < MINIMUM_SQLITE_VERSION:
        needed = ".".join(str(part) for part in MINIMUM_SQLITE_VERSION)
        have = ".".join(str(part) for part in found)
        raise DatabaseVersionError(
            f"SQLite {needed} or newer is required (found {have}). "
            "Install a newer Python, which bundles a newer SQLite."
        )


def _raw_connect(target: str, uri: bool = False) -> sqlite3.Connection:
    # isolation_level=None: this code issues BEGIN/COMMIT itself.
    return sqlite3.connect(target, isolation_level=None, uri=uri)


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open a connection with foreign keys enforced. Does not create a schema."""
    check_sqlite_version()
    connection = _raw_connect(str(path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise PersistenceError("foreign key enforcement could not be enabled")
    except BaseException:
        connection.close()
        raise
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _has_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return row[0] > 0


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create every table and trigger in an empty database."""
    if schema_version(connection) != 0 or _has_tables(connection):
        raise DatabaseVersionError("the database is not empty; refusing to create the schema")
    with transaction(connection):
        for statement in _schema_statements():
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def require_current_schema(connection: sqlite3.Connection) -> None:
    found = schema_version(connection)
    if found != SCHEMA_VERSION:
        raise DatabaseVersionError(
            f"database schema version is {found}, expected {SCHEMA_VERSION}. "
            "V0.1 data is synthetic: rebuild the database from the seed files."
        )


def open_database(path: str = ":memory:") -> sqlite3.Connection:
    """Open a database, creating the schema if it is new and empty."""
    connection = connect(path)
    try:
        if schema_version(connection) == 0 and not _has_tables(connection):
            initialize_schema(connection)
        require_current_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One immediate write transaction: commit on success, roll back on any error."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def make_read_only(connection: sqlite3.Connection) -> None:
    """Refuse further writes on this connection (used for the canonical seed)."""
    connection.execute("PRAGMA query_only = ON")


def create_working_copy(source: sqlite3.Connection, path: str = ":memory:") -> sqlite3.Connection:
    """Copy a whole database into a new, independent, writable database."""
    working = connect(path)
    try:
        if _has_tables(working):
            raise RepositoryConflictError("the working-copy destination is not empty")
        source.backup(working)
        require_current_schema(working)
    except BaseException:
        working.close()
        raise
    return working


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in DATA_TABLES
    }
