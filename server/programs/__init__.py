"""Program layer: which CSI programs this server offers, and what each program's API can do.

CSI_MCP_PROGRAMS (environment, e.g. "ETABS,SAFE"; default all three) limits the server to some programs: only their
actions are listed in the tool description and `help`, and `software` values outside the list are refused. This keeps
the text Claude reads short for a user who works with one or two programs.

Program-specific code lives in sap2000.py, etabs.py and safe.py; everything that works the same in the three programs
stays in the shared modules (csi_bridge, actions, model_read, results, report). CAPABILITIES (from the API matrix in
docs/api, gen_api_matrix.py) say which features exist where; `require` refuses a request with the reason and the
nearest thing the program can do.
"""
from __future__ import annotations

import logging
import os

import edition
from csi_bridge import CSIValidationError

from . import etabs, safe, sap2000

log = logging.getLogger("csi_mcp.programs")

ALL = ("ETABS", "SAP2000", "SAFE")
LETTER = {"E": "ETABS", "S": "SAP2000", "F": "SAFE"}
MODULES = {"ETABS": etabs, "SAP2000": sap2000, "SAFE": safe}
CAPABILITIES: dict[str, dict[str, str]] = {p: m.CAPABILITIES for p, m in MODULES.items()}


def enabled() -> tuple[str, ...]:
    """Programs this server offers: the edition's program, else CSI_MCP_PROGRAMS (development server)."""
    one = edition.current()
    if one:
        return (one,)
    raw = os.environ.get("CSI_MCP_PROGRAMS", "").strip()
    if not raw:
        return ALL
    wanted = {w.strip().upper() for w in raw.replace(";", ",").split(",") if w.strip()}
    unknown = wanted - set(ALL)
    if unknown:
        log.warning("CSI_MCP_PROGRAMS: unknown program(s) %s ignored", sorted(unknown))
    picked = tuple(p for p in ALL if p in wanted)
    return picked


def expand(programs: str) -> tuple[str, ...]:
    """'ESF' / 'S' / 'EF' -> program names."""
    return tuple(LETTER[c] for c in programs)


def check_enabled(software: str) -> None:
    one = edition.current()
    if one and software != one:
        raise CSIValidationError(f"This is the {one} server; {software} has its own server.",
                                 hint=f"Use the {software.lower()} tool for {software} (install its package if it "
                                      "is not listed).")
    on = enabled()
    if software not in on:
        raise CSIValidationError(f"{software} is not enabled in this server (CSI_MCP_PROGRAMS={','.join(on)}).",
                                 hint="Ask the user to add it to CSI_MCP_PROGRAMS in the server configuration.")


def require(software: str, feature: str) -> None:
    """Refuse a feature the program's API does not have, with what to do instead."""
    caps = CAPABILITIES[software]
    if feature in caps and caps[feature]:
        return
    others = [p for p in ALL if CAPABILITIES[p].get(feature)]
    alt = MODULES[software].ALTERNATIVES.get(feature, "")
    raise CSIValidationError(
        f"{software}'s API has no {FEATURES.get(feature, feature)}" + (f" (available in {', '.join(others)})" if others else "") + ".",
        hint=alt or "There is no API route for this in this program.")


FEATURES = {
    "area_auto_mesh": "area auto-mesh assignment (AreaObj.SetAutoMesh)",
    "area_divide": "area divide tool (EditArea.Divide)",
    "joint_patterns": "joint patterns (PatternDef, PointObj.SetPatternByXYZ)",
    "surface_pressure": "area surface pressure load (AreaObj.SetLoadSurfacePressure)",
    "stories": "stories",
    "piers_spandrels": "pier / spandrel labels and forces",
    "bulk_object_readers": "bulk object readers (GetAllPoints / GetAllFrames / GetAllAreas)",
}
