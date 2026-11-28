"""Phase 8.6 (0.24.0): buckling, direct-integration time-history (linear and nonlinear), nonlinear modal (FNA)
time-history, steady-state, power-spectral-density and hyperstatic load cases.

Measured (tests/diag_86.py, diag_86b.py, 28 Nov 2026):
- SAP2000 27.1: all typed - LoadCases.Buckling (SetCase, SetLoads, SetParameters, SetInitialCase), DirHistLinear /
  DirHistNonlinear (SetLoads, SetTimeStep, SetTimeIntegration 1 Newmark ... 4 Hilber-Hughes-Taylor (the default),
  SetDampProportional, SetGeometricNonlinearity, SetInitialCase), ModHistNonlinear (SetModalCase, SetDampConstant),
  SteadyState / PSD (SetLoads with phase angles, SetFreqData, SetDampConstant = hysteretic mass / stiffness
  coefficients), HyperStatic (SetCase, SetBaseCase). Case types read back by GetTypeOAPI_1: buckling 10, linear direct
  7, nonlinear direct 8, nonlinear modal 6, steady state 11, PSD 12, hyperstatic 14.
- ETABS 23.2: the Buckling / DirHist / ModHistNonlinear interfaces have no setters (only GetLoads) and there is no
  SteadyState / PSD interface; their 'Load Case Definitions - ...' tables import with no errors and give the same case
  types (FNA damping in 'Load Case Definitions - Damping - Modal', steady-state damping in '... - Damping -
  Hysteretic', whose import is refused in every form - the program's own row changed, dropped and re-added, or added
  (diag_86c, 28 Nov); the program gives a new steady-state case constant hysteretic damping, mass 0 / stiffness 0.05,
  so ETABS takes no hysteretic coefficients here and reads them back from that table). A row imported into 'Load Case Definitions - Power
  Spectral Density' becomes a steady-state case with the function 'Default Uniform' (live_86, 28 Nov), so PSD cases
  are SAP2000 only. HyperStatic is typed.
- SAFE 23.3: only the hyperstatic case (typed).
- Hyperstatic results: SAP2000 gives them for any linear static base case (two-span beam with end couples: the
  secondary moment = 1.5 M0 / (1 + 12 EI / (G As (2L)^2)) exactly, live_86). ETABS and SAFE define and run the case
  but report no results for a base case without tendons (neither the results API nor 'Element Forces - Beams' has
  HYP rows, measured): their hyperstatic forces come from post-tensioning tendons.
- ETABS steady-state results are in the 'Joint Displacements' table as Real / Imag rows (query_database_table); the
  results API answers no rows for them (measured). |u| = F / k / sqrt((1 - r^2)^2 + eta^2) checked there.
Every value (loads, functions, steps, damping, integration, frequencies, modes) comes from the designer (D1).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import ActionFailed, Params, _dry, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list
from db_edit import edit_table, read_table
from model_read import _try
from modelling import _start_edit
from objects import _EditP

TYPE_CODE = {"buckling": 10, "linear_direct_history": 7, "nonlinear_direct_history": 8,
             "nonlinear_modal_history": 6, "steady_state": 11, "power_spectral_density": 12, "hyperstatic": 14}
INTEGRATION = {"newmark": 1, "wilson": 2, "collocation": 3, "hilber_hughes_taylor": 4, "chung_hulbert": 5}
INTEGRATION_TEXT = {"newmark": "Newmark", "wilson": "Wilson", "collocation": "Collocation",
                    "hilber_hughes_taylor": "Hilber-Hughes-Taylor", "chung_hulbert": "Chung and Hulbert"}
GEO = {"none": 0, "p_delta": 1, "p_delta_large_displacements": 2}
GEO_TEXT = {"none": "None", "p_delta": "P-Delta", "p_delta_large_displacements": "Large Displacements"}
ETABS_TABLE = {"buckling": "Load Case Definitions - Buckling",
               "linear_direct_history": "Load Case Definitions - Time History - Linear Direct Integration",
               "nonlinear_direct_history": "Load Case Definitions - Time History - Nonlin Direct Integration",
               "nonlinear_modal_history": "Load Case Definitions - Time History - Nonlinear Modal",
               "steady_state": "Load Case Definitions - Steady State",
               "power_spectral_density": "Load Case Definitions - Power Spectral Density"}
MODAL_DAMPING = "Load Case Definitions - Damping - Modal"
HYST_DAMPING = "Load Case Definitions - Damping - Hysteretic"
ACCEL = ("U1", "U2", "U3", "R1", "R2", "R3")
BUCKLING_ACCEL = {"U1": "UX", "U2": "UY", "U3": "UZ", "R1": "RX", "R2": "RY", "R3": "RZ"}
TH = ("linear_direct_history", "nonlinear_direct_history", "nonlinear_modal_history")
FREQ = ("steady_state", "power_spectral_density")


class CaseLoad(Params):
    kind: Literal["load_pattern", "acceleration"] = Field(default="load_pattern", description=(
        "load_pattern | acceleration (ground acceleration in U1 / U2 / U3, R1 / R2 / R3)"))
    name: str = Field(description="the load pattern, or U1 / U2 / U3 / R1 / R2 / R3 for an acceleration")
    factor: float = Field(default=1.0, description=(
        "scale factor (acceleration: in the length / s2 of `units`, e.g. 9.81 for a function in g with m)"))
    function: str | None = Field(default=None, description=(
        "time history / steady state / PSD: the function (define_function)"))
    time_factor: float = Field(default=1.0, gt=0, description="time history: time scale factor of the function")
    arrival_time: float = Field(default=0.0, ge=0, description="time history: arrival time, s")
    phase_angle: float = Field(default=0.0, description="steady state / PSD: phase angle, degrees")
    angle: float = Field(default=0.0, description="acceleration: direction angle, degrees")


class Damping(Params):
    method: Literal["mass_stiffness", "periods", "frequencies", "constant"] = Field(description=(
        "direct-integration (Rayleigh): mass_stiffness (coefficients a, b) | periods / frequencies (two points with "
        "damping ratios) | FNA: constant (modal damping ratio for all modes)"))
    mass_coefficient: float = Field(default=0.0, ge=0, description="mass_stiffness: a (1/s)")
    stiffness_coefficient: float = Field(default=0.0, ge=0, description="mass_stiffness: b (s)")
    point_1: list[float] | None = Field(default=None, min_length=2, max_length=2, description=(
        "periods / frequencies: [period (s) or frequency (Hz), damping ratio]"))
    point_2: list[float] | None = Field(default=None, min_length=2, max_length=2, description="the second point")
    ratio: float = Field(default=0.0, ge=0, lt=1, description="constant: damping ratio")

    @model_validator(mode="after")
    def _check(self) -> "Damping":
        if self.method in ("periods", "frequencies") and not (self.point_1 and self.point_2):
            raise ValueError(f"{self.method} needs point_1 and point_2")
        return self


class Integration(Params):
    method: Literal["newmark", "wilson", "collocation", "hilber_hughes_taylor", "chung_hulbert"] = Field(
        description="time integration method")
    gamma: float = Field(default=0.5, description="newmark / collocation: gamma")
    beta: float = Field(default=0.25, description="newmark / collocation: beta")
    theta: float = Field(default=1.0, description="wilson / collocation: theta")
    alpha: float = Field(default=0.0, description="hilber_hughes_taylor / chung_hulbert: alpha")
    alpha_m: float = Field(default=0.0, description="chung_hulbert: alpha-m")


class AdvCaseP(_EditP):
    name: str = Field(min_length=1, max_length=80, description="the load case")
    type: Literal["buckling", "linear_direct_history", "nonlinear_direct_history", "nonlinear_modal_history",
                  "steady_state", "power_spectral_density", "hyperstatic"] = Field(description=(
        "buckling | linear_direct_history | nonlinear_direct_history | nonlinear_modal_history (FNA) | steady_state | "
        "power_spectral_density | hyperstatic"))
    loads: list[CaseLoad] = Field(default=[], max_length=200, description=(
        "the loads (not for hyperstatic): load patterns or accelerations with factors (and functions for time history, "
        "steady state and PSD), from the designer"))
    modes: int = Field(default=6, ge=1, le=1000, description="buckling: number of buckling modes")
    eigen_tolerance: float = Field(default=1e-9, gt=0, description="buckling: eigenvalue convergence tolerance")
    initial_case: str | None = Field(default=None, description=(
        "buckling / nonlinear direct history: start from the stiffness / state at the end of this nonlinear case"))
    steps: int | None = Field(default=None, ge=1, le=1000000, description="time history: number of output steps")
    step_size: float | None = Field(default=None, gt=0, description="time history: output step size, s")
    damping: Damping | None = Field(default=None, description=(
        "time history damping, from the designer (omitted: the program's default)"))
    integration: Integration | None = Field(default=None, description=(
        "direct-integration time history: method and parameters (omitted: the program's default, Hilber-Hughes-Taylor "
        "alpha 0)"))
    geometric_nonlinearity: Literal["none", "p_delta", "p_delta_large_displacements"] = Field(default="none",
        description="nonlinear direct history: none | p_delta | p_delta_large_displacements")
    modal_case: str | None = Field(default=None, description=(
        "FNA: the modal case (Ritz vectors recommended); steady state / PSD: modal case for added modal frequencies"))
    first_frequency: float | None = Field(default=None, ge=0, description="steady state / PSD: first frequency, Hz")
    last_frequency: float | None = Field(default=None, gt=0, description="steady state / PSD: last frequency, Hz")
    frequency_increments: int = Field(default=1, ge=1, le=100000, description="steady state / PSD: increments")
    add_modal_frequencies: bool = Field(default=False, description="steady state / PSD: add the modal frequencies")
    specified_frequencies: list[float] = Field(default=[], max_length=1000, description=(
        "steady state / PSD: extra frequencies, Hz"))
    hysteretic_mass: float | None = Field(default=None, ge=0, description=(
        "steady state / PSD: constant hysteretic damping, mass coefficient (omitted with the stiffness coefficient: "
        "the program's default)"))
    hysteretic_stiffness: float | None = Field(default=None, ge=0, description=(
        "steady state / PSD: constant hysteretic damping, stiffness coefficient (e.g. 2 x damping ratio; omitted: "
        "the program's default[[E:, 0.05 in ETABS]])"))
    base_case: str | None = Field(default=None, description=(
        "hyperstatic: the linear static case (e.g. post-tensioning) whose secondary (hyperstatic) forces are wanted"))
    replace: bool = Field(default=False, description="change an existing case")

    @model_validator(mode="after")
    def _check(self) -> "AdvCaseP":
        t = self.type
        if t == "hyperstatic":
            if not self.base_case:
                raise ValueError("hyperstatic needs base_case")
            return self
        if not self.loads:
            raise ValueError(f"{t} needs loads")
        for ld in self.loads:
            if ld.kind == "acceleration" and ld.name not in ACCEL:
                raise ValueError(f"acceleration name is one of {', '.join(ACCEL)}")
            if t != "buckling" and not ld.function:
                raise ValueError(f"{t} loads need a function")
        if t in TH and (self.steps is None or self.step_size is None):
            raise ValueError("time history needs steps and step_size")
        if t == "nonlinear_modal_history" and not self.modal_case:
            raise ValueError("nonlinear_modal_history (FNA) needs modal_case")
        if t in FREQ and (self.first_frequency is None or self.last_frequency is None):
            raise ValueError(f"{t} needs first_frequency and last_frequency")
        if t in FREQ and self.add_modal_frequencies and not self.modal_case:
            raise ValueError("add_modal_frequencies needs modal_case")
        if self.damping and self.damping.method == "constant" and t != "nonlinear_modal_history":
            raise ValueError("constant (modal) damping is for nonlinear_modal_history; direct integration takes "
                             "mass_stiffness / periods / frequencies")
        if self.damping and self.damping.method != "constant" and t == "nonlinear_modal_history":
            raise ValueError("nonlinear_modal_history takes constant damping")
        return self


def _lists(p: AdvCaseP) -> tuple[list[str], list[str], list[float]]:
    kinds = ["Load" if ld.kind == "load_pattern" else "Accel" for ld in p.loads]
    names = [ld.name for ld in p.loads]
    sfs = [ld.factor for ld in p.loads]
    return kinds, names, sfs


def _sap_define(sap: Any, p: AdvCaseP) -> None:
    lc, t = sap.LoadCases, p.type
    kinds, names, sfs = _lists(p)
    n = len(p.loads)
    if t == "hyperstatic":
        api_call("HyperStatic.SetCase", lc.HyperStatic.SetCase, p.name)
        api_call("HyperStatic.SetBaseCase", lc.HyperStatic.SetBaseCase, p.name, p.base_case)
        return
    if t == "buckling":
        b = lc.Buckling
        api_call("Buckling.SetCase", b.SetCase, p.name)
        api_call("Buckling.SetLoads", b.SetLoads, p.name, n, kinds, names, sfs)
        api_call("Buckling.SetParameters", b.SetParameters, p.name, p.modes, p.eigen_tolerance)
        if p.initial_case:
            api_call("Buckling.SetInitialCase", b.SetInitialCase, p.name, p.initial_case)
        return
    funcs = [ld.function or "" for ld in p.loads]
    csys, ang = ["Global"] * n, [ld.angle for ld in p.loads]
    if t in TH:
        obj = {"linear_direct_history": lc.DirHistLinear, "nonlinear_direct_history": lc.DirHistNonlinear,
               "nonlinear_modal_history": lc.ModHistNonlinear}[t]
        nm = {"linear_direct_history": "DirHistLinear", "nonlinear_direct_history": "DirHistNonlinear",
              "nonlinear_modal_history": "ModHistNonlinear"}[t]
        api_call(f"{nm}.SetCase", obj.SetCase, p.name)
        if t == "nonlinear_modal_history":
            api_call(f"{nm}.SetModalCase", obj.SetModalCase, p.name, p.modal_case)
        api_call(f"{nm}.SetLoads", obj.SetLoads, p.name, n, kinds, names, funcs, sfs,
                 [ld.time_factor for ld in p.loads], [ld.arrival_time for ld in p.loads], csys, ang)
        api_call(f"{nm}.SetTimeStep", obj.SetTimeStep, p.name, int(p.steps), float(p.step_size))
        if t == "nonlinear_modal_history":
            if p.damping:
                api_call(f"{nm}.SetDampConstant", obj.SetDampConstant, p.name, p.damping.ratio)
            return
        if p.integration:
            it = p.integration
            api_call(f"{nm}.SetTimeIntegration", obj.SetTimeIntegration, p.name, INTEGRATION[it.method], it.alpha,
                     it.beta, it.gamma, it.theta, it.alpha_m)
        if p.damping:
            d = p.damping
            code = {"mass_stiffness": 1, "periods": 2, "frequencies": 3}[d.method]
            p1, p2 = d.point_1 or [0.0, 0.0], d.point_2 or [0.0, 0.0]
            api_call(f"{nm}.SetDampProportional", obj.SetDampProportional, p.name, code, d.mass_coefficient,
                     d.stiffness_coefficient, p1[0], p2[0], p1[1], p2[1])
        if t == "nonlinear_direct_history":
            api_call(f"{nm}.SetGeometricNonlinearity", obj.SetGeometricNonlinearity, p.name,
                     GEO[p.geometric_nonlinearity])
        if p.initial_case:
            api_call(f"{nm}.SetInitialCase", obj.SetInitialCase, p.name, p.initial_case)
        return
    obj = lc.SteadyState if t == "steady_state" else lc.PSD
    nm = "SteadyState" if t == "steady_state" else "PSD"
    api_call(f"{nm}.SetCase", obj.SetCase, p.name)
    api_call(f"{nm}.SetLoads", obj.SetLoads, p.name, n, kinds, names, funcs, sfs, [ld.phase_angle for ld in p.loads],
             csys, ang)
    spec = list(p.specified_frequencies)
    api_call(f"{nm}.SetFreqData", obj.SetFreqData, p.name, float(p.first_frequency), float(p.last_frequency),
             p.frequency_increments, p.add_modal_frequencies, False, bool(spec), p.modal_case or "", 0, [], len(spec),
             spec)
    if p.hysteretic_mass is not None or p.hysteretic_stiffness is not None:
        api_call(f"{nm}.SetDampConstant", obj.SetDampConstant, p.name, p.hysteretic_mass or 0.0,
                 p.hysteretic_stiffness or 0.0)


def _yes(v: bool) -> str:
    return "Yes" if v else "No"


def _etabs_define(sap: Any, p: AdvCaseP) -> dict[str, Any]:
    t = p.type
    if t == "hyperstatic":
        lc = sap.LoadCases
        api_call("HyperStatic.SetCase", lc.HyperStatic.SetCase, p.name)
        api_call("HyperStatic.SetBaseCase", lc.HyperStatic.SetBaseCase, p.name, p.base_case)
        return {}
    key = ETABS_TABLE[t]
    rows: list[dict[str, Any]] = []
    for k, ld in enumerate(p.loads):
        r: dict[str, Any] = {"Name": p.name, "LoadType": "Load" if ld.kind == "load_pattern" else "Accel"}
        if t == "buckling":
            r["LoadName"] = ld.name if ld.kind == "load_pattern" else "Accel " + BUCKLING_ACCEL[ld.name]
        else:
            r["LoadName"] = ld.name
            r["Function"] = ld.function
        if ld.kind == "load_pattern":
            r["LoadSF"] = repr(ld.factor)
        else:
            r["TransAccSF" if ld.name[0] == "U" else "RotAccSF"] = repr(ld.factor)
        if t in TH:
            r.update(TimeFactor=repr(ld.time_factor), ArrivalTime=repr(ld.arrival_time), Angle=repr(ld.angle))
        if t in FREQ:
            r.update(PhaseAngle=repr(ld.phase_angle), Angle=repr(ld.angle))
        if k == 0:       # case-level fields on the first row
            if t == "buckling":
                r.update(NumModes=str(p.modes), EigenTol=repr(p.eigen_tolerance))
                if p.initial_case:
                    r.update(StiffType="Nonlinear Case", NonlinCase=p.initial_case)
            if t in TH:
                r.update(NumSteps=str(p.steps), StepSize=repr(p.step_size))
            if t in ("linear_direct_history", "nonlinear_direct_history"):
                if p.integration:
                    it = p.integration
                    r.update(IntType=INTEGRATION_TEXT[it.method], Gamma=repr(it.gamma), Beta=repr(it.beta),
                             Theta=repr(it.theta), Alpha=repr(it.alpha), Alpham=repr(it.alpha_m))
                if p.damping:
                    d = p.damping
                    if d.method == "mass_stiffness":
                        r.update(ProBy="Direct", MassCoeff=repr(d.mass_coefficient),
                                 StiffCoeff=repr(d.stiffness_coefficient))
                    else:
                        r.update(ProBy="Period" if d.method == "periods" else "Frequency",
                                 ProTimeVal1=repr(d.point_1[0]), ProDamping1=repr(d.point_1[1]),
                                 ProTimeVal2=repr(d.point_2[0]), ProDamping2=repr(d.point_2[1]))
            if t == "nonlinear_direct_history":
                r["GeoNonlin"] = GEO_TEXT[p.geometric_nonlinearity]
                if p.initial_case:
                    r.update(InitialCond="Nonlinear Case", NonlinCase=p.initial_case)
            if t == "nonlinear_modal_history":
                r["ModalCase"] = p.modal_case
            if t in FREQ:
                r.update(FirstFreq=repr(p.first_frequency), LastFreq=repr(p.last_frequency),
                         NumFreqInc=str(p.frequency_increments), AddMFreq=_yes(p.add_modal_frequencies),
                         AddMDev="No", AddSpFreq=_yes(bool(p.specified_frequencies)))
                if p.modal_case:
                    r["ModalCase"] = p.modal_case
        rows.append(r)
    if t in FREQ:
        for k, f in enumerate(p.specified_frequencies):
            if k < len(rows):
                rows[k]["SpecFreq"] = repr(f)
            else:
                rows.append({"Name": p.name, "SpecFreq": repr(f)})
    out = {"table_edit": edit_table(sap, key, rows, drop=lambda r: r.get("Name") == p.name)}
    if t == "nonlinear_modal_history" and p.damping:
        out["damping_table_edit"] = edit_table(
            sap, MODAL_DAMPING, [{"Name": p.name, "DampType": "Constant", "ConstDamp": repr(p.damping.ratio)}],
            drop=lambda r: r.get("Name") == p.name)
    return out


def read_case(sap: Any, sw: str, name: str) -> dict[str, Any]:
    t = _try(sap, "LoadCases.GetTypeOAPI_1", name)
    code = int(t[0]) if t else None
    kind = {v: k for k, v in TYPE_CODE.items()}.get(code, code)
    out: dict[str, Any] = {"name": name, "type": kind}
    if kind == "hyperstatic":
        b = _try(sap, "LoadCases.HyperStatic.GetBaseCase", name)
        out["base_case"] = str(b[0]) if b else None
        return out
    if sw != "SAP2000":
        key = ETABS_TABLE.get(kind) if isinstance(kind, str) else None
        if key:
            _, _, rows = read_table(sap, key)
            out["rows"] = [{k: v for k, v in r.items() if v not in ("", None) and k != "GUID"}
                           for r in rows if r.get("Name") == name]
        if kind in FREQ:
            _, _, rows = read_table(sap, HYST_DAMPING)
            for r in rows:
                if r.get("Name") == name and r.get("DampType") == "Constant":
                    out.update(hysteretic_mass=float(r.get("MassCoeff") or 0),
                               hysteretic_stiffness=float(r.get("StiffCoeff") or 0))
        if kind == "nonlinear_modal_history":
            _, _, rows = read_table(sap, MODAL_DAMPING)
            out["damping"] = [{k: v for k, v in r.items() if v} for r in rows if r.get("Name") == name]
        if kind in FREQ:
            _, _, rows = read_table(sap, HYST_DAMPING)
            out["damping"] = [{k: v for k, v in r.items() if v} for r in rows if r.get("Name") == name]
        return out
    base = {"buckling": "Buckling", "linear_direct_history": "DirHistLinear",
            "nonlinear_direct_history": "DirHistNonlinear", "nonlinear_modal_history": "ModHistNonlinear",
            "steady_state": "SteadyState", "power_spectral_density": "PSD"}.get(kind)
    if not base:
        return out
    g = _try(sap, f"LoadCases.{base}.GetLoads", name)
    if g:
        if kind == "buckling":
            out["loads"] = [{"type": str(a), "name": str(b), "factor": float(c)}
                            for a, b, c in zip(as_list(g[1]), as_list(g[2]), as_list(g[3]))]
            pr = _try(sap, "LoadCases.Buckling.GetParameters", name)
            if pr:
                out.update(modes=int(pr[0]), eigen_tolerance=float(pr[1]))
        else:
            out["loads"] = [{"type": str(a), "name": str(b), "function": str(c), "factor": float(d)}
                            for a, b, c, d in zip(as_list(g[1]), as_list(g[2]), as_list(g[3]), as_list(g[4]))]
    ini = _try(sap, f"LoadCases.{base}.GetInitialCase", name)
    if ini:
        out["initial_case"] = str(ini[0])
    if kind in TH:
        ts = _try(sap, f"LoadCases.{base}.GetTimeStep", name)
        if ts:
            out.update(steps=int(ts[0]), step_size=float(ts[1]))
    if kind in ("linear_direct_history", "nonlinear_direct_history"):
        ti = _try(sap, f"LoadCases.{base}.GetTimeIntegration", name)
        if ti:
            inv = {v: k for k, v in INTEGRATION.items()}
            out["integration"] = {"method": inv.get(int(ti[0]), ti[0]), "alpha": float(ti[1]), "beta": float(ti[2]),
                                  "gamma": float(ti[3]), "theta": float(ti[4]), "alpha_m": float(ti[5])}
        dp = _try(sap, f"LoadCases.{base}.GetDampProportional", name)
        if dp:
            out["damping"] = {"method": {1: "mass_stiffness", 2: "periods", 3: "frequencies"}.get(int(dp[0]), dp[0]),
                              "mass_coefficient": float(dp[1]), "stiffness_coefficient": float(dp[2]),
                              "point_1": [float(dp[3]), float(dp[5])], "point_2": [float(dp[4]), float(dp[6])]}
    if kind == "nonlinear_direct_history":
        gn = _try(sap, "LoadCases.DirHistNonlinear.GetGeometricNonlinearity", name)
        if gn:
            out["geometric_nonlinearity"] = {v: k for k, v in GEO.items()}.get(int(gn[0]), gn[0])
    if kind == "nonlinear_modal_history":
        mc = _try(sap, "LoadCases.ModHistNonlinear.GetModalCase", name)
        dc = _try(sap, "LoadCases.ModHistNonlinear.GetDampConstant", name)
        out.update(modal_case=str(mc[0]) if mc else None, damping_ratio=float(dc[0]) if dc else None)
    if kind in FREQ:
        fd = _try(sap, f"LoadCases.{base}.GetFreqData", name)
        if fd:
            out.update(first_frequency=float(fd[0]), last_frequency=float(fd[1]), frequency_increments=int(fd[2]),
                       add_modal_frequencies=bool(fd[3]), modal_case=str(fd[6]),
                       specified_frequencies=[float(v) for v in as_list(fd[10])])
        dc = _try(sap, f"LoadCases.{base}.GetDampConstant", name)
        if dc:
            out.update(hysteretic_mass=float(dc[0]), hysteretic_stiffness=float(dc[1]))
    return out


@action("define_advanced_load_case", "Create or change a [[ES:buckling, direct-integration time-history (linear or "
        "nonlinear: steps, damping, integration, geometric nonlinearity, initial case), nonlinear modal (FNA) "
        "time-history, steady-state, ]][[S:power-spectral-density, ]][[ES:or ]]hyperstatic load case (secondary forces of a linear "
        "static case, e.g. post-tensioning[[EF:; ETABS / SAFE compute them from tendon loads only - a base case without "
        "tendons gives no results]]). Every value from the designer.[[E: ETABS takes all but hyperstatic "
        "through its load case tables; its PSD table import makes a steady-state case (measured), so no PSD here; its "
        "steady-state cases keep the program's hysteretic damping (stiffness coefficient 0.05, read back; its table "
        "import is refused) and their results are read with query_database_table 'Joint Displacements' (Real / Imag "
        "rows).]] "
        "Answers with the case read back.", "Definitions", "edit", AdvCaseP,
        options={"type": {"buckling": "ES", "linear_direct_history": "ES", "nonlinear_direct_history": "ES",
                          "nonlinear_modal_history": "ES", "steady_state": "ES", "power_spectral_density": "S"}},
        fields={"loads": "ES", "modes": "ES", "eigen_tolerance": "ES", "initial_case": "ES", "steps": "ES",
                "step_size": "ES", "damping": "ES", "integration": "ES", "geometric_nonlinearity": "ES",
                "modal_case": "ES", "first_frequency": "ES", "last_frequency": "ES", "frequency_increments": "ES",
                "add_modal_frequencies": "ES", "specified_frequencies": "ES", "hysteretic_mass": "S",
                "hysteretic_stiffness": "S"})
def define_advanced_load_case(p: AdvCaseP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    cases = bridge.try_name_list(sap, ["LoadCases"])[0] or []
    existed = p.name in cases
    if existed and not p.replace:
        raise CSIValidationError(f"Load case '{p.name}' exists.", hint="replace: true changes it.")
    if existed:
        t = _try(sap, "LoadCases.GetTypeOAPI_1", p.name)
        if t and int(t[0]) != TYPE_CODE[p.type]:
            raise CSIValidationError(f"Load case '{p.name}' is another type (code {int(t[0])}).",
                                     hint="Use a new name, or delete that case in the program first.")
    pats = set(bridge.try_name_list(sap, ["LoadPatterns"])[0] or [])
    missing = [ld.name for ld in p.loads if ld.kind == "load_pattern" and ld.name not in pats]
    if missing:
        raise CSIValidationError(f"Load patterns not defined: {missing}.")
    o = _try(sap, "Func.GetNameList")
    funcs = set(str(x) for x in as_list(o[1])) if o else set()
    nofunc = sorted({ld.function for ld in p.loads if ld.function and ld.function not in funcs})
    if nofunc:
        raise CSIValidationError(f"Functions not defined: {nofunc}.", hint="define_function makes one.")
    for ref, what in ((p.initial_case, "Initial case"), (p.modal_case, "Modal case"), (p.base_case, "Base case")):
        if ref and ref not in cases:
            raise CSIValidationError(f"{what} '{ref}' is not defined.", hint=f"Cases: {cases[:30]}")
    if p.type == "hyperstatic":
        bt = _try(sap, "LoadCases.GetTypeOAPI_1", p.base_case)
        if bt and int(bt[0]) != 1:
            raise CSIValidationError(f"Base case '{p.base_case}' is not a linear static case.")
    plan = {"case": p.name, "type": p.type, "existed": existed,
            **p.model_dump(exclude={"software", "units", "allow_unlock", "dry_run", "snapshot", "name", "type",
                                    "replace"}, exclude_none=True, exclude_defaults=True)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_case", f"define a {p.type} case")
        extra = _sap_define(sap, p) if p.software == "SAP2000" else _etabs_define(sap, p)
        back = read_case(sap, p.software, p.name)
    out = {**plan, "read_back": back, **(extra or {}), "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if back.get("type") != p.type:
        out["message"] = f"The case reads back as {back.get('type')}, not {p.type}."
        raise ActionFailed(out)
    return out
