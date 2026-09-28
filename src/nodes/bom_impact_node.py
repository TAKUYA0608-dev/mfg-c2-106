"""MFG-C2-106 — inner workflow step 2: bom_impact.

Deterministic BOM where-used retrieval + inventory disposition classification. For each ECO, resolves the
affected part against the BOM master data (where-used, on-hand / WIP), classifies the required disposition
(use-as-is / rework / scrap / use-as-is-with-deviation) via the deterministic rule table, and cites the
master-data source. Skips (no-op) on rejected / 0-ingest input, emitting a count-only S-4 event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import BomKB
from src.utils.audit import emit_trace_event


class BomImpactNode(FunctionNode):
    """Resolve affected parts + classify inventory/WIP disposition per ECO (deterministic)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            emit_trace_event(
                "bom_impact.skip", {"reason": state.get("error_code") or "no_eco", "impact_count": 0}, state
            )
            return {}

        slots = json.loads(state.get("validated_input") or "{}")
        impacts: list[dict[str, Any]] = []
        matched = 0
        for eco in slots.get("ecos", []):
            rec = BomKB.lookup(eco.get("part_no"), eco.get("notes"))
            change_type = BomKB.normalize_change_type(eco.get("change_type"))
            effectivity = BomKB.normalize_effectivity(eco.get("effectivity"))
            if rec is None:
                impacts.append(
                    {
                        "eco_id": eco.get("eco_id"),
                        "part_no": eco.get("part_no"),
                        "matched": False,
                        "change_type": change_type,
                        "effectivity": effectivity,
                        "disposition": "unresolved",
                        "rationale": "BOM マスタに該当部品が見つかりません (out-of-scope / 要データ確認)。",
                        "citation": None,
                    }
                )
                continue
            matched += 1
            disp = BomKB.classify_disposition(rec, change_type, effectivity)
            # Citation = the validated BOM master-data source only (never fabricated). A record whose
            # master-data reference is not yet established carries source=None → a matched-but-uncited
            # assessment, which post_process withholds fail-closed (S-3 citation-completeness).
            impacts.append(
                {
                    "eco_id": eco.get("eco_id"),
                    "part_no": rec["part_no"],
                    "part_description": rec["description"],
                    "matched": True,
                    "change_type": change_type,
                    "effectivity": effectivity,
                    "disposition": disp["disposition"],
                    "rationale": disp["rationale"],
                    "wip_affected": disp["wip_affected"],
                    "where_used": rec["where_used"],
                    "citation": rec.get("source"),
                }
            )
        emit_trace_event("bom_impact.complete", {"impact_count": len(impacts), "matched": matched}, state)
        return {"eco_impacts": json.dumps(impacts, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
