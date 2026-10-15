"""The Account Workspace: REMEMBER, MONITOR, RECOGNIZE, PREPARE, plus account state and timeline.

Which sections are available is decided from stored records (AccountView), never from session state. Session
state holds only what the person is looking at: the selected section and which preview is on screen.
"""

from __future__ import annotations

from datetime import datetime, time, timezone

import streamlit as st

from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    ThresholdComparator,
)
from baec_app.engine2.domain import (
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    SufficiencyFinding,
)
from baec_app.engine2.integration.engine1_handoff import HandoffRefused
from baec_app.engine2.measurement import Transformation
from baec_app.engine3.enums import (
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    HumanReviewDecision,
    PreparationChannel,
    PreparationGoal,
    ReferenceUseMode,
    SourceIdentityDisclosure,
)
from baec_app.engine3.models import ClaimField, DraftSegment

from . import engine1_adapter as e1a
from . import engine2_adapter as e2a
from . import engine3_adapter as e3a
from .console_service import ATTESTATIONS, STATE_LABELS, AccountView, Console, ConsoleService
from .console_store import ORIGIN_COMPATIBILITY_TEXT
from .ui_common import STAGE_LABELS, attempt, badge, boundary, e, flow, html, notice, show_flash, technical, when

SECTIONS = ("Remember", "Monitor", "Recognize", "Prepare", "Timeline", "Account state")
SECTION_FOR_STAGE = {"REMEMBER": "Remember", "MONITOR": "Monitor", "RECOGNIZE": "Recognize", "PREPARE": "Prepare",
                     "PREPARED": "Prepare"}
GOAL_LABELS = {
    PreparationGoal.CHECK_CONDITION_STILL_RELEVANT: "Check whether the earlier condition is still relevant",
    PreparationGoal.ASK_WHETHER_TO_REVISIT_PRIOR_DISCUSSION: "Ask whether to revisit the earlier conversation",
    PreparationGoal.ASK_WHAT_HAS_CHANGED: "Ask what has changed",
    PreparationGoal.UNDERSTAND_WHETHER_EVALUATION_REOPENED: "Learn whether evaluation has actually reopened",
}
CHANNEL_LABELS = {PreparationChannel.EMAIL_AND_CALL: "Email and call", PreparationChannel.EMAIL: "Email only",
                  PreparationChannel.CALL: "Call only"}
CLAIM_LABELS = {
    ClaimType.FACTUAL_ASSERTION: "Statement of fact",
    ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE: "Reference to the buyer's earlier statement",
    ClaimType.QUESTION: "Question",
    ClaimType.COURTESY_OR_TRANSITION: "Courtesy or transition",
}


def render(console: Console, account_id: str) -> None:
    service = console.owner(account_id)
    view = service.account_view(account_id)
    actor = console.actor
    if st.button("All accounts", key="back"):
        st.session_state.page = "Accounts"
        st.rerun()
    _header(view)
    flow(view.stage)
    show_flash()
    widget = f"section-{account_id}"
    requested = st.session_state.pop("section", None)
    if requested in SECTIONS:
        st.session_state[widget] = requested
    elif st.session_state.get(widget) not in SECTIONS:
        st.session_state[widget] = SECTION_FOR_STAGE.get(view.stage, "Remember")
    section = st.segmented_control("Section", SECTIONS, key=widget, required=True, label_visibility="collapsed")
    st.query_params["section"] = section
    if section == "Remember":
        _remember(service, view, actor)
    elif section == "Monitor":
        _monitor(service, view, actor)
    elif section == "Recognize":
        _recognize(service, view, actor)
    elif section == "Prepare":
        _prepare(service, view, actor)
    elif section == "Timeline":
        timeline(service.timeline(account_id, view=view))
    else:
        _account_state(service, view, actor)


def _header(view: AccountView) -> None:
    syn = " " + badge("Synthetic", "syn") if view.synthetic else ""
    meta = " · ".join(x for x in (view.meta.get("industry"), view.meta.get("owner_note")) if x)
    stage = STAGE_LABELS.get(view.stage, view.stage)
    html(f'<div class="bc-head"><div><div class="bc-label">Account</div><div class="bc-head-name">'
         f'{e(view.display_name)}{syn}</div><div class="bc-muted">{e(meta)}</div></div>'
         f'<div><div class="bc-label">Account state</div><div class="bc-head-value">'
         f'{e(STATE_LABELS[view.account.state])}</div></div>'
         f'<div><div class="bc-label">Workflow step</div><div class="bc-head-value">{e(stage)}</div>'
         f'<div class="bc-muted">{e(view.next_action)}</div></div>'
         f'<div><div class="bc-label">Last activity</div><div class="bc-head-value">'
         f'{e(when(view.last_activity) or "None yet")}</div></div></div>')


def _statement(view: AccountView) -> str | None:
    if view.current_baec is not None:
        return view.current_baec.candidate.buyer_exact_statement
    for _, meta in view.interactions:
        if meta:
            return meta["buyer_statement"]
    return None


# --- REMEMBER ---------------------------------------------------------------------------------------------------


def _remember(service: ConsoleService, view: AccountView, actor: str) -> None:
    html('<div class="bc-h">Buyer interactions</div>')
    for interaction, meta in view.interactions:
        meta = meta or {}
        html(f'<div class="bc-card"><div class="bc-label">{e(meta.get("buyer_role") or "Buyer")} · '
             f'{e(when(interaction.occurred_at))}</div><div class="bc-quote">'
             f'{e(meta.get("buyer_statement", interaction.text))}</div>'
             + (f'<div class="bc-muted">Seller notes: {e(meta["seller_notes"])}</div>' if meta.get("seller_notes")
                else "") + "</div>")
    with st.expander("Add buyer interaction", expanded=not view.interactions):
        with st.form(f"interaction-{view.account.account_id}", clear_on_submit=True):
            role = st.text_input("Buyer role", placeholder="e.g. Director of Operations")
            c1, c2 = st.columns(2)
            day = c1.date_input("Interaction date", value=datetime.now().date())
            at = c2.time_input("Time", value=time(9, 0))
            statement = st.text_area("Exact buyer statement",
                                     help="The buyer's own words, exactly as said. This text is never altered.")
            notes = st.text_area("Seller notes / interaction context (optional)")
            if st.form_submit_button("Record interaction", type="primary"):
                occurred = datetime.combine(day, at).astimezone(timezone.utc)
                if attempt(lambda: service.record_interaction(view.account.account_id, buyer_role=role,
                                                              occurred_at=occurred, buyer_statement=statement,
                                                              seller_notes=notes, actor=actor),
                           success="Buyer interaction recorded."):
                    st.rerun()
    if not view.interactions:
        return

    html('<div class="bc-h">Buyer-articulated conditions</div>')
    for record in view.baecs:
        _baec_card(record)
    for draft in view.pending_drafts:
        _draft_card(service, view, draft, actor)
    with st.expander("Capture a buyer-articulated condition", expanded=view.current_baec is None
                     and not view.pending_drafts):
        _capture_form(service, view, actor)


