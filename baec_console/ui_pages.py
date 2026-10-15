"""Cross-account pages: Dashboard, Accounts, Review Queue, Preparation, Activity. Every figure comes from records."""

from __future__ import annotations

import streamlit as st

from baec_app.domain.enums import BaecClassification

from .console_service import STATE_LABELS, AccountView, Console
from .ui_common import STAGE_LABELS, attempt, e, go, heading, html, notice, show_flash, when
from .ui_workspace import timeline

SECTION_FOR_QUEUE = {"REMEMBER": "Remember", "RECOGNIZE": "Recognize", "PREPARE": "Prepare"}


def _condition(view: AccountView) -> str:
    if view.current_baec is not None:
        return view.current_baec.candidate.buyer_exact_statement or ""
    if view.pending_drafts:
        return "Captured, awaiting confirmation"
    return "None recorded"


def _buyer(view: AccountView) -> str:
    if view.current_baec is not None and view.current_baec.candidate.buyer_role:
        return view.current_baec.candidate.buyer_role
    roles = [m["buyer_role"] for _, m in view.interactions if m and m.get("buyer_role")]
    return roles[-1] if roles else ""


def _name(view: AccountView) -> str:
    return view.display_name + (" (synthetic)" if view.synthetic else "")


def _open_on_select(event, ids: list[str], key: str, sections: list[str | None] | None = None,
                    focus: list[str | None] | None = None) -> None:
    rows = event.selection.rows if event and event.selection else []
    if rows:
        i = rows[0]
        go("Workspace", ids[i], (sections or [None] * len(ids))[i], (focus or [None] * len(ids))[i])
        st.session_state.pop(key, None)
        st.rerun()


def _empty_state(console: Console) -> None:
    notice("No accounts yet", "Add your own account, or load the synthetic Alder Ridge Clinics example to walk the "
           "workflow.")
    c1, c2, c3 = st.columns([1, 1.2, 2])
    if c1.button("Add account", type="primary"):
        go("Accounts")
        st.rerun()
    if c2.button("Load synthetic demo account"):
        from . import demo_seed
        account_id = attempt(lambda: demo_seed.load(console, "beginning"), success="Synthetic demo account loaded.")
        if account_id:
            go("Workspace", account_id)
            st.rerun()


def dashboard(console: Console) -> None:
    heading("Dashboard", "Trigger Intelligence",
            "Buyer-articulated conditions your team is remembering, monitoring, and preparing for.")
    show_flash()
    views = console.account_views()
    m = console.dashboard(views)
    cards = (("Accounts tracked", m["accounts"]), ("Verified buyer conditions", m["verified"]),
             ("Monitoring", m["monitoring"]), ("Correspondences awaiting review", m["awaiting"]),
             ("Ready to prepare", m["ready"]))
    html('<div class="bc-metrics">' + "".join(
        f'<div class="bc-metric"><div class="bc-metric-value">{v}</div><div class="bc-metric-label">{e(k)}</div></div>'
        for k, v in cards) + "</div>")
    if not views:
        _empty_state(console)
        return
    views.sort(key=lambda v: v.last_activity.timestamp() if v.last_activity else 0, reverse=True)
    rows = [{"Account": _name(v), "Buyer / Role": _buyer(v), "Condition": _condition(v),
             "Workflow stage": STAGE_LABELS.get(v.stage, v.stage), "Last activity": when(v.last_activity),
             "Next action": v.next_action} for v in views]
    html('<div class="bc-h">Accounts in motion</div>')
    event = st.dataframe(rows, hide_index=True, on_select="rerun",
                         selection_mode="single-row", key="dash-table",
                         column_config={"Condition": st.column_config.TextColumn(width="medium"),
                                        "Next action": st.column_config.TextColumn(width="medium")})
    st.caption("Select a row to open the account workspace.")
    _open_on_select(event, [v.account.account_id for v in views], "dash-table")


def accounts(console: Console) -> None:
    heading("Accounts", "Accounts", "Every account you track, with its account state and where it is in the workflow.")
    show_flash()
    with st.expander("Add account", expanded=st.session_state.pop("open_add", False)):
        with st.form("add-account", clear_on_submit=True):
            name = st.text_input("Account name")
            industry = st.text_input("Industry (optional)")
            note = st.text_input("Owner or note (optional)")
            st.caption("Creating an account records nothing about the buyer. Its account state starts unclassified.")
            if st.form_submit_button("Add account", type="primary"):
                account_id = attempt(lambda: console.local.create_account(name, industry=industry, owner_note=note,
                                                                          actor=console.actor),
                                     success="Account added.")
                if account_id:
                    go("Workspace", account_id, "Remember")
                    st.rerun()
    views = console.account_views()
    query = st.text_input("Search accounts", placeholder="Search by name, industry, or condition",
                          label_visibility="collapsed")
    if query:
        q = query.lower()
        views = [v for v in views if q in " ".join([_name(v), v.meta.get("industry") or "", _condition(v)]).lower()]
    if not views:
        notice("No matching accounts") if query else _empty_state(console)
        return
    rows = [{"Account": _name(v), "Industry": v.meta.get("industry") or "", "Account state": STATE_LABELS[v.account.state],
             "Verified conditions": sum(1 for b in v.baecs if b.classification is BaecClassification.CONFIRMED_BAEC),
             "Workflow stage": STAGE_LABELS.get(v.stage, v.stage), "Last activity": when(v.last_activity)}
            for v in views]
    event = st.dataframe(rows, hide_index=True, on_select="rerun",
                         selection_mode="single-row", key="acct-table")
    _open_on_select(event, [v.account.account_id for v in views], "acct-table")


