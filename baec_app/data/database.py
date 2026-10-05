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
# and its judgment to that BAEC. Version 3 added the RC-33 guards: protected
# baec_records columns, no baec_records deletes, and no replacing stored rows.
# Version 4 added evidence fidelity: interaction evidence text must occur
# verbatim in the text of the interaction it cites. Version 5 added the five
# append-only Phase 6 AI provenance tables (docs/PHASE6_STRUCTURED_CLAUDE_PROVENANCE_DESIGN.md §10).
# Version 6 added ai_runs.validation_version and the closed, status-specific
# failure-code vocabulary (docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §5-§6).
SCHEMA_VERSION = 6
# 3.37.0 is the first version with STRICT tables; 3.38.0 the first with JSON
# functions built in by default, which the failure-code backstop uses.
MINIMUM_SQLITE_VERSION = (3, 38, 0)


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
) + (
    # Phase 6 AI provenance (schema version 5). AI interpretation, never domain evidence.
    "ai_runs",
    "ai_run_results",
    "ai_run_outputs",
    "ai_artifacts",
    "ai_artifact_excerpts",
)
AI_PROVENANCE_TABLES = DATA_TABLES[-5:]
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

# baec_records is not append-only: staleness_status is a lifecycle field
# reserved for a later, explicitly designed phase. Every other column holds
# source evidence, provenance, a classification decision, or AI-derived
# text, and is immutable once written (RC-33).
BAEC_RECORD_MUTABLE_COLUMNS = ("staleness_status",)
BAEC_RECORD_PROTECTED_COLUMNS = (
    "baec_id",
    "account_id",
    "source_interaction_id",
    "source_excerpt_id",
    "captured_at",
    "buyer_role",
    "buyer_exact_statement",
    "articulation_origin",
    "elicitation_mode",
    "classification",
    "classification_reason",
    "confirmation_authorization_id",
    "normalized_text",
    "normalized_generated_at",
    "normalized_model",
)

# Every UNIQUE key (primary key included) of each table whose stored rows must
# never be replaced. SQLite runs REPLACE as delete-then-insert without firing
# DELETE triggers, so each insert that collides on any of these keys is refused.
REPLACE_GUARDED_KEYS = {
    "interactions": (("interaction_id",),),
    "interaction_evidence": (("evidence_id",), ("interaction_id", "provenance", "text")),
    "human_authorizations": (("authorization_id",),),
    "baec_records": (("baec_id",), ("confirmation_authorization_id",)),
    "criterion_assessments": (("baec_id", "criterion"), ("baec_id", "position")),
    "criterion_evidence": (("baec_id", "criterion", "position"),),
    "stringency_expressions": (("baec_id",),),
    "dormancy_judgments": (("judgment_id",), ("authorization_id",)),
    "evaluation_evidence": (("evaluation_evidence_id",),),
    "non_evaluation_evidence": (("non_evaluation_evidence_id",),),
    "account_state_transitions": (("transition_id",), ("authorization_id",)),
}

# The AI provenance tables are append-only too. They are kept apart from the
# domain RC-33 constants above so those keep their Phase 3 meaning.
AI_APPEND_ONLY_TABLES = AI_PROVENANCE_TABLES
AI_REPLACE_GUARDED_KEYS = {
    "ai_runs": (("ai_run_id",),),
    "ai_run_results": (("ai_run_id",),),
    "ai_run_outputs": (("ai_run_id",),),
    "ai_artifacts": (("artifact_id",), ("ai_run_id",)),
    "ai_artifact_excerpts": (("artifact_id", "excerpt_id"), ("artifact_id", "interaction_id", "text")),
}

