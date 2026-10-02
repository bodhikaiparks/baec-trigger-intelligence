"""Phase 5B: static architecture rules for baec_app/mcp/** (design §12, M1–M10 as relevant to 5B).

Production MCP code only; tests are exempt. Every rule also runs against
synthetic violating and allowed snippets to prove it has teeth. Call-target
rules use exact names (or anchored prefixes for the forbidden families), never
substring matching, so preview_move_to_* is never mistaken for move_to_*.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import baec_app.application as application
from tests.test_application_boundary import _in_package, _resolve_relative, imports_package, scan

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "baec_app" / "mcp"

APPROVED_RESOURCE_URIS = {
    "baec://accounts",
    "baec://accounts/{account_id}",
    "baec://accounts/{account_id}/interactions",
    "baec://accounts/{account_id}/transition-history",
    "baec://interactions/{interaction_id}",
    "baec://baecs",
    "baec://baecs/{baec_id}",
    "baec://baecs/{baec_id}/dormancy-judgments",
}


def mcp_modules() -> list[tuple[str, str]]:
    modules = []
    for path in sorted(MCP_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append((".".join(parts), path.read_text(encoding="utf-8")))
    return modules


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


# --- rules ------------------------------------------------------------------------------


def m1_no_data_layer(module: str, source: str) -> list[str]:
    return imports_package(scan(source, module), "baec_app.data")


def m2_application_package_api_only(module: str, source: str) -> list[str]:
    """Only `baec_app.application` itself, and only names in its __all__; never a submodule."""
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = _resolve_relative(module, False, node.level, node.module) if node.level else (node.module or "")
            if _in_package(target, "baec_app.application"):
                if target != "baec_app.application":
                    found.append(f"{module}:{node.lineno} imports submodule {target}")
                for alias in node.names:
                    if alias.name not in application.__all__:
                        found.append(f"{module}:{node.lineno} imports non-public {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("baec_app.application."):
                    found.append(f"{module}:{node.lineno} imports submodule {alias.name}")
    facts = scan(source, module)
    found += [
        f"{module}:{line} dynamically imports {name}"
        for name, line in facts.dynamic_imports
        if name.startswith("baec_app.application.")
    ]
    return found


M3_FORBIDDEN_NAMES = {
    "HumanCommandFacade", "HumanConfirmationGate", "HumanApproval", "HumanAuthorization", "ApprovalRequest",
    "ClassificationService", "DormancyJudgmentService", "AccountStateService", "build_command_facade",
    "Repository", "authorize", "_GATE_KEY", "ConfirmationProposal", "DormancyJudgmentProposal",
    "MoveToDormantProposal", "MoveToActiveProposal", "MoveToNoPlausiblePathProposal",
}


# Stored records returned by the reads (BAEC confirmations, judgment and transition
# authorizations) carry HumanAuthorization values. adapters.py may name the type
# only to serialize those stored values by exact type; it may never call or
# construct it (also enforced across production by the Phase 4 rule R1).
M3_TYPE_ONLY_EXCEPTIONS = {"baec_app.mcp.adapters": {"HumanAuthorization"}}


def m3_no_command_side_names(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    tokens = [(n, line) for n, line in facts.names] + [(a, line) for a, line in facts.attributes]
    tokens += [(name.rsplit(".", 1)[-1], line) for name, line in facts.imports]
    allowed = M3_TYPE_ONLY_EXCEPTIONS.get(module, set())
    found = [f"{module}:{line} references {name}" for name, line in tokens if name in M3_FORBIDDEN_NAMES - allowed]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            target = _call_name(node)
            arguments = [a.id for a in node.args if isinstance(a, ast.Name)]
            if target in allowed or (target == "__new__" and set(arguments) & allowed):
                found.append(f"{module}:{node.lineno} constructs {target}")
    return found


def m4_no_sqlite(module: str, source: str) -> list[str]:
    return imports_package(scan(source, module), "sqlite3")


MODEL_SDKS = ("anthropic", "openai", "claude_agent_sdk", "claude_code_sdk", "litellm", "langchain")


def m5_no_model_sdk(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = []
    for package in MODEL_SDKS:
        found += imports_package(facts, package)
    return found


M6_EXACT = {
    "request_from_proposal", "confirm_baec", "record_dormancy_judgment", "move_to_conditionally_dormant",
    "move_to_active_opportunity", "move_to_no_plausible_path", "approve", "redeem", "register",
}
M6_PREFIXES = ("propose_", "request_", "save_", "persist_", "add_")
M6_ALLOWED = {"preview_move_to_conditionally_dormant", "preview_move_to_active_opportunity", "preview_move_to_no_plausible_path"}


def m6_no_forbidden_calls(module: str, source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name is None or name in M6_ALLOWED:
                continue
            if name in M6_EXACT or name.startswith(M6_PREFIXES):
                found.append(f"{module}:{node.lineno} calls {name}")
    return found


def _registration_decorators(function: ast.AST) -> list[ast.Call]:
    return [
        d for d in getattr(function, "decorator_list", [])
        if isinstance(d, ast.Call) and _call_name(d) in {"resource", "tool", "prompt"}
    ]


def m7_async_handlers_and_no_offloading(module: str, source: str) -> list[str]:
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registration_decorators(node):
            if not isinstance(node, ast.AsyncFunctionDef):
                found.append(f"{module}:{node.lineno} handler {node.name} is not async")
        if isinstance(node, ast.Call) and _call_name(node) in {
            "to_thread", "run_in_executor", "run_sync", "ThreadPoolExecutor", "ProcessPoolExecutor", "Thread",
        }:
            found.append(f"{module}:{node.lineno} offloads with {_call_name(node)}")
    facts = scan(source, module)
    for package in ("concurrent.futures", "threading", "multiprocessing"):
        found += imports_package(facts, package)
    return found


def m8_literal_static_registration(module: str, source: str) -> list[str]:
    found = []
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in _registration_decorators(node):
                if not decorator.args or not (isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str)):
                    found.append(f"{module}:{decorator.lineno} registers a non-literal name")
                ancestor = parents.get(node)
                while ancestor is not None:
                    if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                        found.append(f"{module}:{decorator.lineno} registers inside a loop")
                        break
                    ancestor = parents.get(ancestor)
        if isinstance(node, ast.Call) and _call_name(node) in {"getattr", "setattr", "__import__", "eval", "exec", "import_module"}:
            found.append(f"{module}:{node.lineno} uses dynamic dispatch via {_call_name(node)}")
    return found


def registered(source: str, kind: str) -> list[str]:
    values = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in _registration_decorators(node):
                if _call_name(decorator) == kind and decorator.args and isinstance(decorator.args[0], ast.Constant):
                    values.append(decorator.args[0].value)
    return values


APPROVED_TOOL_NAMES = {
    "preview_baec_classification",
    "preview_move_to_conditionally_dormant",
    "preview_move_to_active_opportunity",
    "preview_move_to_no_plausible_path",
}


def _from_function_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "from_function"]


def tool_names(source: str) -> list[str]:
    """Literal names of tools built with Tool.from_function."""
    names = []
    for call in _from_function_calls(ast.parse(source)):
        for keyword in call.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                names.append(keyword.value.value)
    return names


def m7_tool_functions_are_async(module: str, source: str) -> list[str]:
    """Every function given to Tool.from_function is an async def named directly (no lambda or computed callable)."""
    tree = ast.parse(source)
    async_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    found = []
    for call in _from_function_calls(tree):
        target = call.args[0] if call.args else None
        if not isinstance(target, ast.Name) or target.id not in async_names:
            found.append(f"{module}:{call.lineno} tool function is not a directly named async def")
    return found


def m8_literal_tool_registration(module: str, source: str) -> list[str]:
    """Tool names are literal, and tools are not built inside loops or comprehensions."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for call in _from_function_calls(tree):
        names = [k for k in call.keywords if k.arg == "name"]
        if len(names) != 1 or not (isinstance(names[0].value, ast.Constant) and isinstance(names[0].value.value, str)):
            found.append(f"{module}:{call.lineno} tool name is not a literal")
        ancestor = parents.get(call)
        while ancestor is not None:
            if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                found.append(f"{module}:{call.lineno} builds a tool inside a loop")
                break
            ancestor = parents.get(ancestor)
    return found


