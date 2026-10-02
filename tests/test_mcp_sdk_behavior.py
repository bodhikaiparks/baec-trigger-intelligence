"""Phase 5B: checks against the exact installed MCP SDK (design checkpoints).

These record how mcp 2.2.0 behaves with the approved Option A contracts. They
use a small test-only server; the production 5B server registers no tools.
"""

import importlib.metadata
import json

import pytest
from mcp import Client
from mcp.server import MCPServer

from baec_app.mcp.contracts import PreviewActiveArgs
from tests.mcp_builders import run


def test_the_recorded_sdk_versions_are_installed():
    assert importlib.metadata.version("mcp") == "2.2.0"
    assert importlib.metadata.version("mcp-types") == "2.2.0"
    assert importlib.metadata.version("pydantic") == "2.13.5"
    assert MCPServer.__module__ == "mcp.server.mcpserver.server"


def test_requirements_pin_the_sdk():
    from pathlib import Path

    lines = (Path(__file__).resolve().parents[1] / "requirements-dev.txt").read_text(encoding="utf-8").split()
    assert "mcp[cli]==2.2.0" in lines


def test_in_process_client_negotiates_a_protocol_version():
    async def main():
        async with Client(MCPServer(name="probe")) as client:
            return client.protocol_version, client.server_info.name

    version, name = run(main)
    assert version == "2026-07-28" and name == "probe"


# --- strict-mode experiment with the real Option A contract --------------------------

reached = []
probe = MCPServer(name="strict-probe")


@probe.tool()
async def preview_active_probe(args: PreviewActiveArgs) -> dict:
    reached.append(args)
    return {"account_id": args.account_id, "observed_at": args.evaluation_evidence.observed_at}


VALID = {
    "account_id": "ACC-1",
    "evaluation_evidence": {
        "account_id": "ACC-1",
        "evidence": {"text": "We have opened a formal supplier review.", "provenance": "BUYER_FACT", "source_id": "INT-2"},
        "observed_at": "2026-01-15T12:00:00.123+05:30",
    },
}


def _with(path, value):
    data = json.loads(json.dumps(VALID))
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


_DELETE = object()

REFUSED = {
    "extra field approved": _with(("approved",), True),
    "nested extra field": _with(("evaluation_evidence", "authorization"), "yes"),
    "account id as int": _with(("account_id",), 7),
    "account id as bool": _with(("account_id",), True),
    "provenance lowercase": _with(("evaluation_evidence", "evidence", "provenance"), "buyer_fact"),
    "provenance near match": _with(("evaluation_evidence", "evidence", "provenance"), "BUYER_FACTS"),
    "provenance as int": _with(("evaluation_evidence", "evidence", "provenance"), 0),
    "provenance as bool": _with(("evaluation_evidence", "evidence", "provenance"), False),
    "naive timestamp": _with(("evaluation_evidence", "observed_at"), "2026-01-15T12:00:00"),
    "unix timestamp": _with(("evaluation_evidence", "observed_at"), 1736942400),
    "malformed identifier": _with(("account_id",), "../ACC-1"),
    "missing required field": _with(("evaluation_evidence",), _DELETE),
}


def _call(arguments):
    async def main():
        async with Client(probe) as client:
            return await client.call_tool("preview_active_probe", {"args": arguments})

    return run(main)


def test_a_valid_strict_contract_reaches_the_async_handler_unchanged():
    before = len(reached)
    result = _call(VALID)
    assert not result.is_error and len(reached) == before + 1
    received = reached[-1]
    assert type(received) is PreviewActiveArgs
    assert received.evaluation_evidence.evidence.provenance == "BUYER_FACT"  # a plain string, as Option A intends
    assert received.evaluation_evidence.observed_at == "2026-01-15T12:00:00.123+05:30"  # lexical text kept


@pytest.mark.parametrize("arguments", REFUSED.values(), ids=REFUSED.keys())
def test_schema_invalid_input_is_refused_and_never_reaches_the_handler(arguments):
    before = len(reached)
    result = _call(arguments)
    assert result.is_error
    assert len(reached) == before


@pytest.mark.xfail(
    strict=True,
    reason="OPEN 5C CHECKPOINT: mcp 2.2.0 returns Pydantic's full validation text, including the rejected "
    "input_value, for SDK-level tool argument errors. The approved design forbids exposing raw rejected values. "
    "Not accepted; must be resolved or escalated in 5C.",
)
def test_sdk_level_argument_errors_do_not_expose_rejected_values():
    secret = "REJECTED-VALUE-SHOULD-NOT-ECHO"
    result = _call(_with(("account_id",), 12345) | {"approved": secret})
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert secret not in text and "input_value" not in text
