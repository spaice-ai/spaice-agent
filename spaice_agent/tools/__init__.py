"""Tool descriptors — callable-building factories for Hermes tools.

Each ``build_*_tool(agent_id)`` returns a tool descriptor dict ready for
``registry.register()``. Moving these out of the hook means:

  - They're pytest-able (run against a fake registry)
  - They're versioned with spaice_agent
  - Bugs fix once, ship everywhere
"""
from .use_consensus import build_use_consensus_tool

__all__ = ["build_use_consensus_tool"]