# Terminal AI run vocabulary (Phase 6 design §11). Lowercase machine tokens.
AI_RUN_STATUSES = (
    "success",
    "refusal",
    "max_tokens",
    "unexpected_stop",
    "api_error",
    "transport_failure",
    "parse_failure",
    "semantic_validation_failure",
    "model_mismatch",
    "interrupted",
)
AI_REMOTE_OUTCOMES = ("response_received", "not_sent", "unknown")
# Statuses that exist only because a response came back.
AI_RESPONSE_STATUSES = tuple(s for s in AI_RUN_STATUSES if s not in ("transport_failure", "interrupted"))
# Statuses that may carry returned model text, and those that must.
AI_OUTPUT_STATUSES = (
    "success",
    "refusal",
    "max_tokens",
    "unexpected_stop",
    "parse_failure",
    "semantic_validation_failure",
    "model_mismatch",
)
AI_OUTPUT_REQUIRED_STATUSES = ("success", "semantic_validation_failure")
AI_API_ERROR_CATEGORIES = (
    "authentication",
    "permission",
    "rate_limited",
    "overloaded",
    "invalid_request",
    "server_error",
    "other",
)
# AI-inferred speaker of a source excerpt. An interpretation, never verified and never buyer evidence.
AI_SPEAKER_LABELS = ("buyer", "seller", "unclear")
# Each transport category implies its delivery state.
AI_TRANSPORT_CATEGORIES = {"connection_not_established": "not_sent", "timeout_or_disconnect": "unknown"}

# Closed failure-code vocabularies (Phase 6D design §5.1), duplicated from the AI
# layer with equality tests. Fixed machine tokens with no variable part.
# parse_failure carries exactly one parse code. semantic_validation_failure carries
# one or more codes, sorted and unique, from the vocabulary of the validator
# version its run recorded: a code is legal only under a validator that can emit it.
# Validation v2 and its grounding codes are added here by 6D-B2, not before.
AI_PARSE_FAILURE_CODES = (
    "invalid_json",
    "missing_stop_reason",
    "missing_text_block",
    "multiple_text_blocks",
    "structured_output_validation_failed",
)
AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION = {
    "baec-extraction-validation/v1": (
        "criterion_set_invalid",
        "duplicate_excerpt_id",
        "duplicate_excerpt_reference",
        "duplicate_excerpt_text",
        "excerpt_blank",
        "excerpt_id_blank",
        "excerpt_not_verbatim",
        "explanation_blank",
        "explanation_too_long",
        "normalization_blank",
        "normalization_too_long",
        "possible_language_without_excerpt",
        "source_interaction_mismatch",
        "supported_without_excerpt",
        "too_many_excerpt_references",
        "too_many_excerpts",
        "too_many_uncertainties",
        "uncertainty_blank",
        "uncertainty_too_long",
        "unknown_excerpt_reference",
    ),
}
# Every semantic code legal under some known validator version.
AI_SEMANTIC_FAILURE_CODES = tuple(sorted(set().union(*AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION.values())))


def _values(members) -> str:
    """SQL value list for an enum column, generated from the enum itself."""
    return ", ".join(f"'{m.value}'" for m in members)


def _tokens(values) -> str:
    """SQL value list for a fixed vocabulary of plain string tokens."""
    return ", ".join(f"'{value}'" for value in values)


