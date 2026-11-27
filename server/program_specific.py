"""Phase 5 (first batch): program-specific modelling - ETABS stories, diaphragms, pier / spandrel labels; automatic
seismic load patterns (ASCE 7-16) with the designer's parameters; link properties and link objects.

Every value (story heights, R, Ss, S1, link stiffness ...) comes from the user; nothing is defaulted silently - the
parameters without a code-neutral default are required. Answers read the definitions back from the program.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import Params, _dry, _refresh_view, action, bridge
from csi_bridge import CSIError, CSIValidationError, api_call, as_list, describe_units
from model_read import _names, _try
from modelling import _start_edit, _targets
from objects import MAX, _EditP

# --------------------------------------------------------------------------- stories (ETABS)


class StoryIn(Params):
    name: str = Field(min_length=1, description="story name")
    height: float = Field(gt=0, description="story height (length units)")
    master: bool = Field(default=True, description="master story (default true)")
    similar_to: str = Field(default="", description="a master story this story is similar to")
    splice_above: bool = Field(default=False, description="column splice above the story")
    splice_height: float = Field(default=0.0, ge=0, description="splice height above the story")


class StoriesP(_EditP):
    base_elevation: float = Field(default=0.0, description="elevation of the base (length units)")
    stories: list[StoryIn] = Field(min_length=1, max_length=500, description="from the bottom story up")


def read_stories(sap: Any) -> dict[str, Any]:
    o = api_call("Story.GetStories_2", sap.Story.GetStories_2)
    names = [str(x).strip() for x in as_list(o[2])]
    return {"base_elevation": float(o[0]), "stories": [
        {"name": n, "elevation": round(float(e), 6), "height": round(float(h), 6), "master": bool(m),
         "similar_to": str(s).strip()} for n, e, h, m, s in zip(names, as_list(o[3]), as_list(o[4]), as_list(o[5]),
                                                                as_list(o[6]))]}


@action("define_stories", "Set the story table: base elevation and each story's name, height, master / similar-to "
        "and splice (bottom story first), as the user gives them. Answers with the stories read back.",
        "Model", "edit", StoriesP, programs="E")
def define_stories(p: StoriesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    names = [s.name for s in p.stories]
    if len(set(n.lower() for n in names)) != len(names):
        raise CSIValidationError("Story names must be unique.")
    masters = {s.name for s in p.stories if s.master}
    bad = [s.similar_to for s in p.stories if s.similar_to and s.similar_to not in masters]
    if bad:
        raise CSIValidationError(f"similar_to must name a master story in the list: {bad}")
    with bridge.units_scope(sap, p.units):
        before = read_stories(sap)
        plan = {"before": before, "base_elevation": p.base_elevation, "stories": [s.model_dump() for s in p.stories],
                "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_stories", "change the stories")
        n = len(p.stories)
        api_call("Story.SetStories_2", sap.Story.SetStories_2, p.base_elevation, n, names,
                 [s.height for s in p.stories], [s.master for s in p.stories],
                 [s.similar_to or "None" for s in p.stories], [s.splice_above for s in p.stories],
                 [s.splice_height for s in p.stories], [0] * n)
        after = read_stories(sap)
    got = [(s["name"], s["height"]) for s in after["stories"]]
    want = [(s.name, round(s.height, 6)) for s in p.stories]
    if [g[0] for g in got] != [w[0] for w in want] or any(abs(g[1] - w[1]) > 1e-6 for g, w in zip(got, want)):
        raise CSIValidationError(f"The program reports stories {got}, not {want}.")
    _refresh_view(sap)
    return {**{k: v for k, v in plan.items() if k != "before"}, "stories_before": before, "read_back": after,
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- diaphragms

class DiaphragmP(_EditP):
    name: str = Field(min_length=1, description="diaphragm name")
    semi_rigid: bool = Field(default=False, description="false: rigid (default) | true: semi-rigid")
    points: list[str] = Field(default=[], max_length=MAX, description="points to assign it to")
    areas: list[str] = Field(default=[], max_length=MAX, description="areas (floors) to assign it to")
    replace: bool = Field(default=False, description="change an existing diaphragm's type")


def _sap_diaphragm(p: DiaphragmP) -> dict[str, Any]:
    """SAP2000: a diaphragm is a constraint (ConstraintDef.SetDiaphragm about Z) given to joints; areas are taken as
    their corner joints. Rigid only."""
    sap = bridge.sap_model(p.software)
    if p.semi_rigid:
        raise CSIValidationError("SAP2000 diaphragm constraints are rigid only.",
                                 hint="Leave semi_rigid false, or model the floor's in-plane stiffness with shells.")
    have = bridge.try_name_list(sap, ["ConstraintDef"])[0] or []
    exists = p.name in have
    if exists and not p.replace:
        raise CSIValidationError(f"Constraint '{p.name}' exists.", hint="replace: true reassigns it.")
    pts = _targets(sap, "PointObj", p.points, None, "points") if p.points else []
    ars = _targets(sap, "AreaObj", p.areas, None, "areas") if p.areas else []
    joints = list(dict.fromkeys(pts + [str(j) for a in ars
                                        for j in api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, a)[1]]))
    if not joints:
        raise CSIValidationError("Give points and / or areas to assign the diaphragm to.")
    plan = {"name": p.name, "semi_rigid": False, "existed": exists, "points": len(pts), "areas": len(ars),
            "joints": len(joints), "note": "SAP2000: diaphragm constraint about global Z on the joints (areas: their "
            "corner joints)."}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_diaphragm", "define a diaphragm")
    api_call("ConstraintDef.SetDiaphragm", sap.ConstraintDef.SetDiaphragm, p.name, 3, "Global")
    for j in joints:
        api_call(f"PointObj.SetConstraint('{j}')", sap.PointObj.SetConstraint, j, p.name, 0, True)
    ax = api_call("ConstraintDef.GetDiaphragm", sap.ConstraintDef.GetDiaphragm, p.name, 0, "")
    got = {}
    for j in joints[:300]:
        o = _try(sap, "PointObj.GetConstraint", j, 0, [], [], 0) or [0, [], []]
        got[j] = [str(c) for c in (o[2] if len(o) > 2 else [])]
    wrong = {j: c for j, c in got.items() if p.name not in c}
    if wrong:
        raise CSIValidationError(f"Joints read back without the diaphragm: {dict(list(wrong.items())[:20])}")
    return {**plan, "read_back": {"axis": {1: "X", 2: "Y", 3: "Z"}.get(int(ax[0]), ax[0]),
                                  "joints_with_diaphragm": len(got)},
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


@action("define_diaphragm", "Create (or with replace change) a rigid or semi-rigid diaphragm and assign it to points "
        "and / or floor areas[[S: (SAP2000: a rigid diaphragm constraint about Z on the joints; areas give their "
        "corner joints)]]. Answers with the diaphragm and assignments read back.", "Definitions", "edit",
        DiaphragmP, programs="ESF")
def define_diaphragm(p: DiaphragmP) -> dict[str, Any]:
    if p.software == "SAP2000":
        return _sap_diaphragm(p)
    sap = bridge.sap_model(p.software)
    have = bridge.try_name_list(sap, ["Diaphragm"])[0] or []
    exists = p.name in have
    if exists and not p.replace:
        raise CSIValidationError(f"Diaphragm '{p.name}' exists.", hint="replace: true changes its type.")
    twin = next((d for d in have if d.lower() == p.name.lower() and d != p.name), None)
    if twin:
        raise CSIValidationError(f"Diaphragm '{twin}' exists (names differ only in case).")
    pts = _targets(sap, "PointObj", p.points, None, "points") if p.points else []
    ars = _targets(sap, "AreaObj", p.areas, None, "areas") if p.areas else []
    plan = {"name": p.name, "semi_rigid": p.semi_rigid, "existed": exists, "points": len(pts), "areas": len(ars)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_diaphragm", "define a diaphragm")
    api_call("Diaphragm.SetDiaphragm", sap.Diaphragm.SetDiaphragm, p.name, p.semi_rigid)
    for n in pts:
        api_call(f"PointObj.SetDiaphragm('{n}')", sap.PointObj.SetDiaphragm, n, 3, p.name)
    for n in ars:
        api_call(f"AreaObj.SetDiaphragm('{n}')", sap.AreaObj.SetDiaphragm, n, p.name)
    back = api_call("Diaphragm.GetDiaphragm", sap.Diaphragm.GetDiaphragm, p.name)
    if bool(back[0]) != p.semi_rigid:
        raise CSIValidationError(f"Diaphragm '{p.name}' reads back as {'semi-rigid' if back[0] else 'rigid'}.")
    pa = {n: str((_try(sap, "PointObj.GetDiaphragm", n) or ["", ""])[1]).strip() for n in pts[:200]}
    aa = {n: str((_try(sap, "AreaObj.GetDiaphragm", n) or [""])[0]).strip() for n in ars[:200]}
    wrong = {n: v for n, v in {**pa, **aa}.items() if v != p.name}
    if wrong:
        raise CSIValidationError(f"Objects read back with another diaphragm: {wrong}")
    return {**plan, "read_back": {"semi_rigid": bool(back[0]), "points": pa, "areas": aa},
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- piers and spandrels (ETABS)

class PierSpandrelP(_EditP):
    kind: Literal["pier", "spandrel"] = Field(description="pier | spandrel")
    label: str = Field(min_length=1, description="the label (made if it does not exist)")
    areas: list[str] = Field(default=[], max_length=MAX, description="wall / spandrel areas")
    frames: list[str] = Field(default=[], max_length=MAX, description="frames")
    multi_story: bool = Field(default=False, description="spandrel only: multi-story")

    @model_validator(mode="after")
    def _some(self) -> "PierSpandrelP":
        if not (self.areas or self.frames):
            raise ValueError("give areas and / or frames")
        return self


@action("assign_pier_spandrel", "Assign a pier or spandrel label (made if missing) to wall areas and / or frames, so "
        "ETABS reports pier / spandrel forces and designs them. Answers with the labels read back.", "Objects",
        "edit", PierSpandrelP, programs="E")
def assign_pier_spandrel(p: PierSpandrelP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    coll = "PierLabel" if p.kind == "pier" else "SpandrelLabel"
    have = bridge.try_name_list(sap, [coll])[0] or []
    ars = _targets(sap, "AreaObj", p.areas, None, "areas") if p.areas else []
    frs = _targets(sap, "FrameObj", p.frames, None, "frames") if p.frames else []
    plan = {"kind": p.kind, "label": p.label, "label_existed": p.label in have, "areas": len(ars), "frames": len(frs)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, f"before_{p.kind}", f"assign a {p.kind}")
    if p.label not in have:
        if p.kind == "pier":
            api_call("PierLabel.SetPier", sap.PierLabel.SetPier, p.label)
        else:
            api_call("SpandrelLabel.SetSpandrel", sap.SpandrelLabel.SetSpandrel, p.label, p.multi_story)
    meth = "SetPier" if p.kind == "pier" else "SetSpandrel"
    get = "GetPier" if p.kind == "pier" else "GetSpandrel"
    back = {}
    for coll_o, names in (("AreaObj", ars), ("FrameObj", frs)):
        obj = getattr(sap, coll_o)
        for n in names:
            api_call(f"{coll_o}.{meth}('{n}')", getattr(obj, meth), n, p.label)
            g = _try(sap, f"{coll_o}.{get}", n)
            back[n] = str(g[0]) if g else None
    wrong = {n: v for n, v in back.items() if v != p.label}
    if wrong:
        raise CSIValidationError(f"Objects read back with another {p.kind} label: {wrong}")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- automatic seismic loads

SITE = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6}
DIRS = ("X", "X+e", "X-e", "Y", "Y+e", "Y-e")


class SeismicP(_EditP):
    load_pattern: str = Field(description="an existing load pattern of type quake")
    code: Literal["ASCE 7-16"] = Field(description="the code named by the designer (ASCE 7-16 in this version)")
    directions: list[Literal[DIRS]] = Field(min_length=1, description=(  # type: ignore[valid-type]
        "X | X+e | X-e | Y | Y+e | Y-e (with +e / -e: the eccentricity below)"))
    eccentricity: float = Field(default=0.05, ge=0, le=0.5, description="eccentricity ratio for +e / -e directions")
    period: Literal["approximate", "program", "user"] = Field(description=(
        "approximate (Ct from ct_type) | program (from the modal analysis, limited by Cu Ta) | user (user_period)"))
    ct_type: int = Field(default=0, ge=0, le=3, description=(
        "approximate period: 0 Ct=0.028 x=0.8 | 1 Ct=0.016 x=0.9 | 2 Ct=0.03 x=0.75 | 3 Ct=0.02 x=0.75 (ft units)"))
    user_period: float | None = Field(default=None, gt=0, description="user period T (s)")
    R: float = Field(gt=0, description="response modification factor")
    omega: float = Field(gt=0, description="system overstrength factor")
    Cd: float = Field(gt=0, description="deflection amplification factor")
    importance: float = Field(gt=0, description="importance factor Ie")
    Ss: float = Field(gt=0, description="mapped short-period spectral acceleration (g)")
    S1: float = Field(gt=0, description="mapped 1-second spectral acceleration (g)")
    TL: float = Field(gt=0, description="long-period transition period (s)")
    site_class: Literal["A", "B", "C", "D", "E", "F"] = Field(description="site class")
    Fa: float = Field(default=0.0, ge=0, description="site class F: user Fa")
    Fv: float = Field(default=0.0, ge=0, description="site class F: user Fv")
    top_z: float | None = Field(default=None, description="user top elevation of the load (default: program)")
    bottom_z: float | None = Field(default=None, description="user bottom elevation (default: program)")
    top_story: str | None = Field(default=None, description=(
        "[[E: ETABS: story at the top of the load (default: the highest story)]]"))
    bottom_story: str | None = Field(default=None, description=(
        "[[E: ETABS: story at the bottom of the load (default: Base)]]"))

    @model_validator(mode="after")
    def _check(self) -> "SeismicP":
        if self.period == "user" and self.user_period is None:
            raise ValueError("period user needs user_period")
        if self.site_class == "F" and not (self.Fa and self.Fv):
            raise ValueError("site class F needs Fa and Fv")
        if (self.top_z is None) != (self.bottom_z is None):
            raise ValueError("give both top_z and bottom_z, or neither")
        return self


ETABS_DIR_FIELDS = dict(zip(("XDir", "XDirPlusE", "XDirMinusE", "YDir", "YDirPlusE", "YDirMinusE"), DIRS))
GET_ARGS = ([False] * 6, 0.0, 0, 0, 0.0, False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)


def _table_directions(sap: Any, name: str) -> list[str] | None:
    """Directions as the program's input table shows them (the Get call's direction array is not laid out like the
    Set call's: ETABS 23.2 returns 7 flags, SAP2000 27.1 three - measured)."""
    import db_edit
    for key in ("Load Pattern Definitions - Auto Seismic - ASCE 7-16", "Auto Seismic - ASCE 7-16"):
        try:
            _, fields, rows = db_edit.read_table(sap, key)
        except Exception:  # noqa: BLE001
            continue
        if "XDir" in fields:
            row = next((r for r in rows if r.get("Name") == name), None)
            if row:
                return [d for f, d in ETABS_DIR_FIELDS.items() if str(row.get(f, "")).lower() == "yes"]
        if "Dir" in fields:
            dirs = [str(r.get("Dir", "")) for r in rows if r.get("LoadPat") == name]
            if dirs:
                return [d.replace(" Ecc.", "e").replace("Ecc", "e").replace(" ", "") for d in dirs]
    return None