def _baec_card(record) -> None:
    c = record.candidate
    if record.classification is BaecClassification.CONFIRMED_BAEC:
        status = badge("Human confirmed", "ok")
        extra = (f'Confirmed by {e(record.confirmation.authorized_by)} on {e(when(record.confirmation.authorized_at))}'
                 f' · Currentness: {e(record.staleness_status.value.title())}')
    else:
        status = badge("Not a confirmed BAEC", "warn")
        extra = e(record.classification_reason or "")
    html(f'<div class="bc-card"><div class="bc-label">Condition {status}</div>'
         f'<div class="bc-quote">{e(c.buyer_exact_statement)}</div><div class="bc-muted">{extra}</div></div>')


def _criteria_rows(candidate) -> str:
    rows = []
    for a in candidate.assessments:
        evidence = "; ".join(f'"{x.text}" ({x.provenance.value.replace("_", " ").lower()})' for x in a.evidence) or "None"
        rows.append(f"<div>{e(e1a.CRITERION_LABELS[a.criterion])}</div>"
                    f"<div><b>{e(e1a.FINDING_LABELS[a.finding])}</b> · {e(evidence)}</div>")
    return "".join(rows)


def _stringency_rows(s) -> str:
    if s is None:
        return "<div>Threshold</div><div>None stated</div>"
    comparator = e1a.COMPARATOR_LABELS[s.comparator] if s.comparator else "Recorded as wording only"
    number = f"{s.numeric_value}{s.unit or ''}" if s.numeric_value is not None else "No number"
    rows = [("Threshold wording", f'"{s.verbatim_text}"'), ("Stored as", f"{comparator} · {number}")]
    for label, value in (("Qualitative term", s.qualitative_term), ("Recurrence", s.recurrence_text),
                         ("Timing", s.timing_text)):
        if value:
            rows.append((label, f'"{value}"'))
    return "".join(f"<div>{e(k)}</div><div>{e(v)}</div>" for k, v in rows)


def _draft_card(service: ConsoleService, view: AccountView, draft: dict, actor: str) -> None:
    key = f"preview-{draft['draft_id']}"
    with st.container(border=True):
        html(f'<div class="bc-label">Condition {badge("Captured · not yet confirmed", "warn")}</div>'
             f'<div class="bc-muted">Captured by {e(draft["captured_by"])} on {e(when(draft["captured_at"]))}</div>')
        focus = st.session_state.get("focus") == draft["draft_id"]
        if key not in st.session_state and not focus:
            c1, c2 = st.columns([1, 1])
            if c1.button("Preview condition", key=f"pv-{draft['draft_id']}", type="primary"):
                st.session_state[key] = True
                st.rerun()
            if c2.button("Discard", key=f"dc-{draft['draft_id']}"):
                if attempt(lambda: service.discard_draft(draft["draft_id"], actor=actor), success="Draft discarded."):
                    st.rerun()
            return
        preview = attempt(lambda: service.preview_condition(draft["draft_id"]), failure="Preview failed.")
        if preview is None:
            return
        cand, result = preview["candidate"], preview["preview"]
        html('<div class="bc-label" style="margin-top:10px">Exactly what will be recorded</div>'
             f'<div class="bc-quote">{e(cand.buyer_exact_statement)}</div><div class="bc-kv">'
             f'<div>Buyer role</div><div>{e(cand.buyer_role or "Not established")}</div>'
             f'<div>Who supplied it</div><div>{e(e1a.ORIGIN_LABELS[cand.articulation_origin])}</div>'
             f'<div>How it came up</div><div>{e(e1a.ELICITATION_LABELS[cand.elicitation_mode])}</div>'
             + _criteria_rows(cand) + _stringency_rows(cand.stringency) + "</div>")
        if result.confirmable:
            notice("Engine 1: meets all four criteria",
                   "Engine 1 classifies this as a confirmable buyer-articulated condition. It is not recorded until "
                   "you confirm it.", "ok")
            if st.button("Confirm buyer-articulated condition", type="primary", key=f"cf-{draft['draft_id']}"):
                if attempt(lambda: service.confirm_condition(draft["draft_id"],
                                                             displayed_fingerprint=preview["fingerprint"], actor=actor),
                           success="Buyer-articulated condition confirmed. It can now be monitored."):
                    st.session_state.pop(key, None)
                    st.rerun()
        else:
            notice(f"Engine 1: {result.result.classification.value.replace('_', ' ').title()}",
                   result.result.reason_text or "", "warn")
            boundary("It cannot be confirmed or monitored. You can preserve the classification with its reason.")
            if st.button("Record as not a confirmed BAEC", key=f"nc-{draft['draft_id']}"):
                if attempt(lambda: service.record_not_confirmable(draft["draft_id"],
                                                                  displayed_fingerprint=preview["fingerprint"],
                                                                  actor=actor),
                           success="Classification recorded with its reason."):
                    st.rerun()
        if st.button("Close preview", key=f"cl-{draft['draft_id']}"):
            st.session_state.pop(key, None)
            st.session_state.focus = None
            st.rerun()
        technical({"Candidate fingerprint (bound to the confirm click)": preview["fingerprint"],
                   "Draft": draft["draft_id"]})


