"""Phase 8.3 (0.21.0): reinforcement on concrete frame sections - beams (covers and the longitudinal areas at the ends)
and columns (pattern, confinement, cover, bar counts and sizes, ties, check or design) - through PropFrame.SetRebarBeam
/ SetRebarColumn (typed in ETABS 23.2, SAP2000 27.1 and SAFE 23.3), read back with GetRebarBeam / GetRebarColumn.

The values are the designer's (D1). Column reinforcement 'to be checked' is what the program's concrete frame design
uses to give a capacity ratio instead of a required area; beam areas are used by the programs for cracked properties
and ductility checks, not as the flexural design.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import Params, _dry, action, bridge
from csi_bridge import CSIValidationError, api_call, describe_units
from model_read import _try
from modelling import _start_edit
from objects import _EditP

PATTERN = {"rectangular": 1, "circular": 2}
CONFINE = {"ties": 1, "spiral": 2}
CONFINE_READ = {0: "ties", 1: "ties", 2: "spiral"}   # measured: a rectangular tied column reads back 0 (E S F)


def read_rebar(sap: Any, name: str) -> dict[str, Any] | None:
    """The section's reinforcement as the program holds it (None when it has none / is not a concrete section)."""
    t = _try(sap, "PropFrame.GetTypeRebar", name, 0)
    kind = int(t[0]) if t else -1     # GetTypeRebar: 1 = column, 2 = beam (CSI API help; confirmed live, 0.21.0)
    if kind in (1, -1):        # 1 = column (measured below in the live test); -1 = getter missing
        c = _try(sap, "PropFrame.GetRebarColumn", name, "", "", 0, 0, 0.0, 0, 0, 0, "", "", 0.0, 0, 0, False)
        if c and kind == 1:
            return {"kind": "column", "rebar_material": str(c[0]), "confinement_material": str(c[1]),
                    "pattern": {1: "rectangular", 2: "circular"}.get(int(c[2]), str(c[2])),
                    "confinement": CONFINE_READ.get(int(c[3]), str(c[3])), "cover": float(c[4]),
                    "bars_circular": int(c[5]), "bars_3_face": int(c[6]), "bars_2_face": int(c[7]),
                    "bar_size": str(c[8]), "tie_size": str(c[9]), "tie_spacing": float(c[10]),
                    "ties_2_dir": int(c[11]), "ties_3_dir": int(c[12]),
                    "mode": "design" if bool(c[13]) else "check"}
    if kind in (2, -1):
        b = _try(sap, "PropFrame.GetRebarBeam", name, "", "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        if b and kind == 2:
            return {"kind": "beam", "rebar_material": str(b[0]), "confinement_material": str(b[1]),
                    "cover_top": float(b[2]), "cover_bottom": float(b[3]), "top_area_i": float(b[4]),
                    "top_area_j": float(b[5]), "bottom_area_i": float(b[6]), "bottom_area_j": float(b[7])}
    return None


class BeamRebar(Params):
    cover_top: float = Field(gt=0, description="top of the section to the centroid of the top bars")
    cover_bottom: float = Field(gt=0, description="bottom of the section to the centroid of the bottom bars")
    top_area_i: float = Field(ge=0, description="total top longitudinal area at the I end")
    top_area_j: float = Field(ge=0, description="total top longitudinal area at the J end")
    bottom_area_i: float = Field(ge=0, description="total bottom longitudinal area at the I end")
    bottom_area_j: float = Field(ge=0, description="total bottom longitudinal area at the J end")


class ColumnRebar(Params):
    pattern: Literal["rectangular", "circular"] = Field(description="bar layout")
    confinement: Literal["ties", "spiral"] = Field(description="confinement type")
    cover: float = Field(gt=0, description="clear cover to the confinement steel (ties / spiral)")
    bars_circular: int = Field(default=0, ge=0, le=500, description="circular pattern: number of bars")
    bars_3_face: int = Field(default=0, ge=0, le=500, description=(
        "rectangular: bars along each face parallel to the local 3 axis, corners included"))
    bars_2_face: int = Field(default=0, ge=0, le=500, description=(
        "rectangular: bars along each face parallel to the local 2 axis, corners included"))
    bar_size: str = Field(description="longitudinal bar size, as the program names it (e.g. 25d, #8)")
    tie_size: str = Field(description="tie / spiral bar size, as the program names it")
    tie_spacing: float = Field(gt=0, description="tie / spiral spacing along the column")
    ties_2_dir: int = Field(default=0, ge=0, le=100, description="rectangular: tie legs in the local 2 direction")
    ties_3_dir: int = Field(default=0, ge=0, le=100, description="rectangular: tie legs in the local 3 direction")
    mode: Literal["check", "design"] = Field(description=(
        "check: the program's design checks these bars (capacity ratio) | design: it computes the required area"))

    @model_validator(mode="after")
    def _bars(self) -> "ColumnRebar":
        if self.pattern == "circular" and self.bars_circular < 1:
            raise ValueError("circular pattern needs bars_circular")
        if self.pattern == "rectangular" and (self.bars_3_face < 2 or self.bars_2_face < 2):
            raise ValueError("rectangular pattern needs bars_3_face and bars_2_face (2 or more: corners included)")
        return self


class RebarP(_EditP):
    section: str = Field(description="an existing concrete frame section")
    rebar_material: str = Field(description="longitudinal rebar material (define_material type rebar)")
    confinement_material: str = Field(description="tie / spiral rebar material")
    beam: BeamRebar | None = Field(default=None, description="beam reinforcement (give beam or column)")
    column: ColumnRebar | None = Field(default=None, description="column reinforcement (give beam or column)")

    @model_validator(mode="after")
    def _one(self) -> "RebarP":
        if (self.beam is None) == (self.column is None):
            raise ValueError("give either beam or column")
        return self


@action("set_frame_rebar", "Reinforcement on a concrete frame section, from the designer: beam (covers to the bar "
        "centroids, top / bottom longitudinal areas at the I and J ends) or column (rectangular / circular pattern, "
        "ties / spiral, clear cover, bar counts and size, tie size, spacing and legs, check or design). Answers with "
        "the reinforcement read back.", "Definitions", "edit", RebarP)
def set_frame_rebar(p: RebarP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    secs = bridge.try_name_list(sap, ["PropFrame"])[0] or []
    if p.section not in secs:
        raise CSIValidationError(f"Frame section '{p.section}' is not defined.", hint=f"Defined: {secs[:30]}")
    mats = bridge.try_name_list(sap, ["PropMaterial"])[0] or []
    for m in {p.rebar_material, p.confinement_material}:
        if m not in mats:
            raise CSIValidationError(f"Material '{m}' is not defined.", hint="define_material type rebar")
    m = _try(sap, "PropFrame.GetMaterial", p.section)
    mt = _try(sap, "PropMaterial.GetMaterial", str(m[0])) if m else None
    if mt and int(mt[0]) != 2:
        raise CSIValidationError(f"Section '{p.section}' is not concrete (material '{m[0]}').")
    if p.column:
        sizes = bridge.try_name_list(sap, ["PropRebar"])[0] or []
        unknown = [s for s in {p.column.bar_size, p.column.tie_size} if sizes and s not in sizes]
        if unknown:
            raise CSIValidationError(f"Bar sizes not defined in the model: {unknown}.", hint=f"Sizes: {sizes[:40]}")
    plan = {"section": p.section, "rebar_material": p.rebar_material, "confinement_material": p.confinement_material,
            **({"beam": p.beam.model_dump()} if p.beam else {"column": p.column.model_dump()}),
            "units": describe_units(p.units), "before": read_rebar(sap, p.section)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_rebar", "set frame reinforcement")
        if p.beam:
            b = p.beam
            api_call("PropFrame.SetRebarBeam", sap.PropFrame.SetRebarBeam, p.section, p.rebar_material,
                     p.confinement_material, b.cover_top, b.cover_bottom, b.top_area_i, b.top_area_j,
                     b.bottom_area_i, b.bottom_area_j)
        else:
            c = p.column
            api_call("PropFrame.SetRebarColumn", sap.PropFrame.SetRebarColumn, p.section, p.rebar_material,
                     p.confinement_material, PATTERN[c.pattern], CONFINE[c.confinement], c.cover, c.bars_circular,
                     c.bars_3_face, c.bars_2_face, c.bar_size, c.tie_size, c.tie_spacing, c.ties_2_dir, c.ties_3_dir,
                     c.mode == "design")
        back = read_rebar(sap, p.section)
    want = "beam" if p.beam else "column"
    if not back or back.get("kind") != want:
        raise CSIValidationError(f"The program did not keep {want} reinforcement on '{p.section}'.",
                                 hint=f"Read back: {back}")
    given = p.beam.model_dump() if p.beam else {k: v for k, v in p.column.model_dump().items()}
    diff = {k: (v, back.get(k)) for k, v in given.items() if k in back and
            (abs(float(back[k]) - float(v)) > 1e-6 * max(1.0, abs(float(v))) if isinstance(v, (int, float))
             else str(back[k]) != str(v))}
    if p.column and p.column.pattern == "circular":
        diff = {k: v for k, v in diff.items() if k not in ("bars_3_face", "bars_2_face", "ties_2_dir", "ties_3_dir")}
    if p.column and p.column.pattern == "rectangular":
        diff.pop("bars_circular", None)
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out