def _etabs_story_range(sap: Any, p: Any, key: str = "Load Pattern Definitions - Auto Seismic - ASCE 7-16"
                       ) -> dict[str, str]:
    """ETABS: the API call leaves the load's story range at Base - Base (no load at all: base shear 0, measured in
    ETABS 23.2), so the range is set through the program's input table: the designer's stories, else the highest
    story and Base (what the program offers for a new load)."""
    import db_edit
    names = [s_["name"] for s_ in read_stories(sap)["stories"]]
    top = p.top_story or (names[-1] if names else "Base")
    bot = p.bottom_story or "Base"
    for s_ in (top, bot):
        if s_ != "Base" and s_ not in names:
            raise CSIValidationError(f"Story '{s_}' is not in the model.", hint=f"Stories: {names}")
    version, fields, rows = db_edit.read_table(sap, key)
    row = next((r for r in rows if r.get("Name") == p.load_pattern), None)
    if row is None or "TopStory" not in fields:
        raise CSIValidationError(f"'{key}' has no row for '{p.load_pattern}'; the story range could not be set.")
    row["TopStory"], row["BotStory"] = top, bot
    db_edit.write_table(sap, key, version, fields, rows)
    _, _, rows = db_edit.read_table(sap, key)
    got = next((r for r in rows if r.get("Name") == p.load_pattern), {})
    if (got.get("TopStory"), got.get("BotStory")) != (top, bot):
        raise CSIValidationError(f"Story range reads back as {got.get('TopStory')} - {got.get('BotStory')}.")
    return {"top": top, "bottom": bot, **({"defaulted": "top story = highest, bottom = Base (the program's offer "
                                                       "for a new load)"} if not (p.top_story and p.bottom_story)
                                         else {})}


