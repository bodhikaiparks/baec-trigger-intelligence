"""Proposal origins.

Phase 4 has exactly two origins. AI_MODEL is deliberately absent: model or
MCP proposal ingestion may not be enabled until persistent AI-origin
provenance is designed and implemented (design §18).
"""

from enum import Enum


class ProposalOrigin(Enum):
    HUMAN_DRAFT = "HUMAN_DRAFT"  # values entered by a human through the UI
    DETERMINISTIC = "DETERMINISTIC"  # produced by deterministic application code