def _capture_form(service: ConsoleService, view: AccountView, actor: str) -> None:
    options = {i.interaction_id: f"{(m or {}).get('buyer_role') or 'Buyer'} · {when(i.occurred_at)}"
               for i, m in view.interactions}
    interaction_id = st.selectbox("Interaction", list(options), format_func=options.get,
                                  index=len(options) - 1, key=f"cap-int-{view.account.account_id}")
    meta = next(m for i, m in view.interactions if i.interaction_id == interaction_id) or {}
    html(f'<div class="bc-quote">{e(meta.get("buyer_statement", ""))}</div>')
    boundary("Human-guided capture. Copy evidence exactly from the interaction; nothing is extracted automatically.")
    with st.form(f"capture-{view.account.account_id}"):
        criteria = {}
        findings = list(CriterionFinding)
        for criterion in BaecCriterion:
            st.markdown(f"**{e1a.CRITERION_LABELS[criterion]}** — {e1a.CRITERION_QUESTIONS[criterion]}")
            c1, c2 = st.columns([1, 2])
            finding = c1.radio("Finding", findings, index=2, format_func=e1a.FINDING_LABELS.get, horizontal=True,
                               key=f"{view.account.account_id}-f-{criterion.value}")
            excerpt = c2.text_input("Evidence (exact words from the interaction)", key=f"{view.account.account_id}-x-{criterion.value}")
            rationale = st.text_input("Why (optional)", key=f"{view.account.account_id}-r-{criterion.value}")
            criteria[criterion.value] = {"finding": finding.value, "excerpt": excerpt, "rationale": rationale}
        c1, c2 = st.columns(2)
        origin = c1.radio("Who supplied the condition?", list(ArticulationOrigin), index=2,
                          format_func=e1a.ORIGIN_LABELS.get)
        elicitation = c2.radio("How did it come up?", list(ElicitationMode), index=2,
                               format_func=e1a.ELICITATION_LABELS.get)
        st.markdown("**Threshold and timing** — stored exactly as the buyer said it")
        c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
        threshold_text = c1.text_input("Threshold wording", placeholder="e.g. by more than 8%")
        comparators = [None, *ThresholdComparator]
        comparator = c2.selectbox("Comparator", comparators,
                                  format_func=lambda c: "Cannot be normalized safely" if c is None
                                  else e1a.COMPARATOR_LABELS[c], index=0)
        value = c3.text_input("Number", placeholder="8")
        unit = c4.text_input("Unit", placeholder="%")
        c1, c2, c3 = st.columns(3)
        timing = c1.text_input("Timing wording", placeholder="e.g. at the July renewal")
        recurrence = c2.text_input("Recurrence wording (optional)")
        qualitative = c3.text_input("Qualitative term (optional)", placeholder="e.g. significant")
        if st.form_submit_button("Capture condition", type="primary"):
            form = e1a.ConditionForm(
                criteria=criteria, origin=origin.value, elicitation=elicitation.value, threshold_text=threshold_text,
                comparator=comparator.value if comparator else "", threshold_value=value, threshold_unit=unit,
                qualitative_term=qualitative, recurrence_text=recurrence, timing_text=timing)
            draft_id = attempt(lambda: service.capture_condition(view.account.account_id, interaction_id, form,
                                                                 actor=actor),
                               success="Condition captured. Preview it, then confirm.")
            if draft_id:
                st.session_state[f"preview-{draft_id}"] = True
                st.rerun()


# --- MONITOR ----------------------------------------------------------------------------------------------------


def _monitor(service: ConsoleService, view: AccountView, actor: str) -> None:
    if view.current_baec is None:
        notice("Condition not confirmed", "Monitoring starts only from a human-confirmed buyer-articulated condition. "
               "Capture and confirm one in Remember.", "warn")
        return
    record = view.current_baec
    html(f'<div class="bc-card"><div class="bc-label">Buyer condition {badge("Human confirmed", "ok")}</div>'
         f'<div class="bc-quote">{e(record.candidate.buyer_exact_statement)}</div><div class="bc-kv">'
         + _stringency_rows(record.candidate.stringency) + "</div></div>")
    boundary("Monitoring watches for evidence related to the buyer's own condition. It is not buyer evaluation.")
    if view.plan is None:
        _start_monitoring(service, view, actor)
        return
    plan = view.plan_record
    status = badge("Active", "ok") if view.plan.activation_result == "ACTIVATED" else badge("Activation refused", "stop")
    dims = ", ".join(d.label for d in CorrespondenceDimension if d in plan.required_dimensions)
    sources = "".join(f"<div>{e(s.source_type)}</div><div>{e(s.owner_or_publisher)} · {e(s.legitimate_basis)} · "
                      f"captures {e(s.permitted_capture)}</div>" for s in view.sources.values())
    threshold = (f"{e1a.COMPARATOR_LABELS[plan.threshold.comparator]} {plan.threshold.value}{plan.threshold.unit}"
                 if plan.threshold else "None stated")
    html(f'<div class="bc-card"><div class="bc-label">Monitoring plan {status}</div><div class="bc-kv">'
         f'<div>Organizations</div><div>{e(", ".join(plan.target_entities))}</div>'
         f'<div>Threshold</div><div>{e(threshold)}</div>'
         f'<div>Timing</div><div>{e(plan.timing_context or "None stated")}</div>'
         f'<div>Reviewers will check</div><div>{e(dims)}</div>{sources}'
         f'<div>Authorized by</div><div>{e(plan.authorized_by)} on {e(when(plan.authorized_at))}</div></div></div>')
    if view.plan.activation_result != "ACTIVATED":
        notice("Monitoring not eligible", "Engine 2 refused activation because Engine 1 does not report this "
               "condition as current.", "stop")
        return
    _observations(view)
    with st.expander("Add observation", expanded=not view.observations):
        _observation_form(service, view, actor)


def _start_monitoring(service: ConsoleService, view: AccountView, actor: str) -> None:
    try:
        handoff = service.handoff(view.current_baec.baec_id)
    except HandoffRefused as error:
        notice("Monitoring not eligible", str(error), "stop")
        return
    html('<div class="bc-h">Start monitoring</div>')
    with st.form(f"plan-{view.account.account_id}"):
        entities = st.text_area("Organizations to monitor (one per line)", value=view.display_name)
        dims = list(CorrespondenceDimension)
        defaults = [d for d in dims if d in e2a.default_required_dimensions(handoff)]
        required = st.multiselect("What a reviewer must check before correspondence can be verified", dims,
                                  default=defaults, format_func=lambda d: d.label)
        st.markdown("**Authorized source**")
        c1, c2 = st.columns(2)
        source_type = c1.text_input("Source type", placeholder="e.g. Supplier renewal notice")
        owner = c2.text_input("Owner or publisher", placeholder="e.g. Cobalt Imaging Supply")
        basis = c1.text_input("Legitimate basis", placeholder="e.g. Forwarded by the buyer under the account agreement")
        access = c2.text_input("Access method", placeholder="e.g. Buyer forwards notices by email")
        capture = c1.text_input("What may be captured", placeholder="e.g. Notice text and dates")
        limits = c2.text_input("Known limitations (optional)")
        boundary("You are authorizing this monitoring plan. Monitoring is limited to legitimate business data from "
                 "the sources you authorize.")
        if st.form_submit_button("Start monitoring", type="primary"):
            missing = [n for n, v in (("source type", source_type), ("owner or publisher", owner),
                                      ("legitimate basis", basis), ("access method", access),
                                      ("what may be captured", capture)) if not v.strip()]
            if missing:
                notice("Source details needed", "Fill in: " + ", ".join(missing) + ".", "warn")
            elif not required:
                notice("Choose at least one check", kind="warn")
            elif attempt(lambda: service.start_monitoring(
                    view.account.account_id, view.current_baec.baec_id, target_entities=tuple(entities.splitlines()),
                    required=frozenset(required),
                    sources=(e2a.SourceForm(source_type, owner, basis, access, capture, limits),), actor=actor),
                    success="Monitoring plan recorded."):
                st.rerun()