def _sha256(column: str) -> str:
    """A lowercase hexadecimal SHA-256 digest: exactly 64 characters from [0-9a-f]."""
    return f"(length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*')"


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
    statements += _ai_provenance_tables()
    for table in APPEND_ONLY_TABLES + AI_APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"""
    CREATE TRIGGER {table}_no_{operation.lower()}
    BEFORE {operation} ON {table}
    BEGIN
        SELECT RAISE(ABORT, '{table} is append-only');
    END"""
            )
    for table, keys in (REPLACE_GUARDED_KEYS | AI_REPLACE_GUARDED_KEYS).items():
        collision = " OR ".join(
            f"EXISTS (SELECT 1 FROM {table} WHERE "
            + " AND ".join(f"{column} = NEW.{column}" for column in key)
            + ")"
            for key in keys
        )
        statements.append(
            f"""
    CREATE TRIGGER {table}_no_replace
    BEFORE INSERT ON {table}
    WHEN {collision}
    BEGIN
        SELECT RAISE(ABORT, '{table} rows cannot be replaced');
    END"""
        )
    # Evidence fidelity (implementation constraint): stored buyer-fact and
    # seller-observation text must be an exact, case-sensitive substring of
    # the cited interaction. Fires when the interaction is missing too.
    statements.append(
        """
    CREATE TRIGGER interaction_evidence_verbatim
    BEFORE INSERT ON interaction_evidence
    WHEN NEW.text = ''
        OR NOT COALESCE(
            instr((SELECT text FROM interactions WHERE interaction_id = NEW.interaction_id), NEW.text) > 0,
            0
        )
    BEGIN
        SELECT RAISE(ABORT, 'interaction_evidence text must occur verbatim in its interaction');
    END"""
    )
    statements.append(
        f"""
    CREATE TRIGGER baec_records_protected_no_update
    BEFORE UPDATE OF {", ".join(BAEC_RECORD_PROTECTED_COLUMNS)} ON baec_records
    BEGIN
        SELECT RAISE(ABORT, 'baec_records source and decision columns are immutable');
    END"""
    )
    statements.append(
        """
    CREATE TRIGGER baec_records_no_delete
    BEFORE DELETE ON baec_records
    BEGIN
        SELECT RAISE(ABORT, 'baec_records rows cannot be deleted');
    END"""
    )
    statements += _ai_provenance_triggers()
    return statements


# --- Phase 6 AI provenance (schema versions 5 and 6) --------------------------------
#
# Records of AI runs, their single terminal result, the raw returned model
# text, and successful structured artifacts with their source excerpts. AI
# output is interpretation, never buyer evidence, human judgment, or approval.


