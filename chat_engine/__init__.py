"""chat_engine — Chat Engine composed on top of general_ai_core (pinned SHA).

Architecture lock: docs/adr/ADR-CE-0001-architecture-lock.md (ACCEPTED).
Rules: additive-only on upstream contracts; never import ``apps.*`` or ``ui/``.
"""

__version__ = "0.1.2"
UPSTREAM_SHA = "0a13556305bcf40cbf1b750d7ef1d81f7850cabf"