def read_asce716(sap: Any, name: str) -> dict[str, Any] | None:
    o = _try(sap, "LoadPatterns.AutoSeismic.GetASCE716_1", name, *GET_ARGS) or \
        _try(sap, "LoadPatterns.AutoSeismic.GetASCE716", name, *GET_ARGS)
    if not o:
        return None
    keys = ("eccentricity", "period_flag", "ct_type", "user_period", "user_z", "top_z", "bottom_z", "R", "omega", "Cd",
            "importance", "Ss", "S1", "TL", "site_class", "Fa", "Fv")
    rec = dict(zip(keys, o[1:18]))
    rec["site_class"] = {v: k for k, v in SITE.items()}.get(int(rec["site_class"]), rec["site_class"])
    rec["period"] = {1: "approximate", 2: "program", 3: "user"}.get(int(rec.pop("period_flag")), "?")
    code = _try(sap, "LoadPatterns.GetAutoSeismicCode", name)
    return {"code": str(code[0]) if code else None, "directions": _table_directions(sap, name),
            **{k: (round(v, 9) if isinstance(v, float) else v) for k, v in rec.items()}}


@action("set_auto_seismic", "Make a quake load pattern an automatic seismic load (ASCE 7-16) with the designer's "
        "parameters: directions, eccentricity, period option, R, Omega, Cd, Ie, Ss, S1, TL, site class (Fa / Fv for "
        "F), optional load elevations. Every value comes from the designer. Answers with the parameters read back.",
        "Definitions", "edit", SeismicP, programs="ES", fields={"top_story": "E", "bottom_story": "E"})
