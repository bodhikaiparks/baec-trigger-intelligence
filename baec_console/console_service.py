"""BAEC Console service layer: one workspace's product operations, with no Streamlit dependency.

A workspace is a directory holding three SQLite files:

    engine1.sqlite3   the released Engine 1 schema (baec_app.data.database.open_database)
    engine2.sqlite3   the released Engine 2 store (baec_app.engine2.persistence.open_engine2_database)
    console.sqlite3   the console-owned store (console_store.py)

The console runs two workspaces: "local" for accounts the user creates, and "synthetic" for demo data. Engine 1
and Engine 2 stores are append-only with no delete path, so synthetic data is kept in its own files and "reset"
removes only those files. User-created accounts live in other files and are never touched.

Every operation opens its connections, does its work through the engine adapters, and closes them. Authoritative
progression is always read back from the stored records, never from UI session state.
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from baec_app.application.context import Clock, SystemClock
from baec_app.data.database import RepositoryNotFoundError, open_database
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.enums import (
    AccountState,
    BaecClassification,
    NoPlausiblePathGround,
    ReviewAnswer,
)
from baec_app.domain.models import Account, EvaluationEvidence, NonEvaluationEvidence
from baec_app.engine2 import persistence as e2store
from baec_app.engine2.domain import CorrespondenceDimension, CorrespondenceOutcome
from baec_app.engine3.enums import (
    ArtifactOrigin,
    HumanReviewDecision,
    PreparationChannel,
    PreparationGoal,
)
from baec_app.engine3.errors import Engine3ValidationError

from . import engine1_adapter as e1a
from . import engine2_adapter as e2a
from . import engine3_adapter as e3a
from .console_store import ConsoleStore, open_console_store, transaction
from .deterministic_preparation import GENERATOR, build_artifacts

LOCAL = "local"
SYNTHETIC = "synthetic"
ATTESTATIONS = (
    ("supported", "Every factual statement is supported by the recorded grounding."),
    ("permitted", "Every fact stated to the buyer is permitted for external use."),
    ("no_state_claims", "Nothing claims the buyer is evaluating, intends to buy, or that an opportunity exists."),
    ("no_internal_exposure", "No internal-only source or monitoring method is exposed."),
)
STAGES = ("REMEMBER", "MONITOR", "RECOGNIZE", "PREPARE")
STATE_LABELS = {
    AccountState.ACTIVE_OPPORTUNITY: "Active Opportunity",
    AccountState.CONDITIONALLY_DORMANT: "Conditionally Dormant",
    AccountState.NO_PLAUSIBLE_PATH: "No Plausible Path",
    None: "Not classified",
}


class ConsoleError(Exception):
    """A console guard refused the action. The message says what must happen next."""


class PreparationRefused(ConsoleError):
    def __init__(self, message: str, report) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class TimelineItem:
    at: datetime
    title: str
    actor_kind: str  # Human | Engine 1 | Engine 2 | Engine 3 | System
    actor: str
    stage: str
    event_type: str
    detail: str = ""
    account_id: str = ""


@dataclass
class AccountView:
    """Everything the workspace page shows for one account, read from stored records."""

    account: Account
    meta: dict
    workspace: str
    interactions: list = field(default_factory=list)  # [(SourceInteraction, meta)]
    drafts: list = field(default_factory=list)
    baecs: list = field(default_factory=list)  # BaecRecord, in stored order
    judgments: dict = field(default_factory=dict)  # baec_id -> [PersistedDormancyJudgment]
    transitions: list = field(default_factory=list)
    current_baec: object = None  # the latest CONFIRMED_BAEC record, if any
    plan: object = None  # e2a.PlanRefs for current_baec
    plan_record: object = None  # MonitoringPlan
    sources: dict = field(default_factory=dict)
    observations: list = field(default_factory=list)
    superseded: dict = field(default_factory=dict)  # observation id -> superseding id
    measurements: list = field(default_factory=list)
    candidates: list = field(default_factory=list)  # [{"candidate", "reviews": [RecordedReview]}]
    requests: list = field(default_factory=list)
    preparations: list = field(default_factory=list)
    blocks: dict = field(default_factory=dict)  # Engine 2 review id -> human block decisions, in order
    stage: str = "REMEMBER"
    next_action: str = ""
    last_activity: datetime | None = None

    @property
    def display_name(self) -> str:
        return self.meta.get("display_name") or self.account.name

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic"))

    @property
    def pending_drafts(self) -> list:
        return [d for d in self.drafts if d["resolution"] is None]

    def verified_correspondences(self) -> list[dict]:
        """Every HUMAN_VERIFIED_CORRESPONDENCE review, in recording order. None is singled out as authoritative.

        Only these records can be selected for preparation; POSSIBLE and NO outcomes never qualify.
        """
        out = []
        for c in self.candidates:
            later = []
            for review, outcome in reversed(c["reviews"]):
                if outcome is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE:
                    out.append({"review": review, "candidate": c["candidate"], "later_reviews": list(reversed(later)),
                                "blocked": bool(self.blocks.get(review.review_id))
                                and self.blocks[review.review_id][-1]["action"] == "BLOCK",
                                "block_history": self.blocks.get(review.review_id, [])})
                later.append((review, outcome))
        return sorted(out, key=lambda v: v["review"].reviewed_at)


def _token() -> str:
    return uuid.uuid4().hex[:12].upper()


class ConsoleService:
    """Product operations for one workspace directory."""

    def __init__(self, root: str | Path, workspace: str, clock: Clock | None = None) -> None:
        if workspace not in (LOCAL, SYNTHETIC):
            raise ConsoleError(f"unknown workspace {workspace!r}")
        self.workspace = workspace
        self.directory = Path(root) / workspace
        self.clock = clock or SystemClock()
        self._prefix = "SYN-" if workspace == SYNTHETIC else ""
        self.initialize()

    # --- files and connections ------------------------------------------------------------------------------

    @property
    def engine1_path(self) -> str:
        return str(self.directory / "engine1.sqlite3")

    @property
    def engine2_path(self) -> str:
        return str(self.directory / "engine2.sqlite3")

    @property
    def console_path(self) -> str:
        return str(self.directory / "console.sqlite3")

    def initialize(self) -> None:
        """Create the workspace files on first use; verify their schemas on every later use."""
        self.directory.mkdir(parents=True, exist_ok=True)
        open_database(self.engine1_path).close()
        e2store.open_engine2_database(self.engine2_path).close()
        open_console_store(self.console_path).close()

    def destroy(self) -> None:
        """Remove this workspace's files. Used only for the synthetic workspace reset."""
        if self.workspace != SYNTHETIC:
            raise ConsoleError("only the synthetic workspace can be reset")
        shutil.rmtree(self.directory, ignore_errors=False)
        self.initialize()

    def ids(self, prefix: str) -> str:
        return f"{self._prefix}{prefix}-{_token()}"

    def now(self) -> datetime:
        return self.clock.now()

    @contextmanager
    def _e1(self) -> Iterator[e1a.Engine1Session]:
        with e1a.Engine1Session(self.engine1_path, self.clock) as session:
            yield session

    @contextmanager
    def _e1_repo(self) -> Iterator[Repository]:
        connection = open_database(self.engine1_path)
        try:
            yield Repository(connection)
        finally:
            connection.close()

    @contextmanager
    def _e2(self):
        connection = e2store.open_engine2_database(self.engine2_path)
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _cs(self) -> Iterator[ConsoleStore]:
        connection = open_console_store(self.console_path)
        try:
            yield ConsoleStore(connection)
        finally:
            connection.close()

    # --- preferences -------------------------------------------------------------------------------------------

    def get_pref(self, key: str, default: str) -> str:
        with self._cs() as cs:
            return cs.get_pref(key, default)

    def set_pref(self, key: str, value: str) -> None:
        with self._cs() as cs:
            cs.set_pref(key, value)

    # --- accounts --------------------------------------------------------------------------------------------

    def create_account(self, name: str, *, industry: str = "", owner_note: str = "", actor: str,
                       synthetic: bool = False) -> str:
        """A new, unclassified account (state None). Nothing about the buyer is inferred from creating it."""
        if not name or not name.strip():
            raise ConsoleError("Enter an account name.")
        if synthetic != (self.workspace == SYNTHETIC):
            raise ConsoleError("synthetic accounts belong only in the synthetic workspace")
        account_id = self.ids("ACC")
        engine_name = name.strip() + (" (synthetic)" if synthetic else "")
        now = self.now()
        with self._e1_repo() as repo:
            repo.add_account(Account(account_id, engine_name))
        with self._cs() as cs, transaction(cs.db):
            cs.add_account_meta(account_id, name.strip(), industry.strip() or None, owner_note.strip() or None,
                                synthetic, now, actor)
            cs.add_event(account_id, "ACCOUNT_CREATED", "System" if synthetic else "Human", actor, now,
                         "Synthetic account created" if synthetic else "Account created")
        return account_id

    def account_ids(self) -> list[str]:
        with self._e1_repo() as repo:
            return [a.account_id for a in repo.list_accounts()]

    def has_account(self, account_id: str) -> bool:
        return account_id in self.account_ids()

    # --- Engine 1: interactions and conditions ----------------------------------------------------------------

    def record_interaction(self, account_id: str, *, buyer_role: str, occurred_at: datetime, buyer_statement: str,
                           seller_notes: str = "", actor: str) -> str:
        if not buyer_statement or not buyer_statement.strip():
            raise ConsoleError("Record the buyer's exact statement.")
        statement = buyer_statement.strip()
        text = e1a.compose_interaction_text(buyer_role, statement, seller_notes)
        interaction_id = self.ids("INT")
        with self._e1_repo() as repo:
            repo.add_interaction(SourceInteraction(interaction_id, account_id, occurred_at, text))
        with self._cs() as cs:
            cs.add_interaction_meta(interaction_id, account_id, buyer_role.strip() or None, statement,
                                    seller_notes.strip() or None, self.now(), actor)
        return interaction_id

    def _candidate(self, repo: Repository, cs: ConsoleStore, draft: dict):
        interaction = repo.get_interaction(draft["interaction_id"])
        meta = cs.interaction_meta(draft["interaction_id"])
        if meta is None:
            raise ConsoleError("This interaction was not recorded through BAEC Console.")
        form = e1a.ConditionForm.from_dict(draft["form"])
        return e1a.build_candidate(draft["account_id"], interaction, meta["buyer_statement"], meta["buyer_role"], form)

    def capture_condition(self, account_id: str, interaction_id: str, form: e1a.ConditionForm, *, actor: str) -> str:
        """Store what the salesperson captured. Builds the Engine 1 candidate first, so invalid input is refused."""
        with self._e1_repo() as repo, self._cs() as cs:
            interaction = repo.get_interaction(interaction_id)
            if interaction.account_id != account_id:
                raise ConsoleError("That interaction belongs to a different account.")
            meta = cs.interaction_meta(interaction_id)
            if meta is None:
                raise ConsoleError("This interaction was not recorded through BAEC Console.")
            e1a.build_candidate(account_id, interaction, meta["buyer_statement"], meta["buyer_role"], form)
            draft_id = self.ids("DRAFT")
            now = self.now()
            with transaction(cs.db):
                cs.add_draft(draft_id, account_id, interaction_id, form.to_dict(), now, actor)
                cs.add_event(account_id, "CONDITION_CAPTURED", "Human", actor, now,
                             "Condition captured (not yet confirmed)", draft_id)
        return draft_id

    def preview_condition(self, draft_id: str) -> dict:
        """The locked classifier's preview of exactly what would be recorded. Writes nothing."""
        with self._e1() as session, self._cs() as cs:
            draft = cs.draft(draft_id)
            candidate = self._candidate(session.repository, cs, draft)
            preview = session.facade.preview_classification(candidate)
        return {"draft": draft, "candidate": candidate, "preview": preview,
                "fingerprint": e1a.fingerprint(candidate)}

    def _open_draft(self, cs: ConsoleStore, draft_id: str) -> dict:
        draft = cs.draft(draft_id)
        if draft["resolution"] is not None:
            raise ConsoleError("This captured condition has already been recorded.")
        return draft

    def confirm_condition(self, draft_id: str, *, displayed_fingerprint: str, actor: str) -> str:
        """Human confirmation through the Engine 1 gate. Only a confirmable candidate can be confirmed."""
        with self._e1() as session, self._cs() as cs:
            draft = self._open_draft(cs, draft_id)
            candidate = self._candidate(session.repository, cs, draft)
            if e1a.fingerprint(candidate) != displayed_fingerprint:
                raise ConsoleError("The condition changed after it was previewed. Preview it again before confirming.")
            human = session.human_session(actor)
            request = session.facade.request_baec_confirmation(human, candidate, captured_at=draft["captured_at"])
            record = session.approve_and_run(human, request, session.facade.confirm_baec)
            cs.resolve_draft(draft_id, "CONFIRMED", record.baec_id, self.now(), actor)
        return record.baec_id

    def record_not_confirmable(self, draft_id: str, *, displayed_fingerprint: str, actor: str) -> str:
        """Preserve a NOT_BAEC or INSUFFICIENT_EVIDENCE classification with its reason (RC-10, RC-13)."""
        with self._e1() as session, self._cs() as cs:
            draft = self._open_draft(cs, draft_id)
            candidate = self._candidate(session.repository, cs, draft)
            if e1a.fingerprint(candidate) != displayed_fingerprint:
                raise ConsoleError("The condition changed after it was previewed. Preview it again.")
            record = session.facade.save_nonconfirmed_classification(candidate, captured_at=draft["captured_at"])
            cs.resolve_draft(draft_id, "RECORDED_NOT_CONFIRMABLE", record.baec_id, self.now(), actor)
        return record.baec_id

    def discard_draft(self, draft_id: str, *, actor: str) -> None:
        with self._cs() as cs:
            draft = self._open_draft(cs, draft_id)
            now = self.now()
            with transaction(cs.db):
                cs.resolve_draft(draft_id, "DISCARDED", None, now, actor)
                cs.add_event(draft["account_id"], "CONDITION_DISCARDED", "Human", actor, now,
                             "Captured condition discarded", draft_id)

    # --- Engine 1: account state (all through the human gate) ---------------------------------------------------

    def record_dormancy_judgment(self, baec_id: str, *, plausibility: ReviewAnswer, addressability: ReviewAnswer,
                                 notes: str, actor: str) -> int:
        with self._e1() as session:
            human = session.human_session(actor)
            request = session.facade.request_dormancy_judgment(human, baec_id, plausibility=plausibility,
                                                               addressability=addressability,
                                                               notes=notes.strip() or None)
            return session.approve_and_run(human, request, session.facade.record_dormancy_judgment).judgment_id

    def _evidence(self, repo: Repository, cs: ConsoleStore, kind: type, account_id: str, interaction_id: str,
                  excerpt: str):
        if not interaction_id or not excerpt or not excerpt.strip():
            return None
        interaction = repo.get_interaction(interaction_id)
        meta = cs.interaction_meta(interaction_id)
        statement = meta["buyer_statement"] if meta else ""
        return e1a.build_evidence(kind, account_id, interaction, statement, excerpt.strip(), interaction.occurred_at)

    def _transition_inputs(self, session: e1a.Engine1Session, cs: ConsoleStore, account_id: str, inputs: dict) -> dict:
        repo = session.repository
        return {
            "non_evaluation_evidence": self._evidence(repo, cs, NonEvaluationEvidence, account_id,
                                                      inputs.get("non_eval_interaction", ""),
                                                      inputs.get("non_eval_excerpt", "")),
            "evaluation_evidence": self._evidence(repo, cs, EvaluationEvidence, account_id,
                                                  inputs.get("eval_interaction", ""), inputs.get("eval_excerpt", "")),
        }

    def preview_state_change(self, account_id: str, target: AccountState, inputs: dict):
        """The locked state machine's answer, without authorization. Writes nothing."""
        with self._e1() as session, self._cs() as cs:
            ev = self._transition_inputs(session, cs, account_id, inputs)
            f = session.facade
            if target is AccountState.CONDITIONALLY_DORMANT:
                return f.preview_move_to_conditionally_dormant(
                    account_id, baec_id=inputs["baec_id"], judgment_id=inputs["judgment_id"],
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
            if target is AccountState.NO_PLAUSIBLE_PATH:
                return f.preview_move_to_no_plausible_path(
                    account_id, ground=inputs.get("ground"), reason=(inputs.get("reason") or "").strip() or None,
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
            return f.preview_move_to_active_opportunity(account_id, evaluation_evidence=ev["evaluation_evidence"])

    def change_account_state(self, account_id: str, target: AccountState, inputs: dict, *, actor: str):
        """Request, human approval, and the approval-only Engine 1 command. The state machine decides."""
        with self._e1() as session, self._cs() as cs:
            ev = self._transition_inputs(session, cs, account_id, inputs)
            f = session.facade
            human = session.human_session(actor)
            if target is AccountState.CONDITIONALLY_DORMANT:
                request = f.request_move_to_conditionally_dormant(
                    human, account_id, baec_id=inputs["baec_id"], judgment_id=inputs["judgment_id"],
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
                return session.approve_and_run(human, request, f.move_to_conditionally_dormant)
            if target is AccountState.NO_PLAUSIBLE_PATH:
                ground = inputs.get("ground")
                request = f.request_move_to_no_plausible_path(
                    human, account_id, ground=ground if isinstance(ground, NoPlausiblePathGround) else None,
                    reason=(inputs.get("reason") or "").strip() or None,
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
                return session.approve_and_run(human, request, f.move_to_no_plausible_path)
            request = f.request_move_to_active_opportunity(human, account_id,
                                                           evaluation_evidence=ev["evaluation_evidence"])
            return session.approve_and_run(human, request, f.move_to_active_opportunity)

    # --- Engine 2: monitoring and correspondence ----------------------------------------------------------------

    def handoff(self, baec_id: str):
        """The Stage K handoff, or HandoffRefused explaining why monitoring is not eligible."""
        return e2a.read_handoff(self.engine1_path, baec_id)

    def start_monitoring(self, account_id: str, baec_id: str, *, target_entities: tuple[str, ...],
                         required: frozenset[CorrespondenceDimension], sources: tuple[e2a.SourceForm, ...],
                         actor: str) -> e2a.PlanRefs:
        handoff = self.handoff(baec_id)
        if handoff.account_id != account_id:
            raise ConsoleError("That condition belongs to a different account.")
        entities = tuple(dict.fromkeys(e.strip() for e in target_entities if e and e.strip()))
        if not entities:
            raise ConsoleError("Name at least one organization to monitor.")
        with self._e2() as e2:
            if any(p.activation_result == "ACTIVATED" for p in e2a.plans_for_baec(e2, baec_id)):
                raise ConsoleError("Monitoring is already active for this condition.")
            return e2a.start_monitoring(e2, handoff, ids=self.ids, target_entities=entities, required=required,
                                        sources=sources, actor=actor, now=self.now())

    def add_observation(self, baec_id: str, form: e2a.ObservationForm, *, actor: str) -> e2a.ObservationResult:
        with self._e2() as e2:
            plans = [p for p in e2a.plans_for_baec(e2, baec_id) if p.activation_result == "ACTIVATED"]
            if not plans:
                raise ConsoleError("Monitoring is not active for this condition. Start monitoring first.")
            return e2a.add_observation(e2, plans[-1], form, ids=self.ids, actor=actor, now=self.now())

    def review_packet(self, candidate_id: str):
        with self._e2() as e2:
            return e2a.packet(e2, candidate_id)

    def submit_correspondence_review(self, candidate_id: str, *, displayed_context_digest: str,
                                     findings: dict, notes: dict, sufficiency: str,
                                     revalidation_triggers: tuple[str, ...] = (),
                                     check_notes: dict | None = None, actor: str):
        with self._e2() as e2:
            return e2a.submit_review(e2, candidate_id, displayed_context_digest=displayed_context_digest,
                                     findings=findings, notes=notes, sufficiency=sufficiency,
                                     revalidation_triggers=revalidation_triggers,
                                     check_notes=check_notes or {}, ids=self.ids, actor=actor, now=self.now())

    # --- Engine 3: preparation -----------------------------------------------------------------------------------

    def preparation_snapshot(self, review_id: str) -> dict:
        """Engine 3's upstream view of exactly this review, including any human preparation block on it."""
        with self._cs() as cs:
            blocked = cs.is_blocked(review_id)
        with self._e2() as e2:
            return e3a.upstream_snapshot(self.engine1_path, e2, review_id, blocked=blocked)

    def _require_verified(self, review_id: str) -> dict:
        snap = self.preparation_snapshot(review_id)
        if not snap["lineage_reconstructable"]:
            raise ConsoleError("That correspondence record could not be reconstructed.")
        if not e3a.is_verified(snap["outcome"]):
            raise ConsoleError("Only human-verified correspondence can be selected for preparation.")
        return snap

    def block_preparation(self, review_id: str, *, reason: str, actor: str) -> None:
        """A human decision that no preparation may be drafted from this correspondence. Append-only."""
        self._set_block(review_id, "BLOCK", reason, actor)

    def unblock_preparation(self, review_id: str, *, reason: str, actor: str) -> None:
        """A new, separate human decision lifting an earlier block. The earlier decision stays on record."""
        self._set_block(review_id, "UNBLOCK", reason, actor)

    def _set_block(self, review_id: str, action: str, reason: str, actor: str) -> None:
        snap = self._require_verified(review_id)
        now = self.now()
        with self._cs() as cs:
            if cs.is_blocked(review_id) == (action == "BLOCK"):
                raise ConsoleError("Preparation is already blocked for this correspondence." if action == "BLOCK"
                                   else "Preparation is not blocked for this correspondence.")
            with transaction(cs.db):
                cs.add_block_decision(snap["account_id"], review_id, action, reason.strip() or None, now, actor)
                cs.add_event(snap["account_id"], f"PREPARATION_{action}ED", "Human", actor, now,
                             ("Preparation blocked by human decision" if action == "BLOCK"
                              else "Preparation block lifted by human decision")
                             + f" (correspondence verified {snap['reviewed_on']})", review_id)

    def preparation_eligibility(self, review_id: str):
        """Engine 3's eligibility answer for a review, assuming the human then requests preparation."""
        snap = self.preparation_snapshot(review_id)
        return snap, e3a.eligibility_report(snap, request_present=True)

    def request_preparation(self, review_id: str, request: dict, *, actor: str) -> str:
        """A separate human Preparation Request. It authorizes drafting only, never outreach."""
        self._require_verified(review_id)  # an explicit, qualifying correspondence record; never substituted
        snap, report = self.preparation_eligibility(review_id)
        if not report.allowed:
            raise PreparationRefused("Preparation is not eligible.", report)
        PreparationChannel(request["channel"])
        PreparationGoal(request["goal"])
        if request.get("statement_use") not in ("PARAPHRASE", "QUOTE"):
            raise ConsoleError("Choose how drafts may refer to the buyer's earlier statement.")
        if request["statement_use"] == "PARAPHRASE" and not (request.get("paraphrase") or "").strip():
            raise ConsoleError("Write how the buyer's condition should be described back to them.")
        request_id = self.ids("PREQ")
        now = self.now()
        with self._cs() as cs, transaction(cs.db):
            cs.add_preparation_request(request_id, snap["account_id"], snap["baec_id"], review_id,
                                       snap["candidate_id"], request, snap, e3a.context_digest(snap, request), now,
                                       actor)
            cs.add_event(snap["account_id"], "PREPARATION_REQUESTED", "Human", actor, now,
                         f"Preparation requested from the correspondence verified {snap['reviewed_on']} "
                         "(drafting only; not outreach authorization)", request_id)
        return request_id

    def _current_context(self, stored_request: dict):
        current = self.preparation_snapshot(stored_request["review_id"])  # the request's own bound review
        with self._cs() as cs:
            newer = [r for r in cs.preparation_requests(stored_request["account_id"])
                     if r["review_id"] == stored_request["review_id"]
                     and r["requested_at"] >= stored_request["requested_at"]
                     and r["request_id"] != stored_request["request_id"]]
        state = e3a.context_state(stored_request, current, newer_request_exists=bool(newer))
        upstream = e3a.upstream_eligibility(current, request_present=True)
        grounding = e3a.grounding_items(current, stored_request["request"]) if current["lineage_reconstructable"] else ()
        return upstream, state, grounding

    def generate_preparation(self, request_id: str, *, actor: str) -> str:
        """Build the template outputs and submit each, unchanged, to the Engine 3 validator."""
        with self._cs() as cs:
            stored = cs.preparation_request(request_id)
            if any(p["request_id"] == request_id for p in cs.preparations()):
                raise ConsoleError("Preparation was already generated for this request.")
        upstream, state, grounding = self._current_context(stored)
        # Engine 3 decides first whether anything may be drafted at all (eligibility, human block, stale context).
        # When it refuses, nothing is generated; the refusal is recorded as an event.
        gate = e3a.validate_case(e3a.ValidationCase(upstream=upstream, context=state))
        if not gate.allowed:
            with self._cs() as cs, transaction(cs.db):
                cs.add_event(stored["account_id"], "PREPARATION_REFUSED", "Engine 3", "Engine 3 validator", self.now(),
                             "Preparation refused by Engine 3: " + "; ".join(
                                 e3a.reason_text(i.reason_code.value) for i in gate.issues), request_id)
            raise PreparationRefused("Engine 3 refused preparation.", gate)
        outputs = []
        try:
            artifacts = build_artifacts(stored["snapshot"], stored["request"])
        except Engine3ValidationError as error:
            artifacts = []
            outputs.append({"artifact": None, "report": e3a.structural_refusal(str(error))})
        for artifact in artifacts:
            report = e3a.validate(artifact, e3a.TEMPLATE_VALIDATOR_ORIGIN, upstream, state, grounding)
            outputs.append({"artifact": e3a.artifact_to_dict(artifact), "report": e3a.report_to_dict(report),
                            "audience": artifact.audience.value})
        preparation_id = self.ids("PREP")
        now = self.now()
        passed = sum(1 for o in outputs if o["report"]["allowed"])
        with self._cs() as cs, transaction(cs.db):
            cs.add_preparation(preparation_id, request_id, GENERATOR, outputs, now, actor)
            cs.add_event(stored["account_id"], "PREPARATION_GENERATED", "Engine 3", "Deterministic preparation", now,
                         f"Preparation generated; {passed} of {len(outputs)} outputs passed Engine 3 validation",
                         preparation_id)
        return preparation_id

    def _revalidate(self, preparation: dict, artifact_dict: dict, origin: ArtifactOrigin):
        with self._cs() as cs:
            stored = cs.preparation_request(preparation["request_id"])
        upstream, state, grounding = self._current_context(stored)
        return e3a.validate(e3a.artifact_from_dict(artifact_dict), origin, upstream, state, grounding)

    def edit_artifact(self, preparation_id: str, artifact_id: str, segments: list[dict], *, actor: str) -> dict:
        """A human edit becomes a new HUMAN_EDITED artifact, validated again. The original is never changed."""
        with self._cs() as cs:
            preparation = cs.preparation(preparation_id)
            if artifact_id in cs.reviews(preparation_id):
                raise ConsoleError("This draft has already been reviewed.")
            stored = cs.preparation_request(preparation["request_id"])
            parent = next(o for o in preparation["artifacts"]
                          if o["artifact"] and o["artifact"]["artifact_id"] == artifact_id)
        edit_id = self.ids("EDIT")
        try:
            edited = e3a.build_edited_artifact(e3a.artifact_from_dict(parent["artifact"]), edit_id, segments)
        except Engine3ValidationError as error:
            raise ConsoleError(f"The edited draft is not well formed: {error}") from error
        upstream, state, grounding = self._current_context(stored)
        report = e3a.validate(edited, ArtifactOrigin.HUMAN_EDITED, upstream, state, grounding)
        record = {"artifact": e3a.artifact_to_dict(edited), "report": e3a.report_to_dict(report),
                  # A human edit is its own provenance: never the deterministic template, never a model.
                  "provenance": {"console_origin": "HUMAN_EDIT", "engine3_validator_origin": ArtifactOrigin.HUMAN_EDITED.value,
                                 "edited_by": actor, "parent_preparation_id": preparation_id,
                                 "parent_artifact_id": artifact_id}}
        now = self.now()
        with self._cs() as cs, transaction(cs.db):
            cs.add_edit(edit_id, preparation_id, artifact_id, record, now, actor)
            cs.add_event(stored["account_id"], "DRAFT_EDITED", "Human", actor, now,
                         "Draft edited by human; original preserved", edit_id)
            cs.add_event(stored["account_id"], "DRAFT_EDIT_VALIDATED", "Engine 3", "Engine 3 validator", now,
                         "Human edit passed Engine 3 validation" if report.allowed
                         else "Human edit refused by Engine 3 validation", edit_id)
        return {"edit_id": edit_id, **record}

    def review_artifact(self, preparation_id: str, artifact_id: str, decision: HumanReviewDecision, *,
                        attestations: dict, reason: str = "", edit_id: str | None = None, actor: str) -> None:
        """Record the human preparation review. Acceptance requires a passing, current validation and attestations."""
        if not isinstance(decision, HumanReviewDecision):
            raise ConsoleError("unknown review decision")
        with self._cs() as cs:
            preparation = cs.preparation(preparation_id)
            if artifact_id in cs.reviews(preparation_id):
                raise ConsoleError("This draft has already been reviewed.")
            stored = cs.preparation_request(preparation["request_id"])
            output = next((o for o in preparation["artifacts"]
                           if o["artifact"] and o["artifact"]["artifact_id"] == artifact_id), None)
            edits = {e["edit_id"]: e for e in cs.edits(preparation_id)}
        if output is None:
            raise ConsoleError("No such draft in this preparation.")
        if decision is not HumanReviewDecision.REJECT:
            missing = [label for key, label in ATTESTATIONS if not attestations.get(key)]
            if missing:
                raise ConsoleError("Confirm every review statement before accepting: " + missing[0])
            if decision is HumanReviewDecision.ACCEPT_AS_DRAFTED:
                if not output["report"]["allowed"]:
                    raise ConsoleError("Engine 3 refused this draft; it cannot be accepted as drafted.")
                report = self._revalidate(preparation, output["artifact"], e3a.TEMPLATE_VALIDATOR_ORIGIN)
            else:
                edit = edits.get(edit_id or "")
                if edit is None or edit["parent_artifact_id"] != artifact_id:
                    raise ConsoleError("Choose the edited version to accept.")
                if not edit["artifact"]["report"]["allowed"]:
                    raise ConsoleError("Engine 3 refused this edit; revise it or reject the draft.")
                report = self._revalidate(preparation, edit["artifact"]["artifact"], ArtifactOrigin.HUMAN_EDITED)
            if not report.allowed:
                codes = ", ".join(e3a.reason_text(i.reason_code.value) for i in report.issues)
                raise ConsoleError(f"The records changed since this draft was validated: {codes}")
        now = self.now()
        titles = {HumanReviewDecision.ACCEPT_AS_DRAFTED: "Draft accepted by human",
                  HumanReviewDecision.EDIT: "Edited draft accepted by human",
                  HumanReviewDecision.REJECT: "Draft rejected by human"}
        with self._cs() as cs, transaction(cs.db):
            cs.add_review(preparation_id, artifact_id, decision.value,
                          edit_id if decision is HumanReviewDecision.EDIT else None,
                          {k: bool(attestations.get(k)) for k, _ in ATTESTATIONS}, reason.strip() or None, now, actor)
            cs.add_event(stored["account_id"], "DRAFT_REVIEWED", "Human", actor, now,
                         f"{titles[decision]}: {e3a.ARTIFACT_LABELS[e3a.ArtifactType(output['artifact']['artifact_type'])]}",
                         preparation_id)

    def preparation_view(self, preparation_id: str) -> dict:
        with self._cs() as cs:
            preparation = cs.preparation(preparation_id)
            request = cs.preparation_request(preparation["request_id"])
            reviews = cs.reviews(preparation_id)
            edits = cs.edits(preparation_id)
            meta = cs.account_meta(request["account_id"]) or {}
            provenance = cs.provenance(preparation_id)
        return {"preparation": preparation, "request": request, "reviews": reviews, "edits": edits,
                "provenance": provenance,
                "status": preparation_status(preparation, reviews), "account_name": meta.get("display_name", ""),
                "workspace": self.workspace}

    def preparation_ids(self) -> list[str]:
        with self._cs() as cs:
            return [p["preparation_id"] for p in cs.preparations()]

    # --- reads ---------------------------------------------------------------------------------------------------

    def account_view(self, account_id: str) -> AccountView:
        with self._e1_repo() as repo, self._cs() as cs:
            account = repo.get_account(account_id)
            meta = cs.account_meta(account_id) or {"display_name": account.name, "synthetic": False}
            view = AccountView(account=account, meta=meta, workspace=self.workspace)
            view.interactions = [(i, cs.interaction_meta(i.interaction_id)) for i in repo.list_interactions(account_id)]
            view.drafts = cs.drafts(account_id)
            view.baecs = list(repo.list_baec_records(account_id))
            view.judgments = {b.baec_id: list(repo.list_dormancy_judgments(b.baec_id)) for b in view.baecs}
            view.transitions = list(repo.get_transition_history(account_id))
            view.requests = cs.preparation_requests(account_id)
            request_ids = {r["request_id"] for r in view.requests}
            view.preparations = [{**p, "reviews": cs.reviews(p["preparation_id"]), "edits": cs.edits(p["preparation_id"]),
                                  "provenance": cs.provenance(p["preparation_id"])}
                                 for p in cs.preparations() if p["request_id"] in request_ids]
        confirmed = [b for b in view.baecs if b.classification is BaecClassification.CONFIRMED_BAEC]
        view.current_baec = confirmed[-1] if confirmed else None
        if view.current_baec is not None:
            with self._e2() as e2:
                plans = e2a.plans_for_baec(e2, view.current_baec.baec_id)
                active = [p for p in plans if p.activation_result == "ACTIVATED"]
                view.plan = active[-1] if active else (plans[-1] if plans else None)
                if view.plan is not None:
                    view.plan_record = e2store.load_monitoring_plan(e2, view.plan.monitoring_plan_id,
                                                                     view.plan.plan_version)
                    all_sources = e2store.load_authorized_sources(e2)
                    view.sources = {s: all_sources[s] for s in view.plan_record.authorized_source_ids}
                    view.observations = [e2store.load_observation(e2, i) for i in e2a.observation_ids(e2, view.plan)]
                    view.superseded = {s: e2store.load_supersession(e2, s).superseding_observation_id
                                       for s in e2a.superseded_ids(e2, view.plan)}
                    view.measurements = [e2store.load_derived_measurement(e2, i)
                                         for i in e2a.measurement_ids(e2, view.plan)]
                    for cid in e2a.candidate_ids(e2, view.plan):
                        reviews = [e2store.load_review(e2, r) for r in e2store.review_ids_for_candidate(e2, cid)]
                        view.candidates.append({"candidate": e2store.load_correspondence_candidate(e2, cid),
                                                "reviews": [(r, r.outcome) for r in reviews]})
                        with self._cs() as cs:
                            for r in reviews:
                                decisions = cs.block_decisions(r.review_id)
                                if decisions:
                                    view.blocks[r.review_id] = decisions
        _derive_progress(view)
        items = self.timeline(account_id, view=view)
        view.last_activity = items[-1].at if items else None
        return view

    def timeline(self, account_id: str, *, view: AccountView | None = None) -> list[TimelineItem]:
        """Account history from the engines' own records plus console events. Nothing is synthesized."""
        view = view or self.account_view(account_id)
        items: list[TimelineItem] = []
        add = items.append
        for interaction, meta in view.interactions:
            meta = meta or {}
            role = meta.get("buyer_role") or "buyer"
            add(TimelineItem(meta.get("recorded_at", interaction.occurred_at), "Buyer interaction recorded", "Human",
                             meta.get("recorded_by", ""), "REMEMBER", "INTERACTION",
                             f"With the {role}; the conversation took place "
                             f"{interaction.occurred_at.astimezone():%d %b %Y, %H:%M}".replace(" 0", " ")))
        for record in view.baecs:
            if record.classification is BaecClassification.CONFIRMED_BAEC:
                add(TimelineItem(record.confirmation.authorized_at, "Buyer-articulated condition confirmed", "Human",
                                 record.confirmation.authorized_by, "REMEMBER", "BAEC_CONFIRMED",
                                 "Engine 1 classified it a confirmed BAEC; a human confirmed it"))
            else:
                add(TimelineItem(record.captured_at, "Condition recorded as not a confirmed BAEC", "Engine 1",
                                 "Engine 1 classifier", "REMEMBER", "BAEC_NOT_CONFIRMED",
                                 record.classification_reason or ""))
            for judgment in view.judgments.get(record.baec_id, []):
                j = judgment.judgment
                add(TimelineItem(j.authorization.authorized_at, "Dormancy judgment recorded", "Human",
                                 j.authorization.authorized_by, "ACCOUNT", "DORMANCY_JUDGMENT",
                                 f"Plausibility {j.plausibility.value.lower()}, "
                                 f"addressability {j.addressability.value.lower()}"))
        for t in view.transitions:
            add(TimelineItem(t.recorded_at, f"Account state: {STATE_LABELS[t.to_state]}", "Human",
                             t.authorization.authorized_by, "ACCOUNT", "STATE_CHANGE",
                             f"From {STATE_LABELS[t.from_state]}"))
        if view.current_baec is not None:
            items.extend(self._engine2_events(view))
        with self._cs() as cs:
            for e in cs.events(account_id):
                add(TimelineItem(e.occurred_at, e.summary, e.actor_kind, e.actor, _EVENT_STAGES.get(e.event_type,
                                 "ACCOUNT"), e.event_type))
        items = [TimelineItem(**{**i.__dict__, "account_id": account_id}) for i in items]
        return sorted(items, key=lambda i: i.at)

    def _engine2_events(self, view: AccountView) -> list[TimelineItem]:
        out = []
        with self._e2() as e2:
            plans = e2a.plans_for_baec(e2, view.current_baec.baec_id)
            owned = set().union(*(e2a.entity_ids(e2, p) for p in plans)) if plans else set()
            for event in e2store.audit_events(e2):
                if event.entity_id not in owned:
                    continue
                mapped = _ENGINE2_EVENTS.get(event.event_type)
                if mapped is None:
                    continue
                title, kind, stage = mapped
                detail = ""
                if event.event_type == "HUMAN_REVIEW_RECORDED":
                    outcome = CorrespondenceOutcome(event.details["outcome"])
                    out.append(TimelineItem(event.recorded_at, "Correspondence review recorded", "Human", event.actor,
                                            "RECOGNIZE", event.event_type))
                    title, kind = f"Engine 2 outcome: {e2a.OUTCOME_LABELS[outcome]}", "Engine 2"
                    detail = "Derived by the locked Engine 2 rule from the human's findings"
                if event.event_type == "SUPERSESSION_RECORDED" and event.details.get("retraction"):
                    title = "Earlier observation retracted"
                if event.event_type == "PLAN_ACTIVATION_REFUSED":
                    detail = event.details.get("refusal_reason", "")
                out.append(TimelineItem(event.recorded_at, title, kind,
                                        event.actor if kind == "Human" else "Engine 2", stage, event.event_type,
                                        detail))
        return out

    def review_queue(self) -> dict:
        """Pending and completed human reviews in this workspace, read from stored records."""
        pending, completed = [], []
        for account_id in self.account_ids():
            view = self.account_view(account_id)
            name = view.display_name
            base = {"account_id": account_id, "account": name, "workspace": self.workspace}
            for d in view.drafts:
                row = {**base, "type": "Condition confirmation", "created": d["captured_at"],
                       "summary": (d.get("form") and _draft_summary(view, d)) or "", "ref": d["draft_id"],
                       "stage": "REMEMBER"}
                if d["resolution"] is None:
                    pending.append({**row, "status": "Awaiting human confirmation"})
                else:
                    completed.append({**row, "status": _DRAFT_STATUS[d["resolution"]]})
            for c in view.candidates:
                cand = c["candidate"]
                text = "; ".join(o.exact_evidence_content for o in cand.observations)
                created = min(o.observed_at for o in cand.observations)
                row = {**base, "type": "Correspondence review", "created": created, "summary": text,
                       "ref": cand.candidate_id, "stage": "RECOGNIZE"}
                if not c["reviews"]:
                    pending.append({**row, "status": "Awaiting human review"})
                else:
                    completed.append({**row, "status": e2a.OUTCOME_LABELS[c["reviews"][-1][1]]})
            for p in view.preparations:
                for output in p["artifacts"]:
                    if not output["artifact"]:
                        continue
                    aid = output["artifact"]["artifact_id"]
                    label = e3a.ARTIFACT_LABELS[e3a.ArtifactType(output["artifact"]["artifact_type"])]
                    row = {**base, "type": "Draft review", "created": p["generated_at"],
                           "summary": f"{label}: {output['artifact']['rendered_text'][:140]}",
                           "ref": p["preparation_id"], "artifact_id": aid, "stage": "PREPARE"}
                    review = p["reviews"].get(aid)
                    if review is not None:
                        completed.append({**row, "status": _DECISION_STATUS[review["decision"]]})
                    elif output["report"]["allowed"]:
                        pending.append({**row, "status": "Awaiting human review"})
                    else:
                        completed.append({**row, "status": "Refused by Engine 3 validation"})
        return {"pending": pending, "completed": completed}


def _draft_summary(view: AccountView, draft: dict) -> str:
    for interaction, meta in view.interactions:
        if interaction.interaction_id == draft["interaction_id"] and meta:
            return meta["buyer_statement"]
    return ""


_DRAFT_STATUS = {"CONFIRMED": "Confirmed by human", "RECORDED_NOT_CONFIRMABLE": "Recorded as not a confirmed BAEC",
                 "DISCARDED": "Discarded"}
_DECISION_STATUS = {"ACCEPT_AS_DRAFTED": "Accepted as drafted", "EDIT": "Edited and accepted", "REJECT": "Rejected"}
_EVENT_STAGES = {"ACCOUNT_CREATED": "ACCOUNT", "CONDITION_CAPTURED": "REMEMBER", "CONDITION_DISCARDED": "REMEMBER",
                 "PREPARATION_REQUESTED": "PREPARE", "PREPARATION_BLOCKED": "PREPARE", "PREPARATION_UNBLOCKED": "PREPARE",
                 "PREPARATION_REFUSED": "PREPARE", "PREPARATION_GENERATED": "PREPARE", "DRAFT_EDITED": "PREPARE",
                 "DRAFT_EDIT_VALIDATED": "PREPARE", "DRAFT_REVIEWED": "PREPARE"}
_ENGINE2_EVENTS = {
    "AUTHORIZED_SOURCE_RECORDED": ("Source authorized for monitoring", "Human", "MONITOR"),
    "MONITORING_PLAN_RECORDED": ("Monitoring plan authorized", "Human", "MONITOR"),
    "PLAN_ACTIVATION_RECORDED": ("Monitoring activated", "Engine 2", "MONITOR"),
    "PLAN_ACTIVATION_REFUSED": ("Monitoring activation refused", "Engine 2", "MONITOR"),
    "SOURCE_ITEM_REFUSED": ("Source item refused", "Engine 2", "MONITOR"),
    "OBSERVATION_RECORDED": ("New observation added", "Human", "MONITOR"),
    "SUPERSESSION_RECORDED": ("Earlier observation corrected", "Human", "MONITOR"),
    "DERIVED_MEASUREMENT_RECORDED": ("Measurement derived", "Engine 2", "MONITOR"),
    "CORRESPONDENCE_CANDIDATE_RECORDED": ("Flagged for correspondence review", "Human", "RECOGNIZE"),
    "HUMAN_REVIEW_RECORDED": ("Correspondence review recorded", "Human", "RECOGNIZE"),
}


def preparation_status(preparation: dict, reviews: dict) -> str:
    """Needs review | Accepted | Rejected, derived from the validation reports and human decisions."""
    reviewable = [o["artifact"]["artifact_id"] for o in preparation["artifacts"]
                  if o["artifact"] and o["report"]["allowed"]]
    if any(aid not in reviews for aid in reviewable):
        return "Needs review"
    if any(r["decision"] in ("ACCEPT_AS_DRAFTED", "EDIT") for r in reviews.values()):
        return "Accepted"
    return "Rejected"


def _derive_progress(view: AccountView) -> None:
    """Workflow stage and next action, from stored records only."""
    def set_(stage: str, action: str) -> None:
        view.stage, view.next_action = stage, action

    if not view.interactions:
        return set_("REMEMBER", "Record a buyer interaction")
    if view.current_baec is None:
        if view.pending_drafts:
            return set_("REMEMBER", "Confirm the captured condition")
        return set_("REMEMBER", "Capture the buyer's condition")
    if view.plan is None:
        return set_("MONITOR", "Start monitoring")
    if view.plan.activation_result != "ACTIVATED":
        return set_("MONITOR", "Monitoring activation was refused")
    if any(not c["reviews"] for c in view.candidates):
        return set_("RECOGNIZE", "Review possible correspondence")
    verified = view.verified_correspondences()
    if not verified:
        return set_("MONITOR", "Add observations as evidence arrives")
    # Progress is read across every verified correspondence; no single review is treated as the operative one.
    if not view.requests:
        if all(v["blocked"] for v in verified):
            return set_("PREPARE", "Preparation blocked by human decision")
        return set_("PREPARE", "Request preparation")
    request = view.requests[-1]  # the most recent explicit human request, with its own bound review
    if request["review_id"] in view.blocks and view.blocks[request["review_id"]][-1]["action"] == "BLOCK":
        return set_("PREPARE", "Preparation blocked by human decision")
    preps = [p for p in view.preparations if p["request_id"] == request["request_id"]]
    if not preps:
        return set_("PREPARE", "Generate preparation")
    status = preparation_status(preps[-1], preps[-1]["reviews"])
    if status == "Needs review":
        return set_("PREPARE", "Review prepared drafts")
    if status == "Accepted":
        return set_("PREPARED", "Prepared for human use. The salesperson decides.")
    return set_("PREPARE", "Drafts rejected; request preparation again if useful")


class Console:
    """The two workspaces, presented as one product."""

    def __init__(self, root: str | Path, clock: Clock | None = None) -> None:
        self.root = Path(root)
        self.local = ConsoleService(self.root, LOCAL, clock)
        self.synthetic = ConsoleService(self.root, SYNTHETIC, clock)

    @property
    def services(self) -> tuple[ConsoleService, ConsoleService]:
        return (self.local, self.synthetic)

    def owner(self, account_id: str) -> ConsoleService:
        for service in self.services:
            if service.has_account(account_id):
                return service
        raise RepositoryNotFoundError(f"account {account_id!r} does not exist")

    def account_views(self) -> list[AccountView]:
        return [s.account_view(a) for s in self.services for a in s.account_ids()]

    def owner_of_preparation(self, preparation_id: str) -> ConsoleService:
        for service in self.services:
            if preparation_id in service.preparation_ids():
                return service
        raise ConsoleError(f"no preparation {preparation_id}")

    def reset_synthetic(self) -> None:
        """Delete only the synthetic workspace files. The local workspace is never opened for writing here."""
        self.synthetic.destroy()

    @property
    def actor(self) -> str:
        return self.local.get_pref("actor", "Local user")

    def set_actor(self, name: str) -> None:
        if not name or not name.strip():
            raise ConsoleError("Enter a name.")
        self.local.set_pref("actor", name.strip())

    def review_queue(self) -> dict:
        merged = {"pending": [], "completed": []}
        for service in self.services:
            queue = service.review_queue()
            merged["pending"] += queue["pending"]
            merged["completed"] += queue["completed"]
        for key in merged:
            merged[key].sort(key=lambda r: r["created"], reverse=True)
        return merged

    def activity(self) -> list[TimelineItem]:
        items = [i for s in self.services for a in s.account_ids() for i in s.timeline(a)]
        return sorted(items, key=lambda i: i.at, reverse=True)

    def preparations(self) -> list[dict]:
        return [s.preparation_view(p) for s in self.services for p in s.preparation_ids()]

    def dashboard(self, views: list[AccountView] | None = None) -> dict:
        """Counts read from stored records."""
        views = views if views is not None else self.account_views()
        verified = monitoring = awaiting = ready = 0
        for v in views:
            verified += sum(1 for b in v.baecs if b.classification is BaecClassification.CONFIRMED_BAEC)
            monitoring += int(v.plan is not None and v.plan.activation_result == "ACTIVATED")
            awaiting += sum(1 for c in v.candidates if not c["reviews"])
            ready += int(v.next_action == "Request preparation")
        return {"accounts": len(views), "verified": verified, "monitoring": monitoring, "awaiting": awaiting,
                "ready": ready}