RULES = {
    "M1 no data layer": m1_no_data_layer,
    "M2 application package API only": m2_application_package_api_only,
    "M3 no command-side names": m3_no_command_side_names,
    "M4 no sqlite3": m4_no_sqlite,
    "M5 no model SDK": m5_no_model_sdk,
    "M6 no forbidden calls": m6_no_forbidden_calls,
    "M7 async handlers, no offloading": m7_async_handlers_and_no_offloading,
    "M8 literal static registration": m8_literal_static_registration,
    "M7 tool functions are async": m7_tool_functions_are_async,
    "M8 literal tool registration": m8_literal_tool_registration,
}


# --- production checks --------------------------------------------------------------------


def test_the_mcp_package_is_scanned():
    names = {module for module, _ in mcp_modules()}
    assert {"baec_app.mcp", "baec_app.mcp.contracts", "baec_app.mcp.adapters", "baec_app.mcp.resources",
            "baec_app.mcp.server", "baec_app.mcp.composition"} <= names


@pytest.mark.parametrize("rule", RULES.values(), ids=RULES.keys())
def test_production_mcp_code_obeys_the_rule(rule):
    violations = [message for module, source in mcp_modules() for message in rule(module, source)]
    assert violations == []


def test_m9_exactly_the_eight_resources_and_no_tools_or_prompts_are_registered():
    resources, tools, prompts = [], [], []
    for _, source in mcp_modules():
        resources += registered(source, "resource")
        tools += registered(source, "tool")
        prompts += registered(source, "prompt")
    assert sorted(resources) == sorted(APPROVED_RESOURCE_URIS) and len(resources) == 8
    assert tools == [] and prompts == []