def _observations(view: AccountView) -> None:
    if not view.observations:
        return
    html('<div class="bc-h">Observations</div>')
    by_obs = {}
    for m in view.measurements:
        for oid in m.source_observation_ids:
            by_obs.setdefault(oid, []).append(m)
    for o in reversed(view.observations):
        status = (badge("Corrected by a later observation", "warn") if o.observation_id in view.superseded
                  else badge("Current", "ok"))
        measures = " · ".join(f"Derived measurement {m.output_value}{m.output_unit}" for m in by_obs.get(o.observation_id, []))
        html(f'<div class="bc-card"><div class="bc-label">{e(o.source_type)} · {e(when(o.observed_at, False))} '
             f'{status}</div><div class="bc-quote">{e(o.exact_evidence_content)}</div><div class="bc-muted">'
             f'{e(e2a.PROVENANCE_LABELS.get(o.provenance, o.provenance.value))}'
             f'{" · " + e(measures) if measures else ""}</div></div>')


def _observation_form(service: ConsoleService, view: AccountView, actor: str) -> None:
    with st.form(f"obs-{view.account.account_id}", clear_on_submit=True):
        source_id = st.selectbox("Source", list(view.sources), format_func=lambda s: view.sources[s].source_type)
        c1, c2 = st.columns(2)
        observed = c1.date_input("Observed date", value=datetime.now().date())
        effective = c2.date_input("Effective date (optional)", value=None)
        content = st.text_area("Exact observation / evidence", help="Stored exactly as entered and never altered.")
        provenance = st.radio("Where it comes from", [ProvenanceCategory.EXTERNAL_EVIDENCE, ProvenanceCategory.BUYER_FACT,
                                                      ProvenanceCategory.SELLER_OBSERVATION],
                              format_func=e2a.PROVENANCE_LABELS.get, horizontal=True)
        st.markdown("**Structured measurement (optional)** — exact arithmetic, never rounded")
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        transformation = c1.selectbox("Measurement", [None, *Transformation],
                                      format_func=lambda t: "None" if t is None else e2a.TRANSFORMATION_LABELS[t])
        baseline = c2.text_input("Earlier value", placeholder="40000")
        new = c3.text_input("New value", placeholder="43600")
        unit = c4.text_input("Unit", placeholder="USD per year")
        current = [o for o in view.observations if o.observation_id not in view.superseded]
        corrects = st.selectbox("Corrects an earlier observation (optional)", [None, *[o.observation_id for o in current]],
                                format_func=lambda oid: "No" if oid is None else next(
                                    o.exact_evidence_content[:90] for o in current if o.observation_id == oid))
        retraction = st.checkbox("It retracts the earlier observation rather than correcting it")
        flag = st.checkbox("Queue for correspondence review", value=True,
                           help="A human then decides whether it corresponds to the buyer's condition.")
        if st.form_submit_button("Add observation", type="primary"):
            form = e2a.ObservationForm(
                source_id=source_id, observed_at=datetime.combine(observed, time(12, 0)).astimezone(timezone.utc),
                content=content, provenance=provenance.value,
                effective_at=datetime.combine(effective, time(0, 0), tzinfo=timezone.utc) if effective else None,
                corrects_observation_id=corrects or "", is_retraction=retraction,
                transformation=transformation.value if transformation else "", baseline_value=baseline,
                new_value=new, value_unit=unit, flag_for_review=flag)
            if attempt(lambda: service.add_observation(view.current_baec.baec_id, form, actor=actor),
                       success="Observation added." + (" It is waiting for correspondence review." if flag else "")):
                st.rerun()


# --- RECOGNIZE --------------------------------------------------------------------------------------------------


def _recognize(service: ConsoleService, view: AccountView, actor: str) -> None:
    if not view.candidates:
        notice("Nothing to review yet", "Observations sent to correspondence review appear here.", "")
        return
    pending = [c for c in view.candidates if not c["reviews"]]
    focus = st.session_state.get("focus")
    for c in pending:
        if focus in (None, c["candidate"].candidate_id) or len(pending) == 1:
            _review_form(service, view, c["candidate"], actor)
            break
    done = [c for c in view.candidates if c["reviews"]]
    if done:
        html('<div class="bc-h">Recorded reviews</div>')
    for c in reversed(done):
        for review, outcome in reversed(c["reviews"]):
            _outcome_card(review, outcome)
        if c["reviews"][-1][1] is CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE and not pending:
            if st.button("Review again", key=f"again-{c['candidate'].candidate_id}"):
                st.session_state[f"again-{c['candidate'].candidate_id}"] = True
                st.rerun()
            if st.session_state.get(f"again-{c['candidate'].candidate_id}"):
                _review_form(service, view, c["candidate"], actor)


def _outcome_card(review, outcome: CorrespondenceOutcome) -> None:
    kind = {"HUMAN_VERIFIED_CORRESPONDENCE": "ok", "POSSIBLE_CORRESPONDENCE": "warn",
            "NO_CORRESPONDENCE": "stop"}[outcome.value]
    text = {
        "HUMAN_VERIFIED_CORRESPONDENCE": "This does not establish buyer intent or authorize outreach.",
        "POSSIBLE_CORRESPONDENCE": "Not verified. The review is preserved; preparation is not available from it.",
        "NO_CORRESPONDENCE": "A required check was contradicted. The review is preserved.",
    }[outcome.value]
    notice(e2a.OUTCOME_LABELS[outcome], f"{text} Reviewed by {review.reviewer} on {when(review.reviewed_at)}.", kind)
    with st.expander("Evidence and findings"):
        for o in review.candidate.observations:
            html(f'<div class="bc-quote">{e(o.exact_evidence_content)}</div>')
        rows = "".join(f"<div>{e(f.dimension.label)}</div><div><b>{e(e2a.FINDING_LABELS[f.finding])}</b></div>"
                       for f in review.findings if f.finding is not DimensionFinding.NOT_APPLICABLE)
        html(f'<div class="bc-kv">{rows}<div>Evidence sufficient</div><div><b>{e(review.sufficiency.value.title())}'
             f'</b></div><div>Revalidation needed</div><div><b>{"Yes" if review.baec_revalidation_required else "No"}'
             f"</b></div></div>")