def _ai_provenance_tables() -> list[str]:
    response = _tokens(AI_RESPONSE_STATUSES)
    return [
        f"""
    CREATE TABLE ai_runs (
        ai_run_id TEXT PRIMARY KEY CHECK (ai_run_id <> ''),
        provider TEXT NOT NULL CHECK (provider = 'anthropic'),
        task_type TEXT NOT NULL CHECK (task_type <> ''),
        task_version TEXT NOT NULL CHECK (task_version <> ''),
        account_id TEXT NOT NULL,
        interaction_id TEXT NOT NULL,
        requested_model TEXT NOT NULL CHECK (requested_model <> ''),
        sdk_name TEXT NOT NULL CHECK (sdk_name <> ''),
        sdk_version TEXT NOT NULL CHECK (sdk_version <> ''),
        prompt_version TEXT NOT NULL CHECK (prompt_version <> ''),
        prompt_digest TEXT NOT NULL CHECK {_sha256('prompt_digest')},
        input_version TEXT NOT NULL CHECK (input_version <> ''),
        input_digest TEXT NOT NULL CHECK {_sha256('input_digest')},
        output_schema_version TEXT NOT NULL CHECK (output_schema_version <> ''),
        output_schema_digest TEXT NOT NULL CHECK {_sha256('output_schema_digest')},
        canonicalization_version TEXT NOT NULL CHECK (canonicalization_version <> ''),
        request_spec_version TEXT NOT NULL CHECK (request_spec_version <> ''),
        request_digest TEXT NOT NULL CHECK {_sha256('request_digest')},
        -- Never blank or ASCII-whitespace-only; stored exactly as given, never trimmed.
        validation_version TEXT NOT NULL CHECK (trim(validation_version, {_SQL_WHITESPACE}) <> ''),
        requested_at TEXT NOT NULL,
        retry_of_ai_run_id TEXT REFERENCES ai_runs (ai_run_id) ON DELETE RESTRICT,
        CHECK (retry_of_ai_run_id IS NULL OR retry_of_ai_run_id <> ai_run_id),
        -- Parent key that binds an artifact to its run's source, task, and versions.
        UNIQUE (ai_run_id, account_id, interaction_id, task_type, task_version, output_schema_version),
        -- A run's interaction must belong to its recorded account.
        FOREIGN KEY (interaction_id, account_id)
            REFERENCES interactions (interaction_id, account_id) ON DELETE RESTRICT
    ) STRICT""",
        f"""
    CREATE TABLE ai_run_results (
        ai_run_id TEXT PRIMARY KEY REFERENCES ai_runs (ai_run_id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK (status IN ({_tokens(AI_RUN_STATUSES)})),
        remote_outcome TEXT NOT NULL CHECK (remote_outcome IN ({_tokens(AI_REMOTE_OUTCOMES)})),
        provider_message_id TEXT CHECK (provider_message_id <> ''),
        response_model TEXT CHECK (response_model <> ''),
        stop_reason TEXT CHECK (stop_reason <> ''),
        provider_request_id TEXT CHECK (provider_request_id <> ''),
        input_tokens INTEGER CHECK (input_tokens >= 0),
        output_tokens INTEGER CHECK (output_tokens >= 0),
        cache_creation_input_tokens INTEGER CHECK (cache_creation_input_tokens >= 0),
        cache_read_input_tokens INTEGER CHECK (cache_read_input_tokens >= 0),
        failure_category TEXT,
        failure_codes TEXT CHECK (failure_codes <> ''),
        output_digest TEXT CHECK (output_digest IS NULL OR {_sha256('output_digest')}),
        completed_at TEXT NOT NULL,
        -- Delivery state follows from the status.
        CHECK (
            (status IN ({response}) AND remote_outcome = 'response_received')
            OR (status = 'transport_failure' AND remote_outcome IN ('not_sent', 'unknown'))
            OR (status = 'interrupted' AND remote_outcome = 'unknown')
        ),
        -- Returned model text exists only for statuses that can carry it, and must for some.
        CHECK (status IN ({_tokens(AI_OUTPUT_STATUSES)}) OR output_digest IS NULL),
        CHECK (status NOT IN ({_tokens(AI_OUTPUT_REQUIRED_STATUSES)}) OR output_digest IS NOT NULL),
        CHECK (status <> 'success' OR (
            provider_message_id IS NOT NULL AND response_model IS NOT NULL AND stop_reason IS 'end_turn'
        )),
        -- NULL-safe comparisons throughout: a CHECK whose expression is NULL would pass.
        CHECK (status <> 'refusal' OR stop_reason IS 'refusal'),
        CHECK (status <> 'max_tokens' OR stop_reason IS 'max_tokens'),
        CHECK (status <> 'unexpected_stop' OR stop_reason NOT IN ('end_turn', 'refusal', 'max_tokens')),
        CHECK (status <> 'unexpected_stop' OR stop_reason IS NOT NULL),
        CHECK (status <> 'model_mismatch' OR response_model IS NOT NULL),
        -- No response: no response metadata.
        CHECK (status NOT IN ('transport_failure', 'interrupted') OR (
            provider_message_id IS NULL AND response_model IS NULL AND stop_reason IS NULL
            AND input_tokens IS NULL AND output_tokens IS NULL
            AND cache_creation_input_tokens IS NULL AND cache_read_input_tokens IS NULL
        )),
        -- Failure categories are a fixed vocabulary, used only where the design defines them.
        CHECK (
            (status = 'api_error' AND COALESCE(failure_category IN ({_tokens(AI_API_ERROR_CATEGORIES)}), 0))
            OR (status = 'transport_failure' AND (
                (failure_category IS 'connection_not_established' AND remote_outcome = 'not_sent')
                OR (failure_category IS 'timeout_or_disconnect' AND remote_outcome = 'unknown')
            ))
            OR (status NOT IN ('api_error', 'transport_failure') AND failure_category IS NULL)
        ),
        -- Failure-code cardinality and vocabulary are also checked by ai_run_results_failure_codes_closed.
        CHECK (status IN ('parse_failure', 'semantic_validation_failure') OR failure_codes IS NULL),
        CHECK (status <> 'semantic_validation_failure' OR failure_codes IS NOT NULL),
        CHECK (status <> 'parse_failure' OR failure_codes IS NOT NULL)
    ) STRICT""",
        f"""
    CREATE TABLE ai_run_outputs (
        ai_run_id TEXT PRIMARY KEY REFERENCES ai_run_results (ai_run_id) ON DELETE RESTRICT,
        raw_output_text TEXT NOT NULL,
        output_digest TEXT NOT NULL CHECK {_sha256('output_digest')}
    ) STRICT""",
        f"""
    CREATE TABLE ai_artifacts (
        artifact_id TEXT PRIMARY KEY CHECK (artifact_id <> ''),
        ai_run_id TEXT NOT NULL UNIQUE REFERENCES ai_run_results (ai_run_id) ON DELETE RESTRICT,
        task_type TEXT NOT NULL,
        task_version TEXT NOT NULL,
        output_schema_version TEXT NOT NULL,
        account_id TEXT NOT NULL,
        interaction_id TEXT NOT NULL,
        canonical_result TEXT NOT NULL CHECK (canonical_result <> ''),
        artifact_digest TEXT NOT NULL CHECK {_sha256('artifact_digest')},
        created_at TEXT NOT NULL,
        -- Parent key for the excerpts' interaction binding.
        UNIQUE (artifact_id, interaction_id),
        -- The artifact's source, task, task version, and output schema version are exactly its run's.
        FOREIGN KEY (ai_run_id, account_id, interaction_id, task_type, task_version, output_schema_version)
            REFERENCES ai_runs (ai_run_id, account_id, interaction_id, task_type, task_version, output_schema_version)
            ON DELETE RESTRICT
    ) STRICT""",
        f"""
    CREATE TABLE ai_artifact_excerpts (
        artifact_id TEXT NOT NULL,
        excerpt_id TEXT NOT NULL CHECK (excerpt_id <> ''),
        interaction_id TEXT NOT NULL,
        text TEXT NOT NULL,
        -- AI inference about who spoke; never verified, never buyer evidence.
        attributed_speaker TEXT NOT NULL CHECK (attributed_speaker IN ({_tokens(AI_SPEAKER_LABELS)})),
        PRIMARY KEY (artifact_id, excerpt_id),
        -- The same source text is never cited twice in one artifact under different IDs.
        UNIQUE (artifact_id, interaction_id, text),
        FOREIGN KEY (artifact_id, interaction_id)
            REFERENCES ai_artifacts (artifact_id, interaction_id) ON DELETE RESTRICT
    ) STRICT""",
    ]


