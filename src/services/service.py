"""MFG-C2-106 — deterministic domain services (no framework imports).

``BomKB``: a seeded knowledge base of manufacturing master data — bill-of-materials (where-used)
relationships, on-hand / WIP / open-order inventory positions, standard unit costs, rework feasibility,
and replenishment lead-times — all sourced from internal engineering/production reference master data
(no PII). Retrieval (part where-used), inventory disposition classification, schedule/cost impact, and
risk tiering are **fully deterministic** (exact/keyword where-used lookup + threshold rules, no LLM) and
auditable. No production language model is used anywhere in this template.

The agent is read-only and advisory: it never mutates the source BOM/inventory feed and never
auto-executes a disposition — the final production-impact decision is an authorized human via the
mandatory HumanGate surfaced in the deliverable (owner/approver = candidate/placeholder).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# ── caller-identifier privacy tokenization vs. part-reference grounding — TWO SEPARATE concerns ──────
#   (1) PRIVACY (safe_identifier): the caller-supplied ``eco_id`` is a pure reference key echoed into the
#       deliverable — it is UNCONDITIONALLY tokenized to a deterministic opaque surrogate ``eco:<sha8>`` so a
#       name (even a bare ``Alice`` / ``John.Smith`` / ``TaroYamada``, no spaces/symbols) can never reach a
#       citation or the output. Deterministic → the surrogate stays referentially consistent across the
#       impact / evaluation / citation joins. A value already in the surrogate namespace passes through
#       unchanged (idempotent). Tokenizing is a privacy measure only — it asserts nothing about grounding.
#   (2) GROUNDING (is_part_reference): the ``part_no`` MUST survive verbatim to look up the internal BOM
#       master data (it is business master-data, non-PII). It passes through ONLY on a full match of a
#       strict manufacturing part/assembly reference form (a known prefix a name can never satisfy);
#       anything else (a name, phone, free text) is NOT a part reference and is masked upstream, so it can
#       never leak into a citation / assessment.
# ★ NO forgeable caller provenance here: the grounding CITATION is the internal KB record's own ``source``
#   (see BomKB below), never a caller-supplied value — so there is nothing for a caller to forge and no
#   ``resolve_provenance`` step is needed (contrast a sibling template, where the caller supplies ``source``).
_PART_REF = re.compile(r"^(?:PN|MPN|ASSY|SKU|DWG|PART|ITEM)-[A-Z0-9][A-Z0-9\-]{0,30}$")


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def safe_identifier(value: Any) -> str:
    """PRIVACY-tokenize a caller ECO identifier to a deterministic opaque surrogate ``eco:<sha8>``.

    Always tokenized — there is no syntactic passthrough — so a name (with or without spaces/symbols) can
    never survive into a citation or the output, and a caller value merely *shaped* like a surrogate
    (``eco:deadbeef``) is re-hashed rather than trusted (it can never forge an internal join key). Same input
    → same surrogate (referential integrity across the impact / evaluation / citation joins). Tokenization
    happens once at S-1 (pre_process). Privacy only; asserts nothing about grounding.
    """
    return "eco:" + _sha8(str(value or "").strip())


def is_part_reference(value: Any) -> bool:
    """True only for a strict manufacturing part/assembly master-data reference.

    A bare person name (``Alice`` / ``John.Smith`` / ``TaroYamada``) can never match — the reference must
    lead with a known master-data prefix (``PN-`` / ``ASSY-`` / ...) followed by an uppercase-alphanumeric
    body. A non-conforming value is not a part reference and is masked upstream so it can never leak into a
    citation / assessment (and never resolves to a grounded KB record).
    """
    return bool(_PART_REF.match(str(value or "").strip()))


# Recognized engineering-change classes and their canonical form.
_CHANGE_TYPES = {
    "form_fit_function": "form_fit_function",
    "fff": "form_fit_function",
    "form-fit-function": "form_fit_function",
    "material_substitution": "material_substitution",
    "material": "material_substitution",
    "substitution": "material_substitution",
    "documentation_only": "documentation_only",
    "doc": "documentation_only",
    "documentation": "documentation_only",
    "process_change": "process_change",
    "process": "process_change",
}

# Effectivity classes: when the change takes effect relative to current builds.
_EFFECTIVITY = {
    "immediate": "immediate",
    "now": "immediate",
    "next_build": "next_build",
    "next": "next_build",
    "phase_in": "phase_in",
    "phasein": "phase_in",
}

# ── seeded BOM / inventory master data (internal reference; no PII) ───────────
# Each record: {part_no, description, where_used[], on_hand_qty, wip_qty, open_order_qty,
#               unit_cost, rework_feasible, rework_unit_cost, replenishment_lead_days, tags[], source}
KB: list[dict[str, Any]] = [
    {
        "part_no": "PN-1001",
        "description": "ブラケット (溶接構造)",
        "where_used": ["ASSY-A100", "ASSY-B200"],
        "on_hand_qty": 480,
        "wip_qty": 120,
        "open_order_qty": 600,
        "unit_cost": 1250.0,
        "rework_feasible": True,
        "rework_unit_cost": 420.0,
        "replenishment_lead_days": 21,
        "tags": ["bracket", "ブラケット", "溶接", "weld", "sheet_metal"],
        "source": "MFG 部品マスタ / BOM where-used (PN-1001)",
    },
    {
        "part_no": "PN-2033",
        "description": "電源基板 (PCBA)",
        "where_used": ["ASSY-A100", "ASSY-C300", "ASSY-D400"],
        "on_hand_qty": 90,
        "wip_qty": 40,
        "open_order_qty": 200,
        "unit_cost": 8600.0,
        "rework_feasible": False,
        "rework_unit_cost": 0.0,
        "replenishment_lead_days": 56,
        "tags": ["pcba", "基板", "電源", "board", "electronics", "smt"],
        "source": "MFG 部品マスタ / BOM where-used (PN-2033)",
    },
    {
        "part_no": "PN-3050",
        "description": "ハーネス (ワイヤ)",
        "where_used": ["ASSY-B200"],
        "on_hand_qty": 1500,
        "wip_qty": 300,
        "open_order_qty": 2000,
        "unit_cost": 320.0,
        "rework_feasible": True,
        "rework_unit_cost": 95.0,
        "replenishment_lead_days": 14,
        "tags": ["harness", "ハーネス", "wire", "配線", "cable"],
        "source": "MFG 部品マスタ / BOM where-used (PN-3050)",
    },
    {
        "part_no": "PN-4088",
        "description": "筐体カバー (樹脂成形)",
        "where_used": ["ASSY-C300"],
        "on_hand_qty": 220,
        "wip_qty": 60,
        "open_order_qty": 300,
        "unit_cost": 540.0,
        "rework_feasible": False,
        "rework_unit_cost": 0.0,
        "replenishment_lead_days": 35,
        "tags": ["cover", "カバー", "筐体", "molding", "樹脂", "plastic"],
        "source": "MFG 部品マスタ / BOM where-used (PN-4088)",
    },
    {
        "part_no": "PN-5120",
        "description": "ラベル (銘板)",
        "where_used": ["ASSY-A100", "ASSY-B200", "ASSY-C300", "ASSY-D400"],
        "on_hand_qty": 5000,
        "wip_qty": 0,
        "open_order_qty": 8000,
        "unit_cost": 25.0,
        "rework_feasible": True,
        "rework_unit_cost": 8.0,
        "replenishment_lead_days": 7,
        "tags": ["label", "ラベル", "銘板", "nameplate", "marking"],
        "source": "MFG 部品マスタ / BOM where-used (PN-5120)",
    },
    # Newly-registered part whose BOM master-data source reference is not yet established
    # (source=None). A change against it is grounded (the part exists) but uncited → the deliverable is
    # withheld fail-closed by post_process rather than shipped with a fabricated/absent citation.
    {
        "part_no": "PN-6000",
        "description": "新規部品 (BOM参照整備中)",
        "where_used": ["ASSY-D400"],
        "on_hand_qty": 40,
        "wip_qty": 10,
        "open_order_qty": 100,
        "unit_cost": 1500.0,
        "rework_feasible": False,
        "rework_unit_cost": 0.0,
        "replenishment_lead_days": 28,
        "tags": ["pending_ref_part_pn6000"],
        "source": None,
    },
]

_BY_PART = {rec["part_no"]: rec for rec in KB}

# Risk-tier thresholds (deterministic, auditable).
_COST_HIGH = 500_000.0
_COST_MEDIUM = 100_000.0
_LEAD_HIGH_DAYS = 30
_LEAD_MEDIUM_DAYS = 14
_ASSY_HIGH = 3


class BomKB:
    """Deterministic where-used retrieval + disposition / impact assessment over the seeded BOM KB."""

    # ── normalization helpers ────────────────────────────────────────────────
    @staticmethod
    def normalize_change_type(text: str | None) -> str:
        low = (text or "").strip().lower()
        return _CHANGE_TYPES.get(low, "process_change" if low else "form_fit_function")

    @staticmethod
    def normalize_effectivity(text: str | None) -> str:
        low = (text or "").strip().lower()
        return _EFFECTIVITY.get(low, "next_build")

    # ── retrieval ────────────────────────────────────────────────────────────
    @staticmethod
    def lookup(part_no: str | None, hint: str | None = None) -> dict[str, Any] | None:
        """Resolve a part record by exact part number, else by a keyword/tag hint. None = out-of-scope."""
        if part_no and part_no in _BY_PART:
            return _BY_PART[part_no]
        probe = f"{part_no or ''} {hint or ''}".lower()
        best: tuple[int, dict[str, Any]] | None = None
        for rec in KB:
            score = sum(2 for t in rec["tags"] if t.lower() in probe)
            if score and (best is None or score > best[0]):
                best = (score, rec)
        return best[1] if best else None

    # ── disposition classification (deterministic rules) ─────────────────────
    @staticmethod
    def classify_disposition(rec: dict[str, Any], change_type: str, effectivity: str) -> dict[str, Any]:
        """Classify the required inventory/WIP disposition for the affected part.

        Deterministic rule table (no LLM). Returns disposition + rationale (advisory only — the
        binding call is the HumanGate downstream).
        """
        ct = change_type
        if ct == "documentation_only":
            disposition = "use_as_is"
            rationale = "文書のみの変更 (form/fit/function に影響なし) → 現行在庫・仕掛は use-as-is。"
        elif ct == "form_fit_function":
            if rec["rework_feasible"]:
                disposition = "rework"
                rationale = "form/fit/function 変更で互換性なし。当該部品は rework 可 → 現行在庫・仕掛を rework。"
            else:
                disposition = "scrap"
                rationale = "form/fit/function 変更で互換性なし。rework 不可 → 現行在庫・仕掛を scrap。"
        elif ct == "material_substitution":
            if rec["rework_feasible"]:
                disposition = "rework"
                rationale = "材料置換。rework で置換可 → 現行在庫・仕掛を rework。"
            else:
                disposition = "use_as_is_with_deviation"
                rationale = "材料置換だが rework 不可 → 逸脱 (deviation) 承認前提で use-as-is 候補 (要人手判断)。"
        else:  # process_change
            disposition = "use_as_is"
            rationale = "工程変更 (部品形状は不変) → 現行在庫・仕掛は use-as-is、次ロットから新工程。"

        # Effectivity modulates whether WIP is caught by the change.
        wip_affected = effectivity == "immediate" and disposition in ("rework", "scrap")
        return {"disposition": disposition, "rationale": rationale, "wip_affected": wip_affected}

    # ── quantitative impact (deterministic) ──────────────────────────────────
    @staticmethod
    def assess_impact(rec: dict[str, Any], disposition: dict[str, Any], effectivity: str) -> dict[str, Any]:
        """Compute schedule (lead-time days) + cost impact + risk tier deterministically."""
        on_hand = rec["on_hand_qty"]
        wip = rec["wip_qty"] if disposition["wip_affected"] else 0
        affected_qty = on_hand + wip
        d = disposition["disposition"]

        if d == "scrap":
            cost_impact = round(on_hand * rec["unit_cost"] + wip * rec["unit_cost"] * 0.5, 2)
            schedule_impact_days = rec["replenishment_lead_days"] if affected_qty > 0 else 0
        elif d == "rework":
            cost_impact = round(affected_qty * rec["rework_unit_cost"], 2)
            schedule_impact_days = min(rec["replenishment_lead_days"], 14) if affected_qty > 0 else 0
        else:  # use_as_is / use_as_is_with_deviation
            cost_impact = 0.0
            schedule_impact_days = 0

        assemblies = len(rec["where_used"])
        risk_tier = BomKB._risk_tier(cost_impact, schedule_impact_days, assemblies)
        return {
            "affected_on_hand_qty": on_hand,
            "affected_wip_qty": wip,
            "affected_assemblies": rec["where_used"],
            "cost_impact_jpy": cost_impact,
            "schedule_impact_days": schedule_impact_days,
            "risk_tier": risk_tier,
        }

    @staticmethod
    def _risk_tier(cost_impact: float, schedule_days: int, assemblies: int) -> str:
        if cost_impact >= _COST_HIGH or schedule_days >= _LEAD_HIGH_DAYS or assemblies >= _ASSY_HIGH:
            return "high"
        if cost_impact >= _COST_MEDIUM or schedule_days >= _LEAD_MEDIUM_DAYS or assemblies >= 2:
            return "medium"
        return "low"