def _review_form(service: ConsoleService, view: AccountView, candidate, actor: str) -> None:
    packet = attempt(lambda: service.review_packet(candidate.candidate_id), failure="The review could not be opened.")
    if packet is None:
        return
    plan = packet.plan
    record = view.current_baec
    refs = set(e2a.candidate_evidence_refs(packet))
    measurements = [m for m in packet.ledger.measurements if m.measurement_id in refs]
    left, right = st.columns(2)
    with left:
        html(f'<div class="bc-card"><div class="bc-label">What the buyer said</div>'
             f'<div class="bc-quote">{e(plan.condition_reference)}</div><div class="bc-label" style="margin-top:12px">'
             f'Condition</div><div class="bc-kv">' + _stringency_rows(record.candidate.stringency) + "</div></div>")
    with right:
        items = "".join(f'<div class="bc-quote">{e(o.exact_evidence_content)}</div><div class="bc-muted">'
                        f'{e(o.source_type)} · {e(when(o.observed_at, False))}</div>' for o in candidate.observations)
        aid = ""
        for m in measurements:
            check = e2a.threshold_arithmetic(plan.threshold, m.output_value)
            aid += (f'<div class="bc-kv"><div>Derived measurement</div><div><b>{m.output_value}{m.output_unit}</b> '
                    f'({e(m.formula)})</div>')
            if check is not None:
                aid += (f'<div>Arithmetic check</div><div>{m.output_value} vs {e1a.COMPARATOR_LABELS[plan.threshold.comparator].lower()} '
                        f'{plan.threshold.value}: {"meets" if check else "does not meet"} the stated threshold '
                        f'(display only; your findings decide)</div>')
            aid += "</div>"
        html(f'<div class="bc-card"><div class="bc-label">What changed</div>{items}{aid}</div>')
    html('<div class="bc-card"><div class="bc-label">Engine 2</div><div class="bc-muted">Engine 2 derives the outcome '
         'from your findings with a fixed rule: any required check contradicted gives no correspondence; every '
         'required check supported, evidence sufficient, and no revalidation needed gives human-verified '
         'correspondence; anything else stays possible correspondence.</div></div>')
    html('<div class="bc-h">Does this evidence correspond to the buyer\'s previously stated condition?</div>')
    key = candidate.candidate_id
    answers, notes = {}, {}
    options = [DimensionFinding.SUPPORTED, DimensionFinding.CONTRADICTED, DimensionFinding.UNRESOLVED]
    for dim in CorrespondenceDimension:
        if dim not in plan.required_dimensions:
            continue
        c1, c2 = st.columns([3, 2])
        answers[dim] = c1.radio(e2a.DIMENSION_QUESTIONS[dim], options, index=None, horizontal=True,
                                format_func=e2a.FINDING_LABELS.get, key=f"{key}-{dim.value}")
        notes[dim] = c2.text_input("Note (optional)", key=f"{key}-n-{dim.value}")
    sufficiency = st.radio("Is the evidence sufficient to judge correspondence?", list(SufficiencyFinding), index=None,
                           horizontal=True, format_func=lambda s: s.value.title(), key=f"{key}-suff")
    revalidate = st.checkbox("The buyer's original condition needs revalidation (for example, the contact or "
                             "situation changed)", key=f"{key}-reval")
    trigger = st.text_input("What changed", key=f"{key}-trig") if revalidate else ""
    with st.expander("Evidence quality notes (optional)"):
        checks = {name: st.text_input(name.label, key=f"{key}-c-{name.value}") for name in CrossCuttingCheckName}
    answered = all(v is not None for v in answers.values()) and sufficiency is not None
    verifies = (answered and all(v is DimensionFinding.SUPPORTED for v in answers.values())
                and sufficiency is SufficiencyFinding.YES and not revalidate)

    def submit(findings, suff):
        return service.submit_correspondence_review(
            candidate.candidate_id, displayed_context_digest=packet.context_digest,
            findings={d: f.value for d, f in findings.items()}, notes=notes, sufficiency=suff.value,
            revalidation_triggers=(trigger or "Revalidation flagged by reviewer",) if revalidate else (),
            check_notes=checks, actor=actor)

    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    if c1.button("Verify correspondence", type="primary", disabled=not verifies, key=f"{key}-verify"):
        if attempt(lambda: submit(answers, sufficiency), success="Review recorded."):
            st.session_state.pop(f"again-{key}", None)
            st.rerun()
    if c2.button("Not enough evidence", disabled=verifies, key=f"{key}-insufficient"):
        filled = {d: (v if v is not None else DimensionFinding.UNRESOLVED) for d, v in answers.items()}
        if attempt(lambda: submit(filled, sufficiency or SufficiencyFinding.NO), success="Review recorded."):
            st.session_state.pop(f"again-{key}", None)
            st.rerun()
    with c3:
        boundary("Verify requires every check supported and the evidence sufficient. \"Not enough evidence\" records "
                 "unanswered checks as unresolved.")
    technical({"Correspondence candidate": candidate.candidate_id, "Review context digest": packet.context_digest,
               "Monitoring plan": f"{plan.monitoring_plan_id} v{plan.plan_version}"})


# --- PREPARE ----------------------------------------------------------------------------------------------------


def _evidence_summary(candidate) -> str:
    text = "; ".join(o.exact_evidence_content for o in candidate.observations)
    return text if len(text) <= 110 else text[:107] + "..."


def _prepare(service: ConsoleService, view: AccountView, actor: str) -> None:
    verified = view.verified_correspondences()
    if not verified:
        notice("Preparation not eligible", "Preparation needs human-verified correspondence. Review possible "
               "correspondence in Recognize first.", "warn")
        return
    ordered = list(reversed(verified))  # newest first for display only; nothing is selected on the record
    if len(ordered) > 1:
        labels = {v["review"].review_id: f"Verified {when(v['review'].reviewed_at, False)} · "
                                         f"{_evidence_summary(v['candidate'])}" for v in ordered}
        chosen = st.selectbox("Verified correspondence", list(labels), format_func=labels.get,
                              key=f"corr-{view.account.account_id}",
                              help="Preparation is bound to the correspondence you prepare from. A newer one never "
                                   "replaces it.")
        selected = next(v for v in ordered if v["review"].review_id == chosen)
    else:
        selected = ordered[0]
    review, candidate = selected["review"], selected["candidate"]
    items = "".join(f'<div class="bc-quote">{e(o.exact_evidence_content)}</div>' for o in candidate.observations)
    html(f'<div class="bc-card"><div class="bc-label">Verified correspondence {badge("Human verified", "ok")}</div>'
         f'{items}<div class="bc-muted">Verified by {e(review.reviewer)} on {e(when(review.reviewed_at))}. '
         'This does not establish buyer intent or authorize outreach.</div></div>')
    for later, outcome in selected["later_reviews"]:
        notice("A later review of this evidence exists",
               f"{e2a.OUTCOME_LABELS[outcome]}, recorded {when(later.reviewed_at)} by {later.reviewer}. Preparation "
               "from the correspondence above stays bound to it; decide whether to block it.", "warn")
    _block_controls(service, review, selected, actor)

    requests = [r for r in view.requests if r["review_id"] == review.review_id]
    request = requests[-1] if requests else None
    preparation = None
    if request is not None:
        preps = [p for p in view.preparations if p["request_id"] == request["request_id"]]
        preparation = preps[-1] if preps else None
    if preparation is not None:
        _preparation_view(service, view, preparation, request, actor)
    technical({"Engine 2 review (bound correspondence)": review.review_id,
               "Correspondence candidate": candidate.candidate_id,
               "Monitoring plan": f"{candidate.monitoring_plan_id} v{candidate.plan_version}"},
              "Technical details for this correspondence")
    if selected["blocked"]:
        return
    if request is None or (preparation and _all_rejected(preparation)):
        _request_or_refusal(service, view, review, actor)
    elif preparation is None:
        notice("Preparation requested", f"Requested by {request['requested_by']} on {when(request['requested_at'])} "
               "from this correspondence. This authorizes drafting only.", "")
        if st.button("Prepare drafts", type="primary", key=f"gen-{request['request_id']}"):
            if attempt(lambda: service.generate_preparation(request["request_id"], actor="Deterministic preparation"),
                       success="Drafts prepared and checked by Engine 3.", failure="Engine 3 refused preparation."):
                st.rerun()
    elif any(o["artifact"] and not o["report"]["allowed"] for o in preparation["artifacts"]):
        with st.expander("Request preparation again with different choices"):
            boundary("A new request replaces this one. This preparation and its validation results stay on record.")
            _request_or_refusal(service, view, review, actor, key="again")