def test_m9_exactly_the_four_approved_tools_are_built_and_nothing_else_registers_tools():
    names = []
    for module, source in mcp_modules():
        names += tool_names(source)
        assert registered(source, "tool") == [], module  # no decorator-registered tools
    assert sorted(names) == sorted(APPROVED_TOOL_NAMES) and len(names) == 4
    tools_source = (MCP_ROOT / "tools.py").read_text(encoding="utf-8")
    assert len(_from_function_calls(ast.parse(tools_source))) == 4


def test_every_registered_handler_is_async_and_holds_no_thread_offloading():
    handlers = 0
    for module, source in mcp_modules():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registration_decorators(node):
                handlers += 1
                assert isinstance(node, ast.AsyncFunctionDef), (module, node.name)
    assert handlers == 8


def test_mcp_imports_the_six_read_types_only_from_the_application_package():
    tree = ast.parse((MCP_ROOT / "adapters.py").read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "baec_app.application" for alias in node.names
    }
    assert {"PersistedDormancyJudgment", "SourceInteraction", "TransitionHistoryEntry"} <= imported


# --- scanner self-tests ------------------------------------------------------------------

VIOLATIONS = {
    "M1 direct": ("baec_app.mcp.adapters", "from baec_app.data.records import SourceInteraction", m1_no_data_layer),
    "M1 dynamic": ("baec_app.mcp.adapters", "import importlib\nimportlib.import_module('baec_app.data.repository')", m1_no_data_layer),
    "M2 submodule": ("baec_app.mcp.resources", "from baec_app.application.facades import ReadService", m2_application_package_api_only),
    "M2 relative submodule": ("baec_app.mcp.resources", "from ..application.composition import build_proposal_facade", m2_application_package_api_only),
    "M2 non-public name": ("baec_app.mcp.server", "from baec_app.application import facades", m2_application_package_api_only),
    "M2 import submodule": ("baec_app.mcp.server", "import baec_app.application.approval", m2_application_package_api_only),
    "M3 command facade": ("baec_app.mcp.server", "from baec_app.application import HumanCommandFacade", m3_no_command_side_names),
    "M3 gate attribute": ("baec_app.mcp.resources", "x = runtime.gate.HumanConfirmationGate", m3_no_command_side_names),
    "M3 repository": ("baec_app.mcp.composition", "repo = Repository(conn)", m3_no_command_side_names),
    "M3 proposal type": ("baec_app.mcp.adapters", "p = MoveToActiveProposal(a, e, o)", m3_no_command_side_names),
    "M3 authorize": ("baec_app.mcp.adapters", "authority.authorize(r, a)", m3_no_command_side_names),
    "M3 adapters constructs HumanAuthorization": (
        "baec_app.mcp.adapters",
        "from baec_app.domain.models import HumanAuthorization\nHumanAuthorization('u', t, a, 's')",
        m3_no_command_side_names,
    ),
    "M3 adapters HumanAuthorization.__new__": (
        "baec_app.mcp.adapters",
        "from baec_app.domain.models import HumanAuthorization\nobject.__new__(HumanAuthorization)",
        m3_no_command_side_names,
    ),
    "M3 resources names HumanAuthorization": (
        "baec_app.mcp.resources",
        "from baec_app.domain.models import HumanAuthorization",
        m3_no_command_side_names,
    ),
    "M3 adapters names HumanApproval": ("baec_app.mcp.adapters", "from baec_app.application import HumanApproval", m3_no_command_side_names),
    "M4 sqlite3": ("baec_app.mcp.composition", "import sqlite3", m4_no_sqlite),
    "M5 anthropic": ("baec_app.mcp.server", "import anthropic", m5_no_model_sdk),
    "M5 openai dynamic": ("baec_app.mcp.server", "import importlib\nimportlib.import_module('openai')", m5_no_model_sdk),
    "M6 propose": ("baec_app.mcp.resources", "facade.propose_confirmation(c, captured_at=t)", m6_no_forbidden_calls),
    "M6 request_from_proposal": ("baec_app.mcp.resources", "f.request_from_proposal(s, p)", m6_no_forbidden_calls),
    "M6 move_to command": ("baec_app.mcp.resources", "f.move_to_active_opportunity(approval)", m6_no_forbidden_calls),
    "M6 approve": ("baec_app.mcp.resources", "gate.approve(s, r, displayed_digest=d)", m6_no_forbidden_calls),
    "M6 register": ("baec_app.mcp.resources", "gate.register(s, r)", m6_no_forbidden_calls),
    "M6 save": ("baec_app.mcp.resources", "repo.save_confirmed_baec(r)", m6_no_forbidden_calls),
    "M6 add": ("baec_app.mcp.server", "server.add_tool(fn)", m6_no_forbidden_calls),
    "M7 sync handler": ("baec_app.mcp.resources", "@server.resource('baec://x')\ndef x():\n    return ''", m7_async_handlers_and_no_offloading),
    "M7 to_thread": ("baec_app.mcp.resources", "import asyncio\nawait_ = asyncio.to_thread(f)", m7_async_handlers_and_no_offloading),
    "M7 run_in_executor": ("baec_app.mcp.resources", "loop.run_in_executor(None, f)", m7_async_handlers_and_no_offloading),
    "M7 anyio run_sync": ("baec_app.mcp.resources", "anyio.to_thread.run_sync(f)", m7_async_handlers_and_no_offloading),
    "M7 thread pool import": ("baec_app.mcp.resources", "from concurrent.futures import ThreadPoolExecutor", m7_async_handlers_and_no_offloading),
    "M8 computed name": ("baec_app.mcp.resources", "@server.resource(uri)\nasync def x():\n    return ''", m8_literal_static_registration),
    "M8 registration in loop": (
        "baec_app.mcp.resources",
        "for uri in uris:\n    @server.resource('baec://x')\n    async def x():\n        return ''",
        m8_literal_static_registration,
    ),
    "M8 getattr dispatch": ("baec_app.mcp.resources", "handler = getattr(facade.reads, name)", m8_literal_static_registration),
    "M7 sync tool function": (
        "baec_app.mcp.tools",
        "def preview(x):\n    return x\nTool.from_function(preview, name='preview_x')",
        m7_tool_functions_are_async,
    ),
    "M7 lambda tool function": ("baec_app.mcp.tools", "Tool.from_function(lambda x: x, name='preview_x')", m7_tool_functions_are_async),
    "M7 computed tool function": ("baec_app.mcp.tools", "Tool.from_function(handlers[name], name='preview_x')", m7_tool_functions_are_async),
    "M8 computed tool name": (
        "baec_app.mcp.tools",
        "async def p(x):\n    return x\nTool.from_function(p, name=stored_text)",
        m8_literal_tool_registration,
    ),
    "M8 tool built in a loop": (
        "baec_app.mcp.tools",
        "async def p(x):\n    return x\ntools = [Tool.from_function(p, name='preview_x') for _ in range(2)]",
        m8_literal_tool_registration,
    ),
    "M8 tool name missing": ("baec_app.mcp.tools", "async def p(x):\n    return x\nTool.from_function(p)", m8_literal_tool_registration),
    "M6 propose from a tool": ("baec_app.mcp.tools", "facade.propose_move_to_active_opportunity(a, evaluation_evidence=e)", m6_no_forbidden_calls),
    "M3 proposal type in tools": ("baec_app.mcp.tools", "from baec_app.application import MoveToActiveProposal", m3_no_command_side_names),
}


