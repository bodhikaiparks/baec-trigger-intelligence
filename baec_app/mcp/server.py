"""Transport-independent construction of the MCP Core server."""

from __future__ import annotations

from mcp.server import MCPServer

from baec_app.application import ProposalFacade
from baec_app.mcp.resources import register_resources

SERVER_NAME = "baec-trigger-intelligence"


def build_mcp_server(proposal_facade: ProposalFacade) -> MCPServer:
    """Build the read-only MCP server over exactly one ProposalFacade.

    Accepts only an exact ProposalFacade: not a path, repository, connection,
    command facade, gate, or service. Registers the eight resources, no tools,
    and no prompts.
    """
    if type(proposal_facade) is not ProposalFacade:
        raise TypeError("build_mcp_server requires a ProposalFacade")
    server = MCPServer(name=SERVER_NAME, title="BAEC Trigger Intelligence (read-only MCP Core)", version="phase-5")
    register_resources(server, proposal_facade)
    return server
