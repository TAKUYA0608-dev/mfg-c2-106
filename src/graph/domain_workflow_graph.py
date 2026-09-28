"""MFG-C2-106 — inner domain workflow graph (Cat 2).

Instantiated by EcoImpactWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with per-node skip
guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph boundary):

    START → eco_ingest → bom_impact → impact_evaluate → report_generate → END

On rejected / 0-ingest input, eco_ingest sets ingest_count=0 (+error_code); bom_impact and
impact_evaluate no-op (emitting a count-only S-4 event) and report_generate emits the out-of-scope safe
answer — no fabricated production-impact assessment.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.bom_impact_node import BomImpactNode
from src.nodes.eco_ingest_node import EcoIngestNode
from src.nodes.impact_evaluate_node import ImpactEvaluateNode
from src.nodes.report_generate_node import ReportGenerateNode
from src.schemas.state import State


class EcoImpactWorkflow(BaseGraph):
    """Inner graph: eco_ingest → bom_impact → impact_evaluate → report_generate."""

    @property
    def name(self) -> str:
        return "EcoImpactWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["eco_ingest"] = EcoIngestNode()
        self._nodes["bom_impact"] = BomImpactNode()
        self._nodes["impact_evaluate"] = ImpactEvaluateNode()
        self._nodes["report_generate"] = ReportGenerateNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-ingest / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "eco_ingest")
        self._sg.add_edge("eco_ingest", "bom_impact")
        self._sg.add_edge("bom_impact", "impact_evaluate")
        self._sg.add_edge("impact_evaluate", "report_generate")
        self._sg.add_edge("report_generate", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            return "report_generate"
        return "bom_impact"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "ingest_count": state.get("ingest_count", 0),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
