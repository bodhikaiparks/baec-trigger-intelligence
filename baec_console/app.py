"""BAEC Console: Trigger Intelligence. A local, offline product shell over BAEC Engines 1 to 3.

Run from the baec_console directory (see README_CONSOLE.md):  ../.venv/bin/streamlit run app.py

No model, network call, email, CRM write, or external action exists here. Data is stored in local SQLite files
under baec_console/.data/ (or BAEC_CONSOLE_DATA_DIR), created on first run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baec_console import demo_seed, ui_pages, ui_workspace  # noqa: E402
from baec_console.console_service import Console  # noqa: E402
from baec_console.styles import CSS  # noqa: E402
from baec_console.ui_common import PAGES, attempt, go, html  # noqa: E402

DATA_DIR = Path(os.environ.get("BAEC_CONSOLE_DATA_DIR") or Path(__file__).resolve().parent / ".data")


@st.cache_resource
def _console(path: str) -> Console:
    # Holds only file paths and a clock; every operation opens and closes its own connections.
    return Console(path)


def _sidebar(console: Console) -> None:
    with st.sidebar:
        html('<div class="bc-brand"><div class="bc-brand-mark">BAEC</div>'
             '<div class="bc-brand-sub">Trigger Intelligence</div></div>')
        current = "Accounts" if st.session_state.page == "Workspace" else st.session_state.page
        for page in PAGES:
            if st.button(page, key=f"nav-{page}", type="primary" if page == current else "secondary"):
                go(page)
                st.rerun()
        html('<div class="bc-side-label">You</div>')
        name = st.text_input("Your name", value=console.actor, key="actor-name", label_visibility="collapsed",
                             help="Recorded on every human action. Not authentication.")
        if name.strip() and name.strip() != console.actor:
            attempt(lambda: console.set_actor(name))
        html('<div class="bc-side-label">Synthetic demo data</div>')
        if st.button("Load demo: start from beginning", key="seed-beginning"):
            account_id = attempt(lambda: demo_seed.load(console, "beginning"), success="Synthetic demo account loaded.")
            if account_id:
                go("Workspace", account_id, "Remember")
                st.rerun()
        if st.button("Load demo: completed example", key="seed-completed"):
            account_id = attempt(lambda: demo_seed.load(console, "completed"), success="Completed synthetic example loaded.")
            if account_id:
                go("Workspace", account_id, "Prepare")
                st.rerun()
        if st.button("Reset synthetic data", key="seed-reset"):
            attempt(console.reset_synthetic, success="Synthetic data reset. Your accounts were not changed.")
            go("Dashboard")
            st.rerun()
        html('<div class="bc-side-note">Loading or resetting replaces only synthetic data. Accounts you create are '
             'never changed.</div>')
        html('<div class="bc-side-note" style="margin-top:14px">Local and offline. No model, no network, no '
             'outreach.</div>')


def main() -> None:
    st.set_page_config(page_title="BAEC Console", layout="wide", initial_sidebar_state="expanded")
    html(CSS)
    console = _console(str(DATA_DIR))
    if "page" not in st.session_state:  # first run of this browser session: honor a bookmarked workspace URL
        st.session_state.page = "Dashboard"
        if st.query_params.get("account"):
            go("Workspace", st.query_params["account"], st.query_params.get("section"))
    _sidebar(console)
    page = st.session_state.page
    if page == "Workspace" and st.session_state.get("account_id") in {
            a for service in console.services for a in service.account_ids()}:
        st.query_params["account"] = st.session_state.account_id
        ui_workspace.render(console, st.session_state.account_id)
        return
    st.query_params.clear()
    if page == "Accounts":
        ui_pages.accounts(console)
    elif page == "Review Queue":
        ui_pages.review_queue(console)
    elif page == "Preparation":
        ui_pages.preparation(console)
    elif page == "Activity":
        ui_pages.activity(console)
    else:
        ui_pages.dashboard(console)


main()