@pytest.mark.parametrize("case", VIOLATIONS.values(), ids=VIOLATIONS.keys())
def test_rules_report_synthetic_violations(case):
    module, source, rule = case
    assert rule(module, source) != []


ALLOWED = {
    "M2 package API": ("baec_app.mcp.adapters", "from baec_app.application import SourceInteraction, ProposalFacade", m2_application_package_api_only),
    "M3 docstring mentions command facade": ("baec_app.mcp.server", '"""Never holds a HumanCommandFacade or Repository."""', m3_no_command_side_names),
    "M3 adapters serializes stored authorizations by type": (
        "baec_app.mcp.adapters",
        "from baec_app.domain.models import HumanAuthorization\n_require(value, HumanAuthorization)",
        m3_no_command_side_names,
    ),
    "M6 preview_move_to calls": (
        "baec_app.mcp.tools",
        "facade.preview_move_to_conditionally_dormant(a, baec_id=b, judgment_id=1)\n"
        "facade.preview_move_to_active_opportunity(a, evaluation_evidence=e)\n"
        "facade.preview_move_to_no_plausible_path(a, ground=g, reason=r)",
        m6_no_forbidden_calls,
    ),
    "M6 register_resources is not register": ("baec_app.mcp.server", "register_resources(server, facade)", m6_no_forbidden_calls),
    "M6 reads": ("baec_app.mcp.resources", "facade.reads.list_accounts()", m6_no_forbidden_calls),
    "M7 async handler": ("baec_app.mcp.resources", "@server.resource('baec://x')\nasync def x():\n    return ''", m7_async_handlers_and_no_offloading),
    "M8 literal registration": ("baec_app.mcp.resources", "@server.resource('baec://x')\nasync def x():\n    return ''", m8_literal_static_registration),
    "M7 async tool function": (
        "baec_app.mcp.tools",
        "async def preview(x):\n    return x\nTool.from_function(preview, name='preview_x')",
        m7_tool_functions_are_async,
    ),
    "M8 literal tool name": (
        "baec_app.mcp.tools",
        "async def p(x):\n    return x\nt = Tool.from_function(p, name='preview_x')",
        m8_literal_tool_registration,
    ),
}


@pytest.mark.parametrize("case", ALLOWED.values(), ids=ALLOWED.keys())
def test_rules_allow_legitimate_code(case):
    module, source, rule = case
    assert rule(module, source) == []
