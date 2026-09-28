"""MFG-C2-106 — inner workflow step 4: report_generate.

Composes the advisory production-impact deliverable: per-ECO affected assemblies, inventory/WIP
disposition (with rationale + master-data citation), schedule and cost impact, risk tier, and a
**mandatory HumanGate** (``human_review_required=True`` + approver role placeholder) — the assessment is
advisory and the binding disposition decision is an authorized human. Grounded in the BOM master data with
a source citation per matched ECO. On the 0-ingest / rejected branch it emits the out-of-scope safe answer
(no fabricated assessment).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "ご依頼の設計変更指示 (ECO) に対応する部品が BOM マスタに見つからないか、有効な ECO 明細が"
    "含まれていませんでした。ECO 明細 (part_no / change_type / effectivity) を指定いただくか、対象部品が"
    "登録済みかをデータ管理担当にご確認ください。"
)
_APPROVER_ROLE = "生産技術 / QA 部門 承認者 (owner/approver = candidate placeholder)"


class ReportGenerateNode(FunctionNode):
    """Compose the ECO production-impact deliverable with HumanGate + citations (or safe answer on 0-hit)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        impacts = json.loads(state.get("eco_impacts") or "[]")
        if (
            state.get("error_code")
            or state.get("ingest_count", 0) == 0
            or not impacts
            or not any(i.get("matched") for i in impacts)
        ):
            emit_trace_event("report_generate.safe", {"reason": state.get("error_code") or "no_eco"}, state)
            report = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "assessments": [],
                "citations": [],
                "human_review_required": True,
                "approver_role": _APPROVER_ROLE,
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        eval_by_id = {e["eco_id"]: e for e in json.loads(state.get("evaluations") or "[]")}
        assessments: list[dict[str, Any]] = []
        citations: list[dict[str, str]] = []
        risk_rollup = {"high": 0, "medium": 0, "low": 0, "unresolved": 0}
        total_cost = 0.0
        max_schedule = 0
        for imp in impacts:
            ev = eval_by_id.get(imp.get("eco_id"), {})
            tier = ev.get("risk_tier", "unresolved")
            risk_rollup[tier] = risk_rollup.get(tier, 0) + 1
            total_cost += float(ev.get("cost_impact_jpy", 0.0))
            max_schedule = max(max_schedule, int(ev.get("schedule_impact_days", 0)))
            assessment = {
                "eco_id": imp.get("eco_id"),
                "part_no": imp.get("part_no"),
                "part_description": imp.get("part_description"),
                "matched": imp.get("matched", False),
                "change_type": imp.get("change_type"),
                "effectivity": imp.get("effectivity"),
                "disposition": imp.get("disposition"),
                "rationale": imp.get("rationale"),
                "affected_assemblies": ev.get("affected_assemblies", imp.get("where_used", [])),
                "affected_on_hand_qty": ev.get("affected_on_hand_qty"),
                "affected_wip_qty": ev.get("affected_wip_qty"),
                "cost_impact_jpy": ev.get("cost_impact_jpy"),
                "schedule_impact_days": ev.get("schedule_impact_days"),
                "risk_tier": tier,
                "citation": imp.get("citation"),
            }
            assessments.append(assessment)
            if imp.get("matched") and imp.get("citation"):
                citations.append({"eco_id": imp.get("eco_id"), "source": imp["citation"]})
        report = {
            "status_kind": "report",
            "assessments": assessments,
            "summary": {
                "eco_count": len(assessments),
                "risk_distribution": risk_rollup,
                "total_cost_impact_jpy": round(total_cost, 2),
                "max_schedule_impact_days": max_schedule,
            },
            "citations": citations,
            "human_review_required": True,
            "approver_role": _APPROVER_ROLE,
        }
        emit_trace_event(
            "report_generate.complete",
            {"eco_count": len(assessments), "citation_count": len(citations), "high_risk": risk_rollup.get("high", 0)},
            state,
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "eco_impacts": state.get("eco_impacts"),
            "status": AgentStatus.SUCCESS.value,
        }