def _request_or_refusal(service: ConsoleService, view: AccountView, review, actor: str, key: str = "") -> None:
    snap, report = service.preparation_eligibility(review.review_id)
    if not report.allowed:
        notice("Preparation not eligible", " ".join(e3a.reason_text(i.reason_code.value) for i in report.issues),
               "stop")
        technical({"Engine 3 reason codes": ", ".join(i.reason_code.value for i in report.issues),
                   "Lineage": snap.get("lineage_error") or "reconstructed"}, f"Technical details {key}".strip())
        return
    _request_form(service, view, review, snap, actor, key=key)


def _block_controls(service: ConsoleService, review, selected: dict, actor: str) -> None:
    rid = review.review_id
    history = selected["block_history"]
    if selected["blocked"]:
        last = history[-1]
        notice("Preparation is blocked by human decision",
               f"Blocked by {last['decided_by']} on {when(last['decided_at'])}"
               + (f": {last['reason'].rstrip('.')}." if last["reason"] else ".")
               + " Engine 3 refuses preparation while it stays "
               "blocked.", "stop")
        with st.expander("Lift the block"):
            reason = st.text_input("Why the block is being lifted (optional)", key=f"unblock-r-{rid}")
            if st.button("Lift block", key=f"unblock-{rid}"):
                if attempt(lambda: service.unblock_preparation(rid, reason=reason, actor=actor),
                           success="Block lifted. The earlier decision stays on record."):
                    st.rerun()
        return
    with st.expander("Block preparation for this correspondence"):
        boundary("Use this when re-engagement would be inappropriate, for example because of the buyer's "
                 "circumstances. It is recorded with your name and can be lifted later by a new decision.")
        reason = st.text_input("Reason (optional)", key=f"block-r-{rid}")
        if st.button("Block preparation", key=f"block-{rid}"):
            if attempt(lambda: service.block_preparation(rid, reason=reason, actor=actor),
                       success="Preparation is blocked by human decision."):
                st.rerun()


def _all_rejected(preparation: dict) -> bool:
    from .console_service import preparation_status
    return preparation_status(preparation, preparation["reviews"]) == "Rejected"


def _request_form(service: ConsoleService, view: AccountView, review, snap: dict, actor: str, key: str = "") -> None:
    if not key:
        html('<div class="bc-h">Request preparation</div>')
        notice("Human-verified correspondence", "This does not establish buyer intent or authorize outreach.", "ok")
    rows = e3a.grounding_rows(snap)
    with st.form(f"prep-{review.review_id}{key}"):
        c1, c2 = st.columns(2)
        channel = c1.radio("Prepare for", list(CHANNEL_LABELS), format_func=CHANNEL_LABELS.get, horizontal=True,
                           key=f"ch{key}")
        goal = c2.selectbox("Goal of the conversation", list(PreparationGoal), index=1, format_func=GOAL_LABELS.get,
                            key=f"goal{key}")
        use = st.radio("How may drafts refer to the buyer's earlier statement?", ["PARAPHRASE", "QUOTE"],
                       format_func={"PARAPHRASE": "In my own words", "QUOTE": "Quote it exactly"}.get, horizontal=True,
                       key=f"use{key}")
        paraphrase = st.text_input("How should the buyer's condition be described back to them?", key=f"para{key}",
                                   placeholder="e.g. a price increase above 8% around the July renewal could make it "
                                               "worth revisiting the contract",
                                   help="Completes the sentence \"When we last spoke, you mentioned that ...\"")
        st.markdown("**What may be used with the buyer** — undecided items are treated as internal only")
        dispositions, identities, adversity = {}, {}, {}
        for row in rows:
            st.markdown(f"{row['label']}: _{row['value']}_")
            c1, c2 = st.columns(2)
            dispositions[row["id"]] = c1.selectbox(
                "Use", list(ExternalReferenceDisposition), index=3, key=f"d{key}-{row['id']}",
                format_func=e3a.DISPOSITION_LABELS.get, label_visibility="collapsed").value
            if row.get("evidence"):
                identities[row["id"]] = c2.selectbox(
                    "Source", list(SourceIdentityDisclosure), index=2, key=f"s{key}-{row['id']}",
                    format_func=e3a.SOURCE_IDENTITY_LABELS.get, label_visibility="collapsed").value
                related = st.checkbox("Involves an adverse event (shortage, layoffs, service failure)",
                                      key=f"a{key}-{row['id']}")
                reviewed = st.checkbox("I have reviewed that context", key=f"ar{key}-{row['id']}") if related else False
                adversity[row["id"]] = {"related": related, "reviewed": reviewed}
        boundary("A Preparation Request authorizes drafting only. It is never outreach authorization.")
        if st.form_submit_button("Prepare from this correspondence", type="primary"):
            request = {"channel": channel.value, "goal": goal.value, "statement_use": use, "paraphrase": paraphrase,
                       "dispositions": dispositions, "source_identity": identities, "adversity": adversity}
            if attempt(lambda: service.request_preparation(review.review_id, request, actor=actor),
                       success="Preparation requested."):
                st.rerun()


def _render_artifact(artifact: dict, report: dict) -> str:
    refused = {i["segment_id"] for i in report.get("issues", []) if i.get("segment_id")}
    kind = ArtifactType(artifact["artifact_type"])
    segs = artifact["segments"]

    def text(s):
        cls = ' class="bc-seg-refused"' if s["segment_id"] in refused else ""
        return f"<span{cls}>{e(s['text'])}</span>"

    if kind is ArtifactType.DISCOVERY_QUESTION_SET:
        return '<ul class="bc-questions">' + "".join(f"<li>{text(s)}</li>" for s in segs) + "</ul>"
    if kind is ArtifactType.FOLLOW_UP_EMAIL:
        return '<div class="bc-output">' + "".join(f"<p>{text(s)}</p>" for s in segs) + "</div>"
    return '<div class="bc-output"><p>' + " ".join(text(s) for s in segs) + "</p></div>"


