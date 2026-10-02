"""The Phase 6C live comparison run. Opt-in only; it never runs in the default test suite.

It is deselected by default (pytest.ini adds -m "not live_claude"), and even when
selected it skips unless the live gate is open (see tests/live/harness.py and
docs/PHASE6C_LIVE_EVALUATION.md). Run one model per invocation, for example:

    BAEC_LIVE_CLAUDE=1 BAEC_LIVE_MODEL=claude-sonnet-5-5 \\
        python -m pytest tests/live/test_live_extraction.py -m live_claude -s -p no:cacheprovider
"""

import os

import pytest

from tests.live.harness import LiveGateClosed, live_gate, load_corpus, run_evaluation

pytestmark = pytest.mark.live_claude


def test_live_extraction_comparison_run():
    try:
        model = live_gate(os.environ)
    except LiveGateClosed as closed:
        pytest.skip(str(closed))
    corpus = load_corpus()  # validated before any database or provider exists
    report = run_evaluation(corpus, model=model)  # the production runtime and the real Anthropic provider
    # Infrastructure invariants. Model behavior is reported, not asserted: it informs the comparison.
    assert report.authority_unchanged
    assert report.calls_attempted == len(corpus.cases)
    assert "provenance_failure" not in report.critical_failures