def review_queue(console: Console) -> None:
    heading("Review Queue", "Review Queue",
            "Decisions that need a human: confirming buyer conditions, reviewing correspondence, and reviewing "
            "prepared drafts.")
    show_flash()
    queue = console.review_queue()
    names = {v.account.account_id: _name(v) for v in console.account_views()}
    for title, rows, key in (("Pending", queue["pending"], "q-pending"), ("Completed", queue["completed"], "q-done")):
        html(f'<div class="bc-h">{title} ({len(rows)})</div>')
        if not rows:
            st.caption("Nothing pending." if key == "q-pending" else "No completed reviews yet.")
            continue
        table = [{"Account": names.get(r["account_id"], r["account"]), "Review type": r["type"],
                  "Created": when(r["created"]), "Evidence / condition": r["summary"], "Status": r["status"]}
                 for r in rows]
        event = st.dataframe(table, hide_index=True, on_select="rerun",
                             selection_mode="single-row", key=key,
                             column_config={"Evidence / condition": st.column_config.TextColumn(width="large")})
        _open_on_select(event, [r["account_id"] for r in rows], key,
                        [SECTION_FOR_QUEUE[r["stage"]] for r in rows], [r["ref"] for r in rows])


def preparation(console: Console) -> None:
    heading("Preparation", "Preparation",
            "Prepared conversation material across accounts. Nothing here is sent; the salesperson decides.")
    show_flash()
    items = console.preparations()
    for status in ("Needs review", "Accepted", "Rejected"):
        rows = [p for p in items if p["status"] == status]
        html(f'<div class="bc-h">{status} ({len(rows)})</div>')
        if not rows:
            st.caption("None.")
            continue
        table = []
        for p in rows:
            snap = p["request"]["snapshot"]
            evidence = "; ".join(x["text"] for x in snap.get("evidence", [])) or ""
            outputs = [o for o in p["preparation"]["artifacts"] if o["artifact"]]
            decided = sum(1 for o in outputs if o["artifact"]["artifact_id"] in p["reviews"])
            passed = sum(1 for o in outputs if o["report"]["allowed"])
            table.append({"Account": p["account_name"] + (" (synthetic)" if p["workspace"] == "synthetic" else ""),
                          "Condition": snap.get("buyer_statement", ""), "Correspondence": evidence,
                          "Created": when(p["preparation"]["generated_at"]),
                          "Review status": f"{status} · {decided} reviewed · {passed} of {len(outputs)} passed Engine 3"})
        key = f"prep-{status}"
        event = st.dataframe(table, hide_index=True, on_select="rerun",
                             selection_mode="single-row", key=key)
        _open_on_select(event, [p["request"]["account_id"] for p in rows], key, ["Prepare"] * len(rows),
                        [p["preparation"]["preparation_id"] for p in rows])


def activity(console: Console) -> None:
    heading("Activity", "Activity",
            "The audit history across accounts, read from Engine 1, Engine 2, and console records.")
    show_flash()
    items = console.activity()
    names = {v.account.account_id: _name(v) for v in console.account_views()}
    if not items:
        notice("No activity yet")
        return
    c1, c2, c3, c4 = st.columns(4)
    chosen_accounts = c1.multiselect("Account", sorted(set(names)), format_func=names.get)
    titles = sorted({i.title.split(":")[0] for i in items})
    chosen_types = c2.multiselect("Event type", titles)
    stages = ["REMEMBER", "MONITOR", "RECOGNIZE", "PREPARE", "ACCOUNT"]
    chosen_stages = c3.multiselect("Workflow stage", stages, format_func=STAGE_LABELS.get)
    dates = c4.date_input("Date", value=(), help="Choose a start and end date")
    if chosen_accounts:
        items = [i for i in items if i.account_id in chosen_accounts]
    if chosen_types:
        items = [i for i in items if i.title.split(":")[0] in chosen_types]
    if chosen_stages:
        items = [i for i in items if i.stage in chosen_stages]
    if isinstance(dates, tuple) and len(dates) == 2:
        items = [i for i in items if dates[0] <= i.at.astimezone().date() <= dates[1]]
    st.caption(f"{len(items)} events")
    timeline(sorted(items, key=lambda i: i.at), show_account=names)