# ASCII whitespace for SQL trim(); the store also refuses any Unicode whitespace-only text.
_SQL_WHITESPACE = "' ' || char(9, 10, 11, 12, 13)"


def _ai_provenance_triggers() -> list[str]:
    return [
        # A model-dependent status agrees with the run's explicitly requested model.
        """
    CREATE TRIGGER ai_run_results_model_binding
    BEFORE INSERT ON ai_run_results
    WHEN (NEW.status = 'success'
          AND NEW.response_model IS NOT (SELECT requested_model FROM ai_runs WHERE ai_run_id = NEW.ai_run_id))
        OR (NEW.status = 'model_mismatch'
          AND NEW.response_model IS (SELECT requested_model FROM ai_runs WHERE ai_run_id = NEW.ai_run_id))
    BEGIN
        SELECT RAISE(ABORT, 'ai_run_results response model disagrees with its status');
    END""",
        # Closed failure codes (Phase 6D design §5.1). failure_codes must be a JSON array
        # of text: exactly one parse code under parse_failure; one or more codes from the
        # run's validator-version vocabulary under semantic_validation_failure, strictly
        # increasing (sorted and unique); NULL under every other status. CASE keeps the
        # JSON functions away from invalid JSON. Canonical spelling is checked by the store.
        f"""
    CREATE TRIGGER ai_run_results_failure_codes_closed
    BEFORE INSERT ON ai_run_results
    WHEN NOT COALESCE(CASE
        WHEN NEW.status = 'parse_failure' THEN CASE
            WHEN json_valid(NEW.failure_codes) AND json_type(NEW.failure_codes) = 'array' THEN
                json_array_length(NEW.failure_codes) = 1
                AND json_type(NEW.failure_codes, '$[0]') = 'text'
                AND json_extract(NEW.failure_codes, '$[0]') IN ({_tokens(AI_PARSE_FAILURE_CODES)})
            ELSE 0 END
        WHEN NEW.status = 'semantic_validation_failure' THEN CASE
            WHEN json_valid(NEW.failure_codes) AND json_type(NEW.failure_codes) = 'array' THEN
                json_array_length(NEW.failure_codes) >= 1
                AND NOT EXISTS (
                    SELECT 1 FROM json_each(NEW.failure_codes) AS code
                    WHERE code.type <> 'text' OR NOT COALESCE({_semantic_vocabulary_sql()}, 0)
                )
                AND NOT EXISTS (
                    SELECT 1 FROM json_each(NEW.failure_codes) AS earlier
                    JOIN json_each(NEW.failure_codes) AS later ON later.key = earlier.key + 1
                    WHERE NOT COALESCE(earlier.value < later.value, 0)
                )
            ELSE 0 END
        ELSE NEW.failure_codes IS NULL
    END, 0)
    BEGIN
        SELECT RAISE(ABORT, 'ai_run_results failure_codes are outside the closed vocabulary for the status');
    END""",
        # Returned text is stored only under a result that names the same digest.
        """
    CREATE TRIGGER ai_run_outputs_require_result
    BEFORE INSERT ON ai_run_outputs
    WHEN NOT EXISTS (
        SELECT 1 FROM ai_run_results
        WHERE ai_run_id = NEW.ai_run_id AND output_digest = NEW.output_digest
    )
    BEGIN
        SELECT RAISE(ABORT, 'ai_run_outputs requires a result that records the same output digest');
    END""",
        # An artifact exists only for a successful run that has its raw output.
        """
    CREATE TRIGGER ai_artifacts_require_success
    BEFORE INSERT ON ai_artifacts
    WHEN NOT EXISTS (
        SELECT 1 FROM ai_run_results AS result
        JOIN ai_run_outputs AS output ON output.ai_run_id = result.ai_run_id
        WHERE result.ai_run_id = NEW.ai_run_id AND result.status = 'success'
            AND output.output_digest = result.output_digest
    )
    BEGIN
        SELECT RAISE(ABORT, 'ai_artifacts requires a successful run with its raw output');
    END""",
        # Evidence fidelity, as for interaction_evidence: non-blank, exact case-sensitive substring.
        f"""
    CREATE TRIGGER ai_artifact_excerpts_verbatim
    BEFORE INSERT ON ai_artifact_excerpts
    WHEN trim(NEW.text, {_SQL_WHITESPACE}) = ''
        OR NOT COALESCE(
            instr((SELECT text FROM interactions WHERE interaction_id = NEW.interaction_id), NEW.text) > 0,
            0
        )
    BEGIN
        SELECT RAISE(ABORT, 'ai_artifact_excerpts text must occur verbatim in its interaction');
    END""",
    ]


def _semantic_vocabulary_sql() -> str:
    """code.value is legal under the validator version recorded by NEW's run."""
    run_version = "(SELECT validation_version FROM ai_runs WHERE ai_run_id = NEW.ai_run_id)"
    return "(" + " OR ".join(
        f"({run_version} = '{version}' AND code.value IN ({_tokens(codes)}))"
        for version, codes in AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION.items()
    ) + ")"


def check_sqlite_version(version_info: tuple[int, ...] | None = None) -> None:
    """Refuse to run on an SQLite too old for STRICT tables and built-in JSON functions."""
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
    """One immediate write transaction: commit on success, roll back on any error.

    A failure of COMMIT itself (a deferred constraint, or a busy database) is
    handled like a failure of the body: the transaction is rolled back, so the
    connection is never left inside it, and the original error is re-raised.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException as error:
        if connection.in_transaction:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error as rollback_error:
                error.add_note(f"rolling back also failed: {rollback_error}")
        raise


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