def set_auto_seismic(p: SeismicP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    if p.load_pattern not in pats:
        raise CSIValidationError(f"Load pattern '{p.load_pattern}' is not defined.", hint=f"Patterns: {pats[:30]}")
    t = _try(sap, "LoadPatterns.GetLoadType", p.load_pattern)
    if t and int(t[0]) != 5:
        raise CSIValidationError(f"'{p.load_pattern}' is not a quake pattern (type code {t[0]}).",
                                 hint="define_load_pattern with type quake first.")
    plan = {k: v for k, v in p.model_dump().items() if k not in ("software", "dry_run", "snapshot", "allow_unlock")}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_auto_seismic", "set an automatic seismic load")
        flags = [d in p.directions for d in DIRS]
        pf = {"approximate": 1, "program": 2, "user": 3}[p.period]
        user_z = p.top_z is not None
        args = (p.load_pattern, flags, p.eccentricity, pf, p.ct_type, p.user_period or 0.0, user_z, p.top_z or 0.0,
                p.bottom_z or 0.0, p.R, p.omega, p.Cd, p.importance, p.Ss, p.S1, p.TL, SITE[p.site_class], p.Fa, p.Fv)
        # SetASCE716_1 first: ETABS 23.2 returned 1 for every SetASCE716 call and 0 for the same _1 call; the _1
        # form also fills Fa / Fv from the site class (SetASCE716 in SAP2000 27.1 left them 0) - measured
        auto = sap.LoadPatterns.AutoSeismic
        meth = "SetASCE716_1" if hasattr(auto, "SetASCE716_1") else "SetASCE716"
        try:
            api_call(f"LoadPatterns.AutoSeismic.{meth}", getattr(auto, meth), *args)
        except CSIError:
            if meth == "SetASCE716":
                raise
            api_call("LoadPatterns.AutoSeismic.SetASCE716", auto.SetASCE716, *args)
        stories = _etabs_story_range(sap, p) if p.software == "ETABS" else None
        back = read_asce716(sap, p.load_pattern)
    if not back:
        raise CSIValidationError("The automatic seismic parameters could not be read back.")
    if stories:
        back["stories"] = stories
    mism = {k: back.get(k) for k, v in (("R", p.R), ("importance", p.importance), ("Ss", p.Ss), ("S1", p.S1))
            if abs(float(back.get(k) or 0) - v) > 1e-9 * max(1, v)}
    if back["directions"] is not None and sorted(back["directions"]) != sorted(p.directions):
        mism["directions"] = back["directions"]
    if back.get("code") not in (None, "", p.code):
        mism["code"] = back["code"]
    if mism:
        raise CSIValidationError(f"Read back differs: {mism}")
    return {**plan, "read_back": back, "note": "Fa / Fv in read_back are the program's site coefficients for the "
            "site class.", "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- automatic wind

WIND_KEY_E = "Load Pattern Definitions - Auto Wind - ASCE 7-16"
WIND_USER_E = "Load Pattern Definitions - Auto Wind - User Loads"
MPH = {"mph": 1.0, "m/s": 1 / 0.44704, "km/h": 1 / 1.609344}
EXPOSURE_SAP = {"B": 1, "C": 2, "D": 3}


class WindP(_EditP):
    load_pattern: str = Field(description="an existing load pattern of type wind")
    code: Literal["ASCE 7-16"] = Field(description="the code named by the designer (ASCE 7-16 in this version)")
    exposure_from: Literal["diaphragms", "shells"] = Field(default="diaphragms", description=(
        "diaphragms (wind on the diaphragms' extents, at the direction angle) | shells (from the wind pressure "
        "coefficients assigned to areas)"))
    angle: float = Field(default=0.0, ge=-360, le=360, description="wind direction angle (deg) from global X, CCW "
                         "(diaphragm exposure)")
    Cpw: float | None = Field(default=None, description=(
        "windward pressure coefficient (diaphragm exposure)[[S:; required]][[E:; with Cpl: user Cp, else ETABS "
        "works Cp out from the building's L / B]]"))
    Cpl: float | None = Field(default=None, description="leeward pressure coefficient (positive number, as Cpw)")
    asce_case: Literal["1", "2", "3", "4", "all"] = Field(description=(
        "ASCE 7-16 Fig. 27.3-8 load case 1 | 2 | 3 | 4[[E: | all (ETABS makes every case)]]"))
    e1: float = Field(default=0.15, ge=0, le=1, description="ex ratio for cases 2 and 4 (ASCE 7-16: 0.15)")
    e2: float = Field(default=0.15, ge=0, le=1, description="ey ratio for cases 2 and 4 (ASCE 7-16: 0.15)")
    wind_speed: float = Field(gt=0, description="basic wind speed V (3-s gust)")
    wind_speed_unit: Literal["mph", "m/s", "km/h"] = Field(default="mph", description=(
        "unit of wind_speed; the programs take mph, converted exactly"))
    exposure_category: Literal["B", "C", "D"] = Field(description="exposure category")
    Kzt: float = Field(gt=0, description="topographic factor")
    gust_factor: float = Field(gt=0, description="gust-effect factor G")
    Kd: float = Field(gt=0, description="directionality factor")
    Ke: float | None = Field(default=None, gt=0, description="[[E: ground elevation factor Ke (default: ETABS' 1)]]")
    parapet_height: float | None = Field(default=None, gt=0, description="[[E: parapet height (diaphragm exposure)]]")
    top_z: float | None = Field(default=None, description="[[S: user top elevation of the load (default: program)]]")
    bottom_z: float | None = Field(default=None, description="[[S: user bottom elevation (default: program)]]")
    top_story: str | None = Field(default=None, description="[[E: top story (default: the highest story)]]")
    bottom_story: str | None = Field(default=None, description="[[E: bottom story (default: Base)]]")

    @model_validator(mode="after")
    def _check(self) -> "WindP":
        if (self.Cpw is None) != (self.Cpl is None):
            raise ValueError("give both Cpw and Cpl, or neither")
        if (self.top_z is None) != (self.bottom_z is None):
            raise ValueError("give both top_z and bottom_z, or neither")
        return self


def _wind_pattern(sap: Any, name: str) -> None:
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    if name not in pats:
        raise CSIValidationError(f"Load pattern '{name}' is not defined.", hint=f"Patterns: {pats[:30]}")
    t = _try(sap, "LoadPatterns.GetLoadType", name)
    if t and int(t[0]) != 6:
        raise CSIValidationError(f"'{name}' is not a wind pattern (type code {t[0]}).",
                                 hint="define_load_pattern with type wind first.")


def read_asce716_wind(sap: Any, software: str, name: str) -> dict[str, Any] | None:
    import db_edit
    code = _try(sap, "LoadPatterns.GetAutoWindCode", name)
    out: dict[str, Any] = {"code": str(code[0]) if code else None}
    if software == "SAP2000":
        o = _try(sap, "LoadPatterns.AutoWind.GetASCE716", name, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, False, 0.0, 0.0, 0.0,
                 0, 0.0, 0.0, 0.0, 0.0, False)
        if not o:
            return None
        keys = ("exposure_from", "angle", "Cpw", "Cpl", "asce_case", "e1", "e2", "user_z", "top_z", "bottom_z",
                "wind_speed_mph", "exposure_category", "Kzt", "gust_factor", "Kd", "solid_gross_ratio",
                "user_exposure")
        rec = dict(zip(keys, o[:17]))
        rec["exposure_from"] = {1: "diaphragms", 2: "shells"}.get(int(rec["exposure_from"]), rec["exposure_from"])
        rec["exposure_category"] = {v: k for k, v in EXPOSURE_SAP.items()}.get(int(rec["exposure_category"]),
                                                                              rec["exposure_category"])
        rec["asce_case"] = str(rec["asce_case"])
        out.update({k: (round(v, 9) if isinstance(v, float) else v) for k, v in rec.items()})
        return out
    try:
        _, _, rows = db_edit.read_table(sap, WIND_KEY_E)
    except Exception:  # noqa: BLE001
        return None
    mine = [r for r in rows if r.get("Name") == name]
    if not mine:
        return None
    head = mine[0]
    out.update({k: head.get(k) for k in ("Exposure", "TopStory", "BotStory", "Parapet", "ParapetHt", "UserCp",
                                         "UserCpw", "UserCpl", "ASCECase", "e1", "e2", "WindSpeed", "ExpType",
                                         "GrdElevFact", "kzt", "GustFact", "Kd", "Angle", "WidthType")})
    out["exposure_widths"] = [{k: r.get(k) for k in ("Story", "Diaphragm", "Width", "Depth", "X", "Y")}
                              for r in mine if r.get("Diaphragm")]
    return out


@action("set_auto_wind", "Make a wind load pattern an automatic wind load (ASCE 7-16) with the designer's "
        "parameters: exposure from diaphragms (direction angle, Cpw / Cpl) or shells, ASCE case, V, exposure "
        "category, Kzt, G, Kd, load range. Every value comes from the designer. Answers with the parameters read "
        "back[[E: and the exposure widths ETABS works out per diaphragm]]. For a code CSI does not list (e.g. ECP "
        "201): set_wind_user_loads with the designer's story forces.", "Definitions", "edit", WindP, programs="ES",
        fields={"Ke": "E", "parapet_height": "E", "top_story": "E", "bottom_story": "E", "top_z": "S", "bottom_z": "S"},
        options={"asce_case": {"all": "E"}})
def set_auto_wind(p: WindP) -> dict[str, Any]:
    import db_edit
    sap = bridge.sap_model(p.software)
    _wind_pattern(sap, p.load_pattern)
    if p.software == "SAP2000" and p.exposure_from == "diaphragms" and p.Cpw is None:
        raise CSIValidationError("SAP2000 needs Cpw and Cpl for wind on diaphragms.",
                                 hint="Give the designer's windward / leeward coefficients (ASCE 7-16 Fig. 27.3-1).")
    v_mph = p.wind_speed * MPH[p.wind_speed_unit]
    plan = {k: v for k, v in p.model_dump(exclude_none=True).items()
            if k not in ("software", "dry_run", "snapshot", "allow_unlock")}
    plan["wind_speed_mph"] = round(v_mph, 6)
    if p.software == "SAP2000":
        have = bridge.try_name_list(sap, ["ConstraintDef"])[0] or []
        diaphs = [c for c in have if (_try(sap, "ConstraintDef.GetConstraintType", c) or [None])[0] == 2]
        if p.exposure_from == "diaphragms" and not diaphs:
            plan["warning"] = ("No diaphragm constraints in the model: SAP2000 puts diaphragm wind on diaphragm "
                               "constraints (define_diaphragm), so this load would be zero.")
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_auto_wind", "set an automatic wind load")
        if p.software == "SAP2000":
            api_call("LoadPatterns.AutoWind.SetASCE716", sap.LoadPatterns.AutoWind.SetASCE716, p.load_pattern,
                     1 if p.exposure_from == "diaphragms" else 2, p.angle, p.Cpw or 0.0, p.Cpl or 0.0,
                     int(p.asce_case), p.e1, p.e2, p.top_z is not None, p.top_z or 0.0, p.bottom_z or 0.0, v_mph,
                     EXPOSURE_SAP[p.exposure_category], p.Kzt, p.gust_factor, p.Kd, 0.0, False)
        else:
            names = [s_["name"] for s_ in read_stories(sap)["stories"]]
            top = p.top_story or (names[-1] if names else "Base")
            bot = p.bottom_story or "Base"
            for s_ in (top, bot):
                if s_ != "Base" and s_ not in names:
                    raise CSIValidationError(f"Story '{s_}' is not in the model.", hint=f"Stories: {names}")
            version, fields, rows = db_edit.read_table(sap, WIND_KEY_E)
            rows = [r for r in rows if r.get("Name") != p.load_pattern]
            user_cp = p.Cpw is not None
            want = {"Name": p.load_pattern, "IsAuto": "No",
                    "Exposure": "Diaphragms" if p.exposure_from == "diaphragms" else "Shells",
                    "TopStory": top, "BotStory": bot, "Parapet": _yes(p.parapet_height is not None),
                    "ParapetHt": f"{p.parapet_height:g}" if p.parapet_height else "",
                    "UserCp": _yes(user_cp), "UserCpw": f"{p.Cpw:g}" if user_cp else "",
                    "UserCpl": f"{p.Cpl:g}" if user_cp else "",
                    "ASCECase": "Create All" if p.asce_case == "all" else p.asce_case,
                    "e1": f"{p.e1:g}", "e2": f"{p.e2:g}", "WindSpeed": f"{v_mph:.10g}",
                    "ExpType": p.exposure_category, "GrdElevFact": f"{p.Ke:g}" if p.Ke else "1",
                    "kzt": f"{p.Kzt:g}", "GustFact": f"{p.gust_factor:g}", "Kd": f"{p.Kd:g}",
                    "WidthType": "Auto", "Angle": f"{p.angle:g}" if p.exposure_from == "diaphragms" else ""}
            missing = [f for f in ("Name", "Exposure", "WindSpeed", "ExpType", "TopStory") if f not in fields]
            if missing:
                raise CSIValidationError(f"'{WIND_KEY_E}' has no field(s) {missing}; fields: {fields}.")
            rows.append({f: want.get(f, "") for f in fields})
            db_edit.write_table(sap, WIND_KEY_E, version, fields, rows)
        back = read_asce716_wind(sap, p.software, p.load_pattern)
    if not back:
        raise CSIValidationError("The automatic wind parameters could not be read back.")
    mism: dict[str, Any] = {}
    if back.get("code") not in ("ASCE7-16", "ASCE 7-16"):
        mism["code"] = back.get("code")
    if p.software == "SAP2000":
        for k, v in (("wind_speed_mph", v_mph), ("Kzt", p.Kzt), ("gust_factor", p.gust_factor), ("Kd", p.Kd)):
            if abs(float(back.get(k) or 0) - v) > 1e-6 * max(1, v):
                mism[k] = back.get(k)
        if back.get("exposure_category") != p.exposure_category:
            mism["exposure_category"] = back.get("exposure_category")
    else:
        for k, v in (("WindSpeed", v_mph), ("kzt", p.Kzt), ("GustFact", p.gust_factor), ("Kd", p.Kd)):
            if abs(float(back.get(k) or 0) - v) > 1e-4 * max(1, v):
                mism[k] = back.get(k)
        if back.get("ExpType") != p.exposure_category:
            mism["ExpType"] = back.get("ExpType")
    if mism:
        raise CSIValidationError(f"Read back differs: {mism}")
    how = ("SAP2000 takes the pressure at each diaphragm's elevation over its tributary height, the lowest "
           "diaphragm down to the bottom of the load (measured)" if p.software == "SAP2000" else
           "ETABS integrates the pressure over the height and gives each storey's load to the levels below and above "
           "as simple-span reactions; the half of the lowest storey's load at the base goes to the supports "
           "(measured)")
    return {**plan, "read_back": back, "note": f"The story forces are worked out at analysis: {how}. Read them "
            "after run_analysis with query_database_table ('Auto Wind Loads To Horizontal Diaphragms' in SAP2000) or "
            "the base reactions.", "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


class WindLoad(Params):
    diaphragm: str = Field(min_length=1, description="diaphragm[[S: constraint]] that takes the load")
    story: str | None = Field(default=None, description="[[E: story of the diaphragm (required)]]")
    fx: float = Field(default=0.0, description="force in global X")
    fy: float = Field(default=0.0, description="force in global Y")
    mz: float = Field(default=0.0, description="moment about Z")
    x: float = Field(description="X of the load point")
    y: float = Field(description="Y of the load point")


class WindUserP(_EditP):
    load_pattern: str = Field(description="an existing load pattern of type wind")
    loads: list[WindLoad] = Field(min_length=1, max_length=500, description=(
        "the designer's forces per diaphragm (e.g. from ECP 201): diaphragm, [[E:story, ]]fx, fy, mz, x, y"))
    reversible: bool = Field(default=False, description="[[E: loads reversible in combinations]]")


@action("set_wind_user_loads", "Make a wind load pattern carry the designer's wind forces on diaphragms (user "
        "loads): for each diaphragm Fx, Fy, Mz at a point - for codes CSI does not list (e.g. ECP 201), with the "
        "forces worked out by the designer. Replaces the pattern's earlier auto wind. Answers with the loads read "
        "back.", "Definitions", "edit", WindUserP, programs="ES", fields={"reversible": "E"})
def set_wind_user_loads(p: WindUserP) -> dict[str, Any]:
    import db_edit
    sap = bridge.sap_model(p.software)
    _wind_pattern(sap, p.load_pattern)
    if p.software == "ETABS":
        names = [s_["name"] for s_ in read_stories(sap)["stories"]]
        bad = [ld.story for ld in p.loads if not ld.story or ld.story not in names]
        if bad:
            raise CSIValidationError(f"ETABS user wind loads need each load's story; not in the model: {bad[:10]}",
                                     hint=f"Stories: {names}")
        have = bridge.try_name_list(sap, ["Diaphragm"])[0] or []
    else:
        have = bridge.try_name_list(sap, ["ConstraintDef"])[0] or []
    missing = sorted({ld.diaphragm for ld in p.loads} - set(have))
    if missing:
        raise CSIValidationError(f"Diaphragm(s) not in the model: {missing}", hint=f"Diaphragms: {have[:30]}"
                                 + ("" if p.software == "ETABS" else " (define_diaphragm makes them in SAP2000)"))
    if p.software == "SAP2000":
        dup = [d for d in {ld.diaphragm for ld in p.loads} if sum(ld.diaphragm == d for ld in p.loads) > 1]
        if dup:
            raise CSIValidationError(f"SAP2000 takes one user wind load per diaphragm; repeated: {dup}")
    plan = {"load_pattern": p.load_pattern, "loads": [ld.model_dump(exclude_none=True) for ld in p.loads],
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_wind_user_loads", "set user wind loads")
        if p.software == "SAP2000":
            for ld in p.loads:
                api_call(f"LoadPatterns.AutoWind.SetUserLoad('{ld.diaphragm}')", sap.LoadPatterns.AutoWind.SetUserLoad,
                         p.load_pattern, ld.diaphragm, ld.fx, ld.fy, ld.mz, ld.x, ld.y)
            o = api_call("LoadPatterns.AutoWind.GetUserLoad", sap.LoadPatterns.AutoWind.GetUserLoad, p.load_pattern,
                         0, [], [], [], [], [], [])
            back = [{"diaphragm": str(o[1][i]), "fx": float(o[2][i]), "fy": float(o[3][i]), "mz": float(o[4][i]),
                     "x": float(o[5][i]), "y": float(o[6][i])} for i in range(int(o[0]))]
        else:
            version, fields, rows = db_edit.read_table(sap, WIND_USER_E)
            rows = [r for r in rows if r.get("Name") != p.load_pattern]
            for i, ld in enumerate(p.loads):
                want = {"Name": p.load_pattern, "NumberSets": "1" if i == 0 else "", "Reversible":
                        _yes(p.reversible) if i == 0 else "", "SetNumber": "1", "Story": ld.story,
                        "Diaphragm": ld.diaphragm, "Fx": f"{ld.fx:.10g}", "Fy": f"{ld.fy:.10g}",
                        "Mz": f"{ld.mz:.10g}", "X": f"{ld.x:.10g}", "Y": f"{ld.y:.10g}"}
                rows.append({f: want.get(f, "") for f in fields})
            db_edit.write_table(sap, WIND_USER_E, version, fields, rows)
            _, _, rows2 = db_edit.read_table(sap, WIND_USER_E)
            back = [{"story": r.get("Story"), "diaphragm": r.get("Diaphragm"), "fx": float(r.get("Fx") or 0),
                     "fy": float(r.get("Fy") or 0), "mz": float(r.get("Mz") or 0), "x": float(r.get("X") or 0),
                     "y": float(r.get("Y") or 0)} for r in rows2 if r.get("Name") == p.load_pattern]
        code = _try(sap, "LoadPatterns.GetAutoWindCode", p.load_pattern)
    key = (lambda d: (d.get("story") or "", d["diaphragm"]))
    want_map = {(ld.story or "", ld.diaphragm): ld for ld in p.loads}
    bad = []
    for b in back:
        w = want_map.get(key(b)) if p.software == "ETABS" else want_map.get(("", b["diaphragm"]))
        if w is None or any(abs(b[k] - getattr(w, k)) > 1e-6 * max(1, abs(getattr(w, k))) for k in
                            ("fx", "fy", "mz", "x", "y")):
            bad.append(b)
    if bad or len(back) != len(p.loads):
        raise CSIValidationError(f"Read back differs ({len(back)} loads read back): {bad[:10]}")
    return {**plan, "read_back": {"code": str(code[0]) if code else None, "loads": back},
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- user coefficient seismic

UC_KEY = "Load Pattern Definitions - Auto Seismic - User Coefficient"


class CoeffP(_EditP):
    load_pattern: str = Field(description="an existing load pattern of type quake")
    direction: Literal["X", "Y"] = Field(description="X | Y")
    eccentricity: float = Field(default=0.0, ge=0, le=0.5, description=(
        "eccentricity ratio (0 = none); in ETABS it adds the +e and -e directions"))
    C: float = Field(gt=0, description="base shear coefficient: V = C W (from the designer, e.g. the code's Cs)")
    K: float = Field(default=1.0, ge=0.5, le=3.0, description="building height exponent of the vertical distribution")
    top_z: float | None = Field(default=None, description="[[S: user top elevation (default: program)]]")
    bottom_z: float | None = Field(default=None, description="[[S: user bottom elevation (default: program)]]")
    top_story: str | None = Field(default=None, description="[[E: top story (default: the highest story)]]")
    bottom_story: str | None = Field(default=None, description="[[E: bottom story (default: Base)]]")

    @model_validator(mode="after")
    def _z(self) -> "CoeffP":
        if (self.top_z is None) != (self.bottom_z is None):
            raise ValueError("give both top_z and bottom_z, or neither")
        return self


def _yes(v: bool) -> str:
    return "Yes" if v else "No"


@action("set_seismic_coefficient", "Make a quake load pattern a user-coefficient seismic load: base shear V = C x W "
        "(W from the mass source) distributed over the height with exponent K - for a code CSI does not list (e.g. "
        "ECP 201), with C worked out by the designer. Direction X or Y, eccentricity, load range. Answers with the "
        "parameters read back.", "Definitions", "edit", CoeffP, programs="ES",
        fields={"top_z": "S", "bottom_z": "S", "top_story": "E", "bottom_story": "E"})
def set_seismic_coefficient(p: CoeffP) -> dict[str, Any]:
    import db_edit
    sap = bridge.sap_model(p.software)
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    if p.load_pattern not in pats:
        raise CSIValidationError(f"Load pattern '{p.load_pattern}' is not defined.", hint=f"Patterns: {pats[:30]}")
    t = _try(sap, "LoadPatterns.GetLoadType", p.load_pattern)
    if t and int(t[0]) != 5:
        raise CSIValidationError(f"'{p.load_pattern}' is not a quake pattern (type code {t[0]}).",
                                 hint="define_load_pattern with type quake first.")
    plan = {k: v for k, v in p.model_dump(exclude_none=True).items()
            if k not in ("software", "dry_run", "snapshot", "allow_unlock")}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_seismic_coefficient", "set a seismic coefficient load")
        if p.software == "SAP2000":
            api_call("LoadPatterns.AutoSeismic.SetUserCoefficient", sap.LoadPatterns.AutoSeismic.SetUserCoefficient,
                     p.load_pattern, 1 if p.direction == "X" else 2, p.eccentricity, p.top_z is not None,
                     p.top_z or 0.0, p.bottom_z or 0.0, p.C, p.K)
            o = api_call("LoadPatterns.AutoSeismic.GetUserCoefficient", sap.LoadPatterns.AutoSeismic.GetUserCoefficient,
                         p.load_pattern, 0, 0.0, False, 0.0, 0.0, 0.0, 0.0)
            back = {"direction": {1: "X", 2: "Y"}.get(int(o[0]), o[0]), "eccentricity": float(o[1]),
                    "user_z": bool(o[2]), "top_z": float(o[3]), "bottom_z": float(o[4]), "C": float(o[5]),
                    "K": float(o[6])}
        else:
            # ETABS: no API setter for this load - the program's input table (one row per pattern)
            names = [s_["name"] for s_ in read_stories(sap)["stories"]]
            top = p.top_story or (names[-1] if names else "Base")
            bot = p.bottom_story or "Base"
            for s_ in (top, bot):
                if s_ != "Base" and s_ not in names:
                    raise CSIValidationError(f"Story '{s_}' is not in the model.", hint=f"Stories: {names}")
            version, fields, rows = db_edit.read_table(sap, UC_KEY)
            rows = [r for r in rows if r.get("Name") != p.load_pattern]
            e = p.eccentricity > 0
            want = {"Name": p.load_pattern, "IsAuto": "No",
                    "XDir": _yes(p.direction == "X" and not e), "XDirPlusE": _yes(p.direction == "X" and e),
                    "XDirMinusE": _yes(p.direction == "X" and e), "YDir": _yes(p.direction == "Y" and not e),
                    "YDirPlusE": _yes(p.direction == "Y" and e), "YDirMinusE": _yes(p.direction == "Y" and e),
                    "EccRatio": str(p.eccentricity), "TopStory": top, "BotStory": bot, "C": str(p.C), "K": str(p.K)}
            missing = [f for f in ("Name", "C", "K", "TopStory", "BotStory") if f not in fields]
            if missing:
                raise CSIValidationError(f"'{UC_KEY}' has no field(s) {missing}; fields: {fields}.")
            rows.append({f: want.get(f, "") for f in fields})
            db_edit.write_table(sap, UC_KEY, version, fields, rows)
            _, _, rows2 = db_edit.read_table(sap, UC_KEY)
            r = next((x for x in rows2 if x.get("Name") == p.load_pattern), None)
            if not r:
                raise CSIValidationError(f"'{UC_KEY}' has no row for '{p.load_pattern}' after the write.")
            back = {k: r.get(k) for k in fields if k != "GUID"}
            back["C"], back["K"] = float(r["C"]), float(r["K"])
            back.update(stories_defaulted=None if (p.top_story and p.bottom_story) else
                        "top story = highest, bottom = Base (the program's offer for a new load)")
        code = _try(sap, "LoadPatterns.GetAutoSeismicCode", p.load_pattern)
    back["code"] = str(code[0]) if code else None
    if not (_close_rel(back["C"], p.C) and _close_rel(back["K"], p.K)):
        raise CSIValidationError(f"Read back differs: C {back['C']}, K {back['K']}.")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


def _close_rel(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


# --------------------------------------------------------------------------- links

LDOF = ("U1", "U2", "U3", "R1", "R2", "R3")
LINK_TYPES = {"linear": 1, "gap": 3, "hook": 4, "multilinear_elastic": 8}   # PropLink.GetTypeOAPI (measured)


class LinkNL(Params):
    stiffness: float | None = Field(default=None, gt=0, description="gap / hook: nonlinear stiffness")
    opening: float | None = Field(default=None, ge=0, description="gap / hook: initial opening")
    points: list[list[float]] | None = Field(default=None, min_length=2, max_length=500, description=(
        "multilinear_elastic: [[deformation, force], ...] in increasing deformation, through [0, 0]"))


class LinkPropP(_EditP):
    name: str = Field(min_length=1, description="link property name")
    type: Literal["linear", "gap", "hook", "multilinear_elastic"] = Field(default="linear", description=(
        "linear (default) | gap (compression only, after an opening) | hook (tension only, after an opening) | "
        "multilinear_elastic (force-deformation points); nonlinear behaviour acts in nonlinear cases"))
    stiffness: dict[Literal[LDOF], float] = Field(default={}, description=(  # type: ignore[valid-type]
        "linear stiffness per direction, e.g. {'U1': 1000} (force / length, moment / radian); for nonlinear "
        "directions the effective stiffness used by linear cases"))
    fixed: list[Literal[LDOF]] = Field(default=[], description=(  # type: ignore[valid-type]
        "directions that are rigid (fixed) instead of springs"))
    damping: dict[Literal[LDOF], float] = Field(default={}, description="linear damping per direction (optional)")
    nonlinear: dict[Literal[LDOF], LinkNL] = Field(default={}, description=(  # type: ignore[valid-type]
        "gap / hook / multilinear_elastic: the nonlinear directions, e.g. {'U1': {'stiffness': 1000, "
        "'opening': 0.01}} or {'U1': {'points': [[-0.1, -50], [0, 0], [0.1, 50]]}}"))
    replace: bool = Field(default=False, description="change an existing link property")

    @model_validator(mode="after")
    def _check(self) -> "LinkPropP":
        both = (set(self.stiffness) | set(self.nonlinear)) & set(self.fixed)
        if both:
            raise ValueError(f"a direction is a spring or fixed, not both: {sorted(both)}")
        if set(self.damping) - set(self.stiffness):
            raise ValueError("damping only on spring directions")
        if self.type == "linear":
            if self.nonlinear:
                raise ValueError("nonlinear directions need type gap, hook or multilinear_elastic")
            if not self.stiffness:
                raise ValueError("give the stiffness of at least one direction")
        elif not self.nonlinear:
            raise ValueError(f"type {self.type} needs nonlinear directions")
        for d, nl in self.nonlinear.items():
            if self.type in ("gap", "hook") and (nl.stiffness is None or nl.opening is None):
                raise ValueError(f"{d}: {self.type} needs stiffness and opening")
            if self.type == "multilinear_elastic":
                pts = nl.points or []
                if not pts or any(len(q) != 2 for q in pts):
                    raise ValueError(f"{d}: multilinear_elastic needs points [[deformation, force], ...]")
                if any(b[0] <= a[0] for a, b in zip(pts, pts[1:])):
                    raise ValueError(f"{d}: deformations must increase")
                if [0.0, 0.0] not in [[float(x), float(y)] for x, y in pts]:
                    raise ValueError(f"{d}: the points must pass through [0, 0]")
        return self


GET6 = ([False] * 6, [False] * 6, [False] * 6, [0.0] * 6, [0.0] * 6)


def read_link_prop(sap: Any, name: str) -> dict[str, Any] | None:
    t = _try(sap, "PropLink.GetTypeOAPI", name)
    code = int(t[0]) if t else 1
    kind = {v: k for k, v in LINK_TYPES.items()}.get(code, f"type {code}")
    out: dict[str, Any] = {"name": name, "type": kind}
    if kind == "linear":
        o = _try(sap, "PropLink.GetLinear", name, [False] * 6, [False] * 6, [0.0] * 6, [0.0] * 6, 0.0, 0.0, False,
                 False, "", "")
        if not o:
            return None
        dof, fixed, ke, ce = (list(as_list(o[i])) for i in range(4))
        nl = [False] * 6
    elif kind in ("gap", "hook"):
        o = _try(sap, f"PropLink.Get{kind.title()}", name, *GET6, [0.0] * 6, [0.0] * 6, 0.0, 0.0, "", "")
        if not o:
            return None
        dof, fixed, nl, ke, ce, kk, dis = (list(as_list(o[i])) for i in range(7))
        out["nonlinear"] = {d: {"stiffness": float(k), "opening": float(x)}
                            for d, on, n, k, x in zip(LDOF, dof, nl, kk, dis) if on and n}
    elif kind == "multilinear_elastic":
        o = _try(sap, "PropLink.GetMultiLinearElastic", name, *GET6, 0.0, 0.0, "", "")
        if not o:
            return None
        dof, fixed, nl, ke, ce = (list(as_list(o[i])) for i in range(5))
        out["nonlinear"] = {}
        for i, (d, on, n) in enumerate(zip(LDOF, dof, nl)):
            if on and n:
                q = _try(sap, "PropLink.GetMultiLinearPoints", name, i + 1, 0, [], [], 0, 0.0, 0.0, 0.0, 0.0, 0.0)
                if q:
                    out["nonlinear"][d] = {"points": [[float(dd), float(f)] for f, dd in
                                                      zip(as_list(q[1]), as_list(q[2]))]}
    else:
        return out
    out.update(stiffness={d: float(k) for d, on, fx, k in zip(LDOF, dof, fixed, ke) if on and not fx},
               fixed=[d for d, on, fx in zip(LDOF, dof, fixed) if on and fx],
               damping={d: float(c) for d, on, fx, c in zip(LDOF, dof, fixed, ce) if on and not fx and c})
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


@action("define_link_property", "Create or change a link property: linear (spring stiffness and damping per direction "
        "U1-R3 in the link's local axes, rigid directions, the rest free) or nonlinear gap / hook (stiffness and "
        "opening) or multi-linear elastic (force-deformation points) in chosen directions - nonlinear behaviour acts in "
        "nonlinear cases (define_load_case nonlinear_static). Answers with the property read back.", "Definitions",
        "edit", LinkPropP, programs="ES")
def define_link_property(p: LinkPropP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    have = bridge.try_name_list(sap, ["PropLink"])[0] or []
    if p.name in have and not p.replace:
        raise CSIValidationError(f"Link property '{p.name}' exists.", hint="replace: true changes it.")
    plan = {k: v for k, v in p.model_dump(exclude_defaults=True).items()
            if k not in ("software", "dry_run", "snapshot", "allow_unlock")}
    plan["type"] = p.type
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_link_property", "define a link property")
        dof = [d in p.stiffness or d in p.fixed or d in p.nonlinear for d in LDOF]
        fixed = [d in p.fixed for d in LDOF]
        nl = [d in p.nonlinear for d in LDOF]
        ke = [float(p.stiffness.get(d, 0.0)) for d in LDOF]
        ce = [float(p.damping.get(d, 0.0)) for d in LDOF]
        pl = sap.PropLink
        if p.type == "linear":
            api_call("PropLink.SetLinear", pl.SetLinear, p.name, dof, fixed, ke, ce, 0.0, 0.0, False, False)
        elif p.type in ("gap", "hook"):
            kk = [float(p.nonlinear[d].stiffness or 0) if d in p.nonlinear else 0.0 for d in LDOF]
            dis = [float(p.nonlinear[d].opening or 0) if d in p.nonlinear else 0.0 for d in LDOF]
            meth = "SetGap" if p.type == "gap" else "SetHook"
            api_call(f"PropLink.{meth}", getattr(pl, meth), p.name, dof, fixed, nl, ke, ce, kk, dis, 0.0, 0.0)
        else:
            api_call("PropLink.SetMultiLinearElastic", pl.SetMultiLinearElastic, p.name, dof, fixed, nl, ke, ce, 0.0,
                     0.0)
            for i, d in enumerate(LDOF):
                if d in p.nonlinear:
                    pts = p.nonlinear[d].points or []
                    api_call(f"PropLink.SetMultiLinearPoints({d})", pl.SetMultiLinearPoints, p.name, i + 1, len(pts),
                             [float(q[1]) for q in pts], [float(q[0]) for q in pts])
        back = read_link_prop(sap, p.name)
    bad = not back or back.get("type") != p.type or sorted(back.get("fixed", [])) != sorted(p.fixed) \
        or any(not _close(back["stiffness"].get(d, 0), v) for d, v in p.stiffness.items())
    if not bad and p.type in ("gap", "hook"):
        bad = any(not (_close(back["nonlinear"].get(d, {}).get("stiffness", -1), v.stiffness or 0)
                       and _close(back["nonlinear"].get(d, {}).get("opening", -1), v.opening or 0))
                  for d, v in p.nonlinear.items())
    if not bad and p.type == "multilinear_elastic":
        bad = any(len(back["nonlinear"].get(d, {}).get("points", [])) != len(v.points or [])
                  for d, v in p.nonlinear.items())
    if bad:
        raise CSIValidationError(f"Link property '{p.name}' reads back as {back}.")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


class NewLink(Params):
    point1: str = Field(description="first point")
    point2: str | None = Field(default=None, description="second point; omit for a one-joint (grounded) link")
    name: str = Field(default="", description="name for the link")


class LinksP(_EditP):
    property: str = Field(description="an existing link property")
    links: list[NewLink] = Field(min_length=1, max_length=MAX, description="links to add")


@action("create_links", "Add link objects with a link property: two-joint links between points, or one-joint "
        "(grounded) links at a point. Answers with the link names, points and property read back.", "Objects",
        "edit", LinksP, programs="ES")
def create_links(p: LinksP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    props = bridge.try_name_list(sap, ["PropLink"])[0] or []
    if p.property not in props:
        raise CSIValidationError(f"Link property '{p.property}' is not defined.", hint=f"Defined: {props[:30]}")
    pts = set(_names(sap, "PointObj"))
    missing = sorted({q for lk in p.links for q in (lk.point1, lk.point2) if q} - pts)
    if missing:
        raise CSIValidationError(f"Points not in the model: {missing[:20]}.")
    plan = {"property": p.property, "links": len(p.links)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_links", "add links")
    made = []
    for lk in p.links:
        single = lk.point2 is None
        o = api_call("LinkObj.AddByPoint", sap.LinkObj.AddByPoint, lk.point1, lk.point2 or lk.point1, "", single,
                     p.property, lk.name)
        name = str(o[0])
        g = api_call("LinkObj.GetPoints", sap.LinkObj.GetPoints, name)
        pr = api_call("LinkObj.GetProperty", sap.LinkObj.GetProperty, name)
        made.append({"name": name, "point1": str(g[0]), "point2": str(g[1]), "property": str(pr[0]),
                     "one_joint": single})
        if str(pr[0]) != p.property:
            raise CSIValidationError(f"Link {name} reads back with property {pr[0]}.")
    _refresh_view(sap)
    return {**plan, "created": made, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
