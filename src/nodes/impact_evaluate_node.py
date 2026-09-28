"""MFG-C2-106 — inner workflow step 3: impact_evaluate.

Deterministic quantitative assessment: for each matched ECO impact, compute the schedule (replenishment
lead-time days) and cost impact of the classified disposition, and assign a risk tier (low / medium /
high) from auditable thresholds. Unmatched ECOs carry no quantitative impact. Skips (no-op) on rejected /
0-ingest input, emitting a count-only S-4 event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, cast

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import BomKB
from src.utils.audit import emit_trace_event


class ImpactEvaluateNode(FunctionNode):
    """Compute schedule/cost impact + risk tier per matched ECO (deterministic)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            emit_trace_event(
                "impact_evaluate.skip", {"reason": state.get("error_code") or "no_eco", "evaluation_count": 0}, state
            )
            return {}

        impacts = json.loads(state.get("eco_impacts") or "[]")
        evaluations: list[dict[str, Any]] = []
        for imp in impacts:
            if not imp.get("matched"):
                evaluations.append(
                    {
                        "eco_id": imp.get("eco_id"),
                        "part_no": imp.get("part_no"),
                        "risk_tier": "unresolved",
                        "cost_impact_jpy": 0.0,
                        "schedule_impact_days": 0,
                        "affected_assemblies": [],
                    }
                )
                continue
            rec = BomKB.lookup(imp["part_no"])
            disp = {"disposition": imp["disposition"], "wip_affected": imp.get("wip_affected", False)}
            assessment = BomKB.assess_impact(cast(dict[str, Any], rec), disp, imp["effectivity"])
            evaluations.append({"eco_id": imp.get("eco_id"), "part_no": imp["part_no"], **assessment})
        emit_trace_event(
            "impact_evaluate.complete",
            {
                "evaluation_count": len(evaluations),
                "high_risk": sum(1 for e in evaluations if e["risk_tier"] == "high"),
            },
            state,
        )
        return {"evaluations": json.dumps(evaluations, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
