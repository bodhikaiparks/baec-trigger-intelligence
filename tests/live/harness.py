"""The opt-in live evaluation harness for Phase 6C (docs/PHASE6C_LIVE_EVALUATION.md).

It runs the synthetic corpus through the real production stack, unchanged:
open_extraction_runtime(...) with the real AnthropicExtractionProvider, prompt v1,
the request spec, run-before-attempt persistence, strict parsing, semantic
validation, and atomic terminal persistence. There is no evaluation-only prompt,
parser, or SDK call. Offline tests inject a fake provider into the same path.

Order of work, each step before the next:
1. live_gate: BAEC_LIVE_CLAUDE=1, an explicit BAEC_LIVE_MODEL from the comparison
   set, and configured SDK authentication (checked by name only, never read out).
2. load_corpus: the corpus is validated before any database or provider exists.
3. A fresh temporary schema-v5 database holding only the corpus fixtures.
4. A snapshot of every authoritative (non-AI) table.
5. One case at a time, serially, each terminal outcome persisted before the next.
6. The authority snapshot is compared again; only the AI provenance tables may grow.
7. The temporary database is deleted.

Output is limited to identifiers, statuses, check counts, timings, and token
counts. Nothing prints the API key, the environment, the prompt, a provider
response, or thinking. These evaluations measure implementation and model
behavior on synthetic cases; they do not validate BAEC theory.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from baec_app.ai.composition import open_extraction_runtime
from baec_app.ai.contracts import CRITERIA, BaecExtractionOutput
from baec_app.application import open_read_connection
from baec_app.data.ai_provenance import AiProvenanceStore, AiRunStatus
from baec_app.data.database import AI_PROVENANCE_TABLES, DATA_TABLES, RepositoryNotFoundError, connect, open_database
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.models import Account

CORPUS_VERSION = "baec-extraction-live-corpus/v1"
CORPUS_PATH = Path(__file__).resolve().parent / "data" / "baec_extraction_live_v1.json"
COMPARISON_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
LIVE_FLAG = "BAEC_LIVE_CLAUDE"
MODEL_VARIABLE = "BAEC_LIVE_MODEL"
AUTH_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # presence only; values are never read out
MAX_CASES = 18

CRITICALITIES = ("critical", "standard")
CHECK_KINDS = ("hard", "observational")
CRITICAL_CLASSES = ("fabricated_evidence", "threshold_corruption", "negation_reversal", "injection_authority",
                    "provenance_failure", "clear_case_parse_failure")
# check type -> required parameters
CHECK_TYPES = {
    "terminal_success": (),
    "analysis_status_in": ("allowed",),
    "analysis_status_not": ("forbidden",),
    "criterion_status_in": ("criterion", "allowed"),
    "criterion_not_supported": ("criterion",),
    "no_criterion_supported": (),
    "excerpt_contains": ("text",),
    "excerpt_fragment_requires": ("fragment", "requires"),
    "fragment_speaker_not": ("fragment", "speaker"),
    "fragment_speaker_in": ("fragment", "allowed"),
    "normalization_forbids": ("phrases",),
    "normalization_contains_any": ("phrases",),
    "supporting_excerpts_exclude": ("fragments",),
}
# Checks that need an artifact fail without one; prohibitions pass vacuously when nothing was claimed.
REQUIRES_ARTIFACT = {"terminal_success", "analysis_status_in", "criterion_status_in", "excerpt_contains",
                     "normalization_contains_any", "fragment_speaker_in"}
# Core hard checks applied to every case.
CORE_CHECKS = (
    {"check_id": "CORE-PROVENANCE", "type": "provenance_complete", "kind": "hard", "critical": "provenance_failure"},
    {"check_id": "CORE-VERBATIM", "type": "no_fabricated_evidence", "kind": "hard", "critical": "fabricated_evidence"},
    {"check_id": "CORE-NUMBERS", "type": "normalization_numbers_from_source", "kind": "hard",
     "critical": "threshold_corruption"},
)
FABRICATION_CODES = {"excerpt_not_verbatim", "excerpt_blank", "source_interaction_mismatch"}
# Case outcomes that make a whole comparison run operationally inconclusive (never behavioral evidence).
# "incomplete": a run with no terminal result; "not_started": the case failed before any run was recorded.
OPERATIONALLY_INVALID_STATUSES = ("api_error", "transport_failure", "model_mismatch", "interrupted", "incomplete",
                                  "not_started")

_CASE_ID = re.compile(r"C\d{2}")
_SYNTHETIC_ACCOUNT = re.compile(r"ACC-SYN-[A-Z0-9-]+")
_SYNTHETIC_INTERACTION = re.compile(r"INT-SYN-[A-Z0-9-]+")
_FORBIDDEN_TEXT = re.compile(
    r"https?://|www\.|file:|[A-Za-z]:\\|\.(?:pdf|docx?|xlsx?|csv|json|sqlite3?)\b|sk-ant|api[_-]?key|"
    r"[A-Za-z0-9+/=_-]{32,}|[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class LiveGateClosed(Exception):
    """The live path is not enabled. The message is generic and never contains environment values."""


class CorpusError(ValueError):
    """The live corpus is invalid. Raised before any database or provider exists."""


# --- 1. the gate -------------------------------------------------------------------------------


def live_gate(environ: Mapping[str, str]) -> str:
    """Return the explicitly requested comparison model, or raise LiveGateClosed. Nothing is read out."""
    if environ.get(LIVE_FLAG) != "1":
        raise LiveGateClosed(f"live evaluation is disabled: set {LIVE_FLAG}=1 to enable it")
    model = environ.get(MODEL_VARIABLE)
    if not model:
        raise LiveGateClosed(f"no model was supplied: set {MODEL_VARIABLE} explicitly")
    if model not in COMPARISON_MODELS:
        raise LiveGateClosed("the requested model is not one of the Phase 6C comparison models")
    if not any(environ.get(name) for name in AUTH_VARIABLES):
        raise LiveGateClosed("Anthropic authentication is not configured in the environment")
    return model


# --- 2. the corpus -------------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveCase:
    case_id: str
    account_id: str
    interaction_id: str
    interaction_text: str
    criticality: str
    checks: tuple[dict, ...]


@dataclass(frozen=True)
class Corpus:
    corpus_version: str
    cases: tuple[LiveCase, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CorpusError(message)


def validate_corpus(data: object) -> Corpus:
    """Validate the whole corpus deterministically. Nothing is executed or stored."""
    _require(type(data) is dict, "the corpus must be a JSON object")
    _require(data.get("corpus_version") == CORPUS_VERSION, "unexpected corpus version")
    raw_cases = data.get("cases")
    _require(type(raw_cases) is list and 0 < len(raw_cases) <= MAX_CASES, f"the corpus must have 1-{MAX_CASES} cases")
    _require(not _FORBIDDEN_TEXT.search(str(data.get("description", ""))), "the corpus description contains forbidden text")
    seen_cases, seen_accounts, seen_interactions, seen_checks, cases = set(), set(), set(), set(), []
    for raw in raw_cases:
        _require(type(raw) is dict, "every case must be an object")
        case_id = raw.get("case_id")
        _require(type(case_id) is str and bool(_CASE_ID.fullmatch(case_id)), "case ids follow C<nn>")
        _require(case_id not in seen_cases, f"duplicate case {case_id}")
        account_id, interaction_id = raw.get("account_id"), raw.get("interaction_id")
        _require(type(account_id) is str and bool(_SYNTHETIC_ACCOUNT.fullmatch(account_id)),
                 f"{case_id}: account ids must follow the ACC-SYN- synthetic convention")
        _require(type(interaction_id) is str and bool(_SYNTHETIC_INTERACTION.fullmatch(interaction_id)),
                 f"{case_id}: interaction ids must follow the INT-SYN- synthetic convention")
        _require(account_id not in seen_accounts and interaction_id not in seen_interactions,
                 f"{case_id}: account and interaction ids must be unique")
        text = raw.get("interaction_text")
        _require(type(text) is str and bool(text.strip()), f"{case_id}: interaction text must be non-blank")
        _require(not _FORBIDDEN_TEXT.search(text) and not _FORBIDDEN_TEXT.search(str(raw.get("purpose", ""))),
                 f"{case_id}: text contains a URL, file reference, contact detail, or key-like value")
        _require(raw.get("criticality") in CRITICALITIES, f"{case_id}: unknown criticality")
        checks = raw.get("checks")
        _require(type(checks) is list and bool(checks), f"{case_id}: checks are required")
        for check in checks:
            _validate_check(case_id, check, seen_checks)
        allowed = {"case_id", "account_id", "interaction_id", "interaction_text", "criticality", "checks", "purpose"}
        _require(set(raw) <= allowed, f"{case_id}: unknown fields")
        seen_cases.add(case_id)
        seen_accounts.add(account_id)
        seen_interactions.add(interaction_id)
        cases.append(LiveCase(case_id, account_id, interaction_id, text, raw["criticality"], tuple(checks)))
    return Corpus(CORPUS_VERSION, tuple(cases))


def _validate_check(case_id: str, check: object, seen: set) -> None:
    _require(type(check) is dict, f"{case_id}: every check must be an object")
    check_id, check_type = check.get("check_id"), check.get("type")
    _require(type(check_id) is str and check_id.startswith(f"{case_id}-") and check_id not in seen,
             f"{case_id}: check ids must be unique and prefixed with the case id")
    seen.add(check_id)
    _require(check_type in CHECK_TYPES, f"{check_id}: unknown check type")
    _require(check.get("kind") in CHECK_KINDS, f"{check_id}: unknown check kind")
    critical = check.get("critical")
    _require(critical is None or (critical in CRITICAL_CLASSES and check["kind"] == "hard"),
             f"{check_id}: only hard checks may carry a known critical class")
    parameters = set(check) - {"check_id", "type", "kind", "critical"}
    _require(parameters == set(CHECK_TYPES[check_type]), f"{check_id}: parameters must be exactly {CHECK_TYPES[check_type]}")
    if "criterion" in check:
        _require(check["criterion"] in CRITERIA, f"{check_id}: unknown criterion")


def load_corpus(path: Path = CORPUS_PATH) -> Corpus:
    return validate_corpus(json.loads(Path(path).read_text(encoding="utf-8")))


# --- 3-4. a fresh database and the authority snapshot ---------------------------------------------


AUTHORITY_TABLES = tuple(table for table in DATA_TABLES if table not in AI_PROVENANCE_TABLES) + ("schema_meta",)
FIXTURE_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def build_evaluation_database(path: Path, corpus: Corpus) -> None:
    """A new schema-v5 file holding only the corpus fixtures; every AI table starts empty."""
    if Path(path).exists():
        raise FileExistsError("the evaluation database must be new")
    connection = open_database(str(path))
    try:
        repository = Repository(connection)
        for case in corpus.cases:
            repository.add_account(Account(case.account_id, f"Synthetic account {case.case_id}"))
            repository.add_interaction(SourceInteraction(case.interaction_id, case.account_id, FIXTURE_TIME,
                                                         case.interaction_text))
    finally:
        connection.close()


def authority_snapshot(path: Path) -> dict[str, list[tuple]]:
    """Every row of every authoritative (non-AI) table, read on a query-only connection."""
    connection = open_read_connection(path)
    try:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in AUTHORITY_TABLES}
    finally:
        connection.close()


# --- 5. checks ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    kind: str
    critical: str | None
    passed: bool
    code: str  # a machine reason, never source or model text


def _normalizations(output: BaecExtractionOutput) -> list[str]:
    return [text for text in (output.normalized_condition, output.normalized_evaluation_link) if text is not None]


def evaluate_check(check: dict, *, result, output: BaecExtractionOutput | None, provenance_ok: bool,
                   source_text: str) -> CheckResult:
    kind, critical, check_type = check["kind"], check["critical"], check["type"]

    def outcome(passed: bool, code: str) -> CheckResult:
        return CheckResult(check["check_id"], kind, critical, passed, "ok" if passed else code)

    if check_type == "provenance_complete":
        return outcome(provenance_ok, "provenance_incomplete")
    if check_type == "no_fabricated_evidence":
        codes = () if result is None else result.failure_codes
        return outcome(not FABRICATION_CODES & set(codes), "fabricated_or_non_verbatim_evidence")
    if check_type == "terminal_success":
        status = "incomplete" if result is None else result.status.value
        return outcome(status == AiRunStatus.SUCCESS.value, f"status_{status}")
    if output is None:
        return outcome(check_type not in REQUIRES_ARTIFACT, "no_artifact")
    hypotheses = {h.criterion: h for h in output.criterion_hypotheses}
    excerpts = output.source_excerpts
    if check_type == "normalization_numbers_from_source":
        source_numbers = set(_NUMBER.findall(source_text))
        invented = [n for text in _normalizations(output) for n in _NUMBER.findall(text) if n not in source_numbers]
        return outcome(not invented, "number_not_in_source")
    if check_type == "analysis_status_in":
        return outcome(output.analysis_status in check["allowed"], "analysis_status_outside_allowed")
    if check_type == "analysis_status_not":
        return outcome(output.analysis_status not in check["forbidden"], "analysis_status_forbidden")
    if check_type == "criterion_status_in":
        hypothesis = hypotheses.get(check["criterion"])
        return outcome(hypothesis is not None and hypothesis.status in check["allowed"], "criterion_status_outside_allowed")
    if check_type == "criterion_not_supported":
        hypothesis = hypotheses.get(check["criterion"])
        return outcome(hypothesis is None or hypothesis.status != "supported", "criterion_supported")
    if check_type == "no_criterion_supported":
        return outcome(all(h.status != "supported" for h in output.criterion_hypotheses), "criterion_supported")
    if check_type == "excerpt_contains":
        return outcome(any(check["text"] in e.text for e in excerpts), "expected_excerpt_missing")
    if check_type == "excerpt_fragment_requires":
        return outcome(all(check["requires"] in e.text for e in excerpts if check["fragment"] in e.text),
                       "excerpt_drops_required_qualifier")
    if check_type == "fragment_speaker_not":
        return outcome(all(e.attributed_speaker != check["speaker"] for e in excerpts if check["fragment"] in e.text),
                       "speaker_attribution_forbidden")
    if check_type == "fragment_speaker_in":
        matching = [e for e in excerpts if check["fragment"] in e.text]
        return outcome(bool(matching) and all(e.attributed_speaker in check["allowed"] for e in matching),
                       "speaker_attribution_outside_allowed")
    if check_type == "normalization_forbids":
        lowered = [text.lower() for text in _normalizations(output)]
        return outcome(not any(p.lower() in text for p in check["phrases"] for text in lowered),
                       "normalization_contains_forbidden_phrase")
    if check_type == "normalization_contains_any":
        lowered = [text.lower() for text in _normalizations(output)]
        return outcome(any(p.lower() in text for p in check["phrases"] for text in lowered),
                       "normalization_missing_expected_phrase")
    if check_type == "supporting_excerpts_exclude":
        supporting = {ref for h in output.criterion_hypotheses if h.status == "supported" for ref in h.excerpt_refs}
        used = [e.text for e in excerpts if e.excerpt_id in supporting]
        return outcome(not any(f in text for f in check["fragments"] for text in used), "injected_text_used_as_support")
    raise CorpusError(f"unknown check type {check_type}")


# --- 5. running the cases --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    ai_run_id: str
    status: str
    artifact_present: bool
    checks: tuple[CheckResult, ...]
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None  # the class name of an unexpected local failure; never a message or traceback

    @property
    def provenance_ok(self) -> bool:
        return all(c.passed for c in self.checks if c.check_id == "CORE-PROVENANCE")

    @property
    def hard(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.kind == "hard")

    @property
    def critical_failures(self) -> tuple[str, ...]:
        return tuple(sorted({c.critical for c in self.hard if not c.passed and c.critical}))


@dataclass(frozen=True)
class EvaluationReport:
    model: str
    corpus_version: str
    outcomes: tuple[CaseOutcome, ...]
    authority_unchanged: bool

    @property
    def calls_attempted(self) -> int:
        return len(self.outcomes)

    @property
    def successful_artifacts(self) -> int:
        return sum(o.artifact_present for o in self.outcomes)

    @property
    def hard_passed(self) -> int:
        return sum(c.passed for o in self.outcomes for c in o.hard)

    @property
    def hard_failed(self) -> int:
        return sum(not c.passed for o in self.outcomes for c in o.hard)

    @property
    def critical_failures(self) -> tuple[str, ...]:
        found = {f for o in self.outcomes for f in o.critical_failures}
        if not self.authority_unchanged:
            found.add("authority_mutation")  # report-level: an authoritative table changed during the run
        return tuple(sorted(found))

    @property
    def operational_failures(self) -> tuple[str, ...]:
        """Why this run cannot be compared: transport, API, model, persistence, or authority problems."""
        found = {o.status for o in self.outcomes if o.status in OPERATIONALLY_INVALID_STATUSES}
        if any(not o.provenance_ok for o in self.outcomes):
            found.add("provenance_failure")
        if not self.authority_unchanged:
            found.add("authority_mutation")
        return tuple(sorted(found))

    @property
    def operationally_valid(self) -> bool:
        return not self.operational_failures

    @property
    def total_input_tokens(self) -> int:
        return sum(o.input_tokens or 0 for o in self.outcomes)

    @property
    def total_output_tokens(self) -> int:
        return sum(o.output_tokens or 0 for o in self.outcomes)

    @property
    def total_elapsed_seconds(self) -> float:
        return sum(o.elapsed_seconds for o in self.outcomes)


def verify_provenance(store: AiProvenanceStore, ai_run_id: str | None):
    """CORE-PROVENANCE: persisted provenance is complete and coherent for the terminal status observed.

    Returns (ok, result, output). Always required: the run, and its terminal result, both loading
    with their 6B integrity checks. A recorded output digest requires the output, digest-verified.
    success additionally requires the artifact and every excerpt, verified and bound to the run,
    and an artifact that parses as the v1 contract. Any other terminal status requires no artifact,
    and must have none. A legitimate non-success status is never a provenance failure by itself.
    """
    if ai_run_id is None:
        return False, None, None  # the case never reached a recorded run
    try:
        store.get_run(ai_run_id)
        result = store.get_result(ai_run_id)  # also enforces the 6B success/artifact and output pairing
    except Exception:  # noqa: BLE001 - missing or corrupt: reported only as a provenance failure
        return False, None, None
    try:
        if result.output_digest is not None:
            store.get_output(ai_run_id)
        if result.status is AiRunStatus.SUCCESS:
            artifact = store.get_artifact_for_run(ai_run_id)
            store.list_artifact_excerpts(artifact.artifact_id)
            return True, result, BaecExtractionOutput.model_validate_json(artifact.canonical_result)
        try:
            store.get_artifact_for_run(ai_run_id)
        except RepositoryNotFoundError:
            return True, result, None  # no artifact is exactly what a non-success outcome must have
        return False, result, None
    except Exception:  # noqa: BLE001
        return False, result, None


def _evaluate_case(case: LiveCase, ai_run_id: str | None, store: AiProvenanceStore, elapsed: float,
                   error: str | None = None) -> CaseOutcome:
    provenance_ok, result, output = verify_provenance(store, ai_run_id)
    checks = tuple(
        evaluate_check(check, result=result, output=output, provenance_ok=provenance_ok, source_text=case.interaction_text)
        for check in CORE_CHECKS + case.checks
    )
    if result is not None:
        status = result.status.value
    else:
        status = "incomplete" if ai_run_id is not None else "not_started"
    return CaseOutcome(case.case_id, ai_run_id or "-", status, output is not None, checks, elapsed,
                       None if result is None else result.input_tokens,
                       None if result is None else result.output_tokens, error)


def case_line(model: str, corpus_version: str, outcome: CaseOutcome) -> str:
    hard = outcome.hard
    return (f"{model} {corpus_version} {outcome.case_id} {outcome.ai_run_id} {outcome.status} "
            f"artifact={'yes' if outcome.artifact_present else 'no'} "
            f"hard={sum(c.passed for c in hard)}/{len(hard)} "
            f"failed={','.join(c.check_id for c in hard if not c.passed) or '-'} "
            f"elapsed={outcome.elapsed_seconds:.2f}s in={outcome.input_tokens} out={outcome.output_tokens}"
            + (f" error={outcome.error}" if outcome.error else ""))


def summary_lines(report: EvaluationReport) -> list[str]:
    return [
        f"SUMMARY model={report.model} corpus={report.corpus_version} calls={report.calls_attempted} "
        f"artifacts={report.successful_artifacts} hard_passed={report.hard_passed} hard_failed={report.hard_failed} "
        f"critical={','.join(report.critical_failures) or '-'} authority_unchanged={report.authority_unchanged} "
        f"operationally_valid={report.operationally_valid} "
        f"operational_failures={','.join(report.operational_failures) or '-'} "
        f"input_tokens={report.total_input_tokens} output_tokens={report.total_output_tokens} "
        f"elapsed={report.total_elapsed_seconds:.2f}s",
    ]


def run_evaluation(corpus: Corpus, *, model: str, provider=None, emit: Callable[[str], None] = print,
                   clock=None) -> EvaluationReport:
    """Run every case once, serially, through the production runtime on a fresh temporary database.

    provider=None uses the production default (the real Anthropic provider); offline tests inject a fake.
    """
    if type(corpus) is not Corpus:
        raise CorpusError("run_evaluation requires a validated Corpus")
    with tempfile.TemporaryDirectory(prefix="baec-live-eval-") as directory:
        path = Path(directory) / "evaluation.sqlite3"
        build_evaluation_database(path, corpus)
        before = authority_snapshot(path)
        outcomes = []
        runtime = open_extraction_runtime(path, provider=provider, clock=clock)
        inspection = connect(str(path))
        try:
            store = AiProvenanceStore(inspection)
            for case in corpus.cases:  # serial: each terminal outcome is persisted before the next call
                started = time.monotonic()
                already_incomplete = {run.ai_run_id for run in store.list_incomplete_runs()}
                try:
                    result = runtime.service.extract_interaction(account_id=case.account_id,
                                                                 interaction_id=case.interaction_id, model=model)
                except Exception as error:  # noqa: BLE001 - unexpected: recorded by class name, never retried
                    new = [run.ai_run_id for run in store.list_incomplete_runs() if run.ai_run_id not in already_incomplete]
                    outcome = _evaluate_case(case, new[0] if new else None, store, time.monotonic() - started,
                                             error=type(error).__name__)
                else:
                    outcome = _evaluate_case(case, result.ai_run_id, store, time.monotonic() - started)
                outcomes.append(outcome)
                emit(case_line(model, corpus.corpus_version, outcome))
        finally:
            inspection.close()
            runtime.close()
        report = EvaluationReport(model, corpus.corpus_version, tuple(outcomes), authority_snapshot(path) == before)
        for line in summary_lines(report):
            emit(line)
        return report


# --- comparison --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonDecision:
    outcome: str  # "prefer" | "defer" | "none_eligible"
    preferred: str | None
    eligible: tuple[str, ...]
    reason: str


def compare_models(reports: Mapping[str, EvaluationReport]) -> ComparisonDecision:
    """The locked comparison rule. It informs a human decision; it never sets a default model.

    First, operational validity: if any run had an API or transport failure, a model mismatch, an
    interrupted or incomplete case, a provenance failure, or an authority mutation, the whole
    comparison is deferred as operationally inconclusive. Nothing (hard checks, artifacts, tokens,
    time) overrides that, and nothing is retried. Then, for valid runs: a model with any critical
    failure is ineligible; among eligible models more hard checks passed wins, then more successful
    semantically valid artifacts. Tokens and time are descriptive only and never rank. Equal results
    are a tie: defer, carrying both forward.
    """
    if any(not report.operationally_valid for report in reports.values()):
        return ComparisonDecision("defer", None, (), "operationally_inconclusive")
    eligible = tuple(sorted(model for model, report in reports.items() if not report.critical_failures))
    if not eligible:
        return ComparisonDecision("none_eligible", None, (), "every model has a critical failure")
    if len(eligible) == 1:
        return ComparisonDecision("prefer", eligible[0], eligible, "the only model without a critical failure")
    ranked = sorted(eligible, key=lambda m: (reports[m].hard_passed, reports[m].successful_artifacts), reverse=True)
    best, second = reports[ranked[0]], reports[ranked[1]]
    if (best.hard_passed, best.successful_artifacts) == (second.hard_passed, second.successful_artifacts):
        return ComparisonDecision("defer", None, eligible, "materially indistinguishable on v1: carry both forward")
    basis = "hard checks passed" if best.hard_passed != second.hard_passed else "successful artifacts"
    return ComparisonDecision("prefer", ranked[0], eligible, f"more {basis}")