def _preparation_view(service: ConsoleService, view: AccountView, preparation: dict, request: dict, actor: str) -> None:
    pid = preparation["preparation_id"]
    outputs = [o for o in preparation["artifacts"] if o["artifact"]]
    html(f'<div class="bc-muted">Prepared by BAEC · Deterministic preparation · {e(when(preparation["generated_at"]))}'
         f' · requested by {e(request["requested_by"])}</div>')
    for o in preparation["artifacts"]:
        if not o["artifact"]:
            notice("Preparation could not be built", o["report"].get("structural_error", ""), "stop")
    labels = [e3a.ARTIFACT_LABELS[ArtifactType(o["artifact"]["artifact_type"])].upper() for o in outputs]
    tabs = st.tabs(labels)
    for tab, output in zip(tabs, outputs):
        with tab:
            _artifact_panel(service, pid, output, preparation, actor)
    reviews = preparation["reviews"]
    if outputs and all(o["artifact"]["artifact_id"] in reviews or not o["report"]["allowed"] for o in outputs):
        html('<div class="bc-final"><div class="bc-final-main">BAEC prepares the conversation. The salesperson decides '
             'whether to have it.</div><div class="bc-final-sub">Prepared material is for human use. Nothing is sent, '
             'scheduled, or recorded in a CRM.</div></div>')
    prov = preparation["provenance"]
    technical({"Console generator": prov["console_generator_origin"],
               "Engine 3 validator origin": f"{prov['engine3_validator_origin']} (compatibility value)",
               "Origin note": f"{prov['origin_compatibility_note']}: {ORIGIN_COMPATIBILITY_TEXT}",
               "Bound correspondence (Engine 2 review)": prov["bound_review_id"],
               "Correspondence candidate": prov["bound_candidate_id"],
               "Preparation": pid, "Preparation Request": request["request_id"],
               "Context digest": request["context_digest"]}, "Technical details for this preparation")


def _artifact_panel(service: ConsoleService, pid: str, output: dict, preparation: dict, actor: str) -> None:
    artifact, report = output["artifact"], output["report"]
    aid = artifact["artifact_id"]
    review = preparation["reviews"].get(aid)
    audience = "Internal (seller only)" if output.get("audience") == "INTERNAL" else "Buyer-facing draft"
    if review is not None:
        state = {"ACCEPT_AS_DRAFTED": badge("Prepared for human use", "ok"),
                 "EDIT": badge("Edited · prepared for human use", "ok"), "REJECT": badge("Rejected", "stop")}
        top = state[review["decision"]]
    elif report["allowed"]:
        top = badge("Passed Engine 3 validation", "ok") + " " + badge("Awaiting human review", "warn")
    else:
        top = badge("Refused by Engine 3", "stop")
    html(f'<div class="bc-label">{e(audience)} {top}</div>')
    shown = artifact
    if review and review["decision"] == "EDIT":
        edit = next(x for x in preparation["edits"] if x["edit_id"] == review["edit_id"])
        shown = edit["artifact"]["artifact"]
    html(_render_artifact(shown, report if shown is artifact else {}))
    if not report["allowed"]:
        lines = dict.fromkeys(e3a.reason_text(i["reason_code"]) for i in report["issues"])
        reasons = "".join(f"<li>{e(line)}</li>" for line in lines)
        html(f'<div class="bc-notice stop"><div class="bc-notice-title">Engine 3 validation refused this draft</div>'
             f'<div class="bc-notice-text"><ul style="margin:4px 0 0 18px">{reasons}</ul>It is shown exactly as built '
             f'and is not repaired. Change the disclosure choices and request preparation again below, or reject it.'
             f'</div></div>')
        technical({"Reason codes": ", ".join(f"{i['reason_code']} ({i['segment_id'] or 'artifact'})"
                                             for i in report["issues"])}, "Technical details for this draft")
    if review is not None:
        boundary(f"Reviewed by {review['reviewed_by']} on {when(review['reviewed_at'])}"
                 + (f". Reason: {review['reason']}" if review.get("reason") else "."))
        return
    _review_controls(service, pid, artifact, report, preparation, actor)


def _review_controls(service: ConsoleService, pid: str, artifact: dict, report: dict, preparation: dict,
                     actor: str) -> None:
    aid = artifact["artifact_id"]
    key = f"{pid}-{aid}"
    if st.session_state.get(f"mode-{key}") == "edit":
        _edit_controls(service, pid, artifact, preparation, actor)
        return
    if report["allowed"]:
        attest = {k: st.checkbox(label, key=f"{key}-{k}") for k, label in ATTESTATIONS}
        c1, c2, c3 = st.columns(3)
        if c1.button("Accept as drafted", type="primary", key=f"{key}-accept"):
            if attempt(lambda: service.review_artifact(pid, aid, HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                                       attestations=attest, actor=actor),
                       success="Prepared for human use."):
                st.rerun()
        if c2.button("Edit", key=f"{key}-edit"):
            st.session_state[f"mode-{key}"] = "edit"
            st.rerun()
    else:
        c3 = st.container()
    with c3:
        reason = st.text_input("Reason for rejecting (optional)", key=f"{key}-reason", label_visibility="collapsed",
                               placeholder="Reason for rejecting (optional)")
        if st.button("Reject", key=f"{key}-reject"):
            if attempt(lambda: service.review_artifact(pid, aid, HumanReviewDecision.REJECT, attestations={},
                                                       reason=reason, actor=actor), success="Draft rejected."):
                st.rerun()


