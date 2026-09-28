"""MFG-C2-106 — inner workflow step 1: eco_ingest.

Deterministic ingest of the validated ECO batch. Sets ``ingest_count``; **0 valid ECOs (rejected input
or no parseable rows) routes to the out-of-scope safe answer** — the agent never fabricates a
production-impact assessment that is not grounded in a supplied ECO + the BOM master data.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event


class EcoIngestNode(FunctionNode):
    """Ingest and count the validated ECO records for the batch."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        slots = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        canonical = json.dumps(slots, ensure_ascii=False)
        ecos = slots.get("ecos") if isinstance(slots, dict) else None
        ecos = ecos if isinstance(ecos, list) else []

        if state.get("error_code") or not ecos:
            emit_trace_event(
                "eco_ingest.skip", {"reason": state.get("error_code") or "no_eco", "ingest_count": 0}, state
            )
            return {
                "validated_input": canonical,
                "ingest_count": 0,
                "error_code": state.get("error_code") or "NO_ECO",
                "status": AgentStatus.SUCCESS.value,
            }

        emit_trace_event("eco_ingest.complete", {"ingest_count": len(ecos)}, state)
        return {"validated_input": canonical, "ingest_count": len(ecos), "status": AgentStatus.SUCCESS.value}