def _edit_controls(service: ConsoleService, pid: str, artifact: dict, preparation: dict, actor: str) -> None:
    aid = artifact["artifact_id"]
    key = f"{pid}-{aid}"
    boundary("Your edit becomes a new version. The generated draft is preserved, and Engine 3 validates the edit.")
    segments = []
    for s in artifact["segments"]:
        c1, c2 = st.columns([5, 1])
        text = c1.text_area(CLAIM_LABELS[ClaimType(s["claim_type"])], value=s["text"], key=f"{key}-t-{s['segment_id']}",
                            height=70)
        if not c2.checkbox("Remove", key=f"{key}-rm-{s['segment_id']}"):
            segments.append({**s, "text": text})
    with st.expander("Add a sentence"):
        new_text = st.text_input("Sentence", key=f"{key}-new")
        kind = st.radio("It is a", ["QUESTION", "COURTESY", "FACT"], horizontal=True, key=f"{key}-newkind",
                        format_func={"QUESTION": "Neutral question", "COURTESY": "Courtesy or transition",
                                     "FACT": "Statement of fact"}.get)
        snap = service.preparation_view(pid)["request"]["snapshot"]
        rows = {r["id"]: r["label"] for r in e3a.grounding_rows(snap)}
        cited = st.multiselect("Supported by (for statements of fact)", list(rows), format_func=rows.get,
                               key=f"{key}-newrefs") if kind == "FACT" else []
        if new_text.strip():
            claim = {"QUESTION": (ClaimType.QUESTION, ClaimSemanticClass.NEUTRAL_QUESTION),
                     "COURTESY": (ClaimType.COURTESY_OR_TRANSITION, ClaimSemanticClass.COURTESY),
                     "FACT": (ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.OTHER_FACT)}[kind]
            segments.append(e3a.segment_to_dict(DraftSegment(
                f"H{len(segments) + 1}", new_text.strip(), claim[0], claim[1], grounding_refs=tuple(cited),
                reference_use_mode=ReferenceUseMode.PARAPHRASE if cited else ReferenceUseMode.DIRECT,
                claim_field=ClaimField.OTHER if kind == "FACT" else ClaimField.NONE)))
    c1, c2 = st.columns(2)
    if c1.button("Validate edit", type="primary", key=f"{key}-validate"):
        result = attempt(lambda: service.edit_artifact(pid, aid, segments, actor=actor))
        if result:
            st.session_state[f"edit-{key}"] = result["edit_id"]
            st.rerun()
    if c2.button("Cancel edit", key=f"{key}-cancel"):
        st.session_state.pop(f"mode-{key}", None)
        st.rerun()
    edit_id = st.session_state.get(f"edit-{key}")
    edit = next((x for x in service.preparation_view(pid)["edits"] if x["edit_id"] == edit_id), None)
    if edit is None:
        return
    edited, report = edit["artifact"]["artifact"], edit["artifact"]["report"]
    html('<div class="bc-label" style="margin-top:12px">Your edited version</div>' + _render_artifact(edited, report))
    if not report["allowed"]:
        notice("Engine 3 validation refused this edit",
               " ".join(e3a.reason_text(i["reason_code"]) for i in report["issues"]), "stop")
        return
    notice("Your edit passed Engine 3 validation", kind="ok")
    attest = {k: st.checkbox(label, key=f"{key}-e-{k}") for k, label in ATTESTATIONS}
    if st.button("Accept edited version", type="primary", key=f"{key}-accept-edit"):
        if attempt(lambda: service.review_artifact(pid, aid, HumanReviewDecision.EDIT, attestations=attest,
                                                   edit_id=edit_id, actor=actor), success="Prepared for human use."):
            st.session_state.pop(f"mode-{key}", None)
            st.rerun()


# --- account state and timeline ------------------------------------------------------------------------------


def _account_state(service: ConsoleService, view: AccountView, actor: str) -> None:
    html(f'<div class="bc-card"><div class="bc-label">Current account state</div><div class="bc-head-value">'
         f'{e(STATE_LABELS[view.account.state])}</div><div class="bc-muted">Account states change only through '
         'separate human judgments checked by Engine 1. A buyer condition, a signal, or a prepared draft never changes '
         'it.</div></div>')
    confirmed = [b for b in view.baecs if b.classification is BaecClassification.CONFIRMED_BAEC]
    interactions = {i.interaction_id: f"{(m or {}).get('buyer_role') or 'Buyer'} · {when(i.occurred_at)}"
                    for i, m in view.interactions}
    if confirmed:
        with st.expander("Record a dormancy judgment"):
            with st.form(f"judg-{view.account.account_id}"):
                answers = list(ReviewAnswer)
                plaus = st.radio("Could the condition plausibly occur within a commercially meaningful horizon?",
                                 answers, index=3, horizontal=True, format_func=e1a.REVIEW_ANSWER_LABELS.get)
                addr = st.radio("Could we credibly respond if it occurred?", answers, index=3, horizontal=True,
                                format_func=e1a.REVIEW_ANSWER_LABELS.get)
                notes = st.text_input("Notes (optional)")
                if st.form_submit_button("Record judgment", type="primary"):
                    if attempt(lambda: service.record_dormancy_judgment(confirmed[-1].baec_id, plausibility=plaus,
                                                                        addressability=addr, notes=notes, actor=actor),
                               success="Dormancy judgment recorded."):
                        st.rerun()
    targets = [AccountState.CONDITIONALLY_DORMANT, AccountState.NO_PLAUSIBLE_PATH, AccountState.ACTIVE_OPPORTUNITY]
    target = st.selectbox("Change account state to", targets, format_func=STATE_LABELS.get, index=None,
                          placeholder="Choose a state")
    if target is None:
        return
    inputs: dict = {}
    if target is AccountState.CONDITIONALLY_DORMANT:
        judgments = [(b.baec_id, j) for b in confirmed for j in view.judgments.get(b.baec_id, [])]
        if not judgments:
            notice("Record a dormancy judgment first", kind="warn")
            return
        baec_id, judgment = judgments[-1]
        inputs.update(baec_id=baec_id, judgment_id=judgment.judgment_id)
        st.caption(f"Uses the latest judgment: plausibility {judgment.judgment.plausibility.value.lower()}, "
                   f"addressability {judgment.judgment.addressability.value.lower()}.")
    if target is AccountState.NO_PLAUSIBLE_PATH:
        inputs["ground"] = st.selectbox("Ground", list(NoPlausiblePathGround), format_func=e1a.GROUND_LABELS.get)
        inputs["reason"] = st.text_input("Reason")
    if interactions and target is AccountState.ACTIVE_OPPORTUNITY:
        inputs["eval_interaction"] = st.selectbox("Interaction showing the buyer is evaluating", list(interactions),
                                                  format_func=interactions.get)
        inputs["eval_excerpt"] = st.text_input("Exact words from that interaction")
    if interactions and view.account.state is AccountState.ACTIVE_OPPORTUNITY and target is not AccountState.ACTIVE_OPPORTUNITY:
        inputs["non_eval_interaction"] = st.selectbox("Interaction showing the buyer stopped evaluating",
                                                      list(interactions), format_func=interactions.get)
        inputs["non_eval_excerpt"] = st.text_input("Exact words from that interaction ")
    preview = attempt(lambda: service.preview_state_change(view.account.account_id, target, inputs),
                      failure="Engine 1 cannot evaluate this change yet.")
    if preview is None:
        return
    blockers = e1a.preview_blockers(preview)
    if blockers:
        notice("Engine 1 would refuse this change", " ".join(blockers), "stop")
        return
    notice("Engine 1 would allow this change", "It is recorded only when you confirm it.", "ok")
    if st.button(f"Confirm: move to {STATE_LABELS[target]}", type="primary"):
        if attempt(lambda: service.change_account_state(view.account.account_id, target, inputs, actor=actor),
                   success=f"Account state changed to {STATE_LABELS[target]}."):
            st.rerun()


def timeline(items, *, show_account: dict | None = None) -> None:
    if not items:
        notice("No history yet")
        return
    parts = []
    for item in reversed(items):
        account = f"{e(show_account.get(item.account_id, ''))} · " if show_account else ""
        detail = f'<div class="bc-tl-detail">{e(item.detail)}</div>' if item.detail else ""
        by = f" · {e(item.actor)}" if item.actor and item.actor_kind == "Human" else ""
        parts.append(f'<div class="bc-tl-item"><div class="bc-tl-when">{account}{e(when(item.at))}{by}</div>'
                     f'<div class="bc-tl-title">{e(item.title)}<span class="bc-prov">{e(item.actor_kind)}</span></div>'
                     f'{detail}</div>')
    html('<div class="bc-tl">' + "".join(parts) + "</div>")
