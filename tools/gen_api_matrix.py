r"""Phase 0: CSI API capability matrix, generated from the type libraries installed on THIS PC.

    tools\run_api_matrix.bat                 static (type libraries) + runtime probe (starts each program hidden)
    gen_api_matrix.py --static-only          type libraries only (no program started)

Writes
    docs/api/<lib>.json          every interface -> method -> parameters (direction, type, default) and properties
    docs/api/methods.csv         interface, member, kind, in ETABS / SAP2000 / SAFE, signature
    docs/api/runtime_probe.json  per program: which SapModel sub-objects answer at run time (2 levels)
    docs/API_MATRIX.md           summary per interface (counts per program, runtime status)

Libraries: ETABSv1 (ETABS), SAP2000v1 (SAP2000), SAFEv1 (SAFE), CSiAPIv1 (cross-product). Nothing is changed in any
model: the runtime probe uses a NEW hidden instance with a blank model and closes it without saving.
"""
from __future__ import annotations

import csv
import importlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
OUT = ROOT / "docs" / "api"
LIBS = {"ETABS": ("ETABSv1.Helper", "ETABSv1"), "SAP2000": ("SAP2000v1.Helper", "SAP2000v1"),
        "SAFE": ("SAFEv1.Helper", "SAFEv1"), "CSiAPI": ("CSiAPIv1.Helper", "CSiAPIv1")}
PROGRAMS = ("ETABS", "SAP2000", "SAFE")

PARAM_IN, PARAM_OUT, PARAM_OPT = 1, 2, 16


def _type_name(t) -> str:
    name = getattr(t, "__name__", repr(t))
    ref = name.startswith("LP_")
    base = name
    while base.startswith("LP_"):
        base = base[3:]
    array = base.startswith("SAFEARRAY_")
    if array:
        base = base[len("SAFEARRAY_"):]
    base = {"c_double": "double", "c_long": "int", "c_int": "int", "BSTR": "string", "VARIANT_BOOL": "bool",
            "c_short": "short", "c_float": "float", "c_ubyte": "byte", "VARIANT": "variant"}.get(base, base)
    if array:
        base += "[]"
    return ("ref " if ref else "") + base


def _iface(type_text: str) -> str:
    """'POINTER(cAnalyze)' / 'ref cAnalyze' -> 'cAnalyze'."""
    return type_text.replace("POINTER(", "").replace(")", "").replace("ref ", "").strip()


def dump_library(lib: str) -> dict:
    import comtypes
    mod = importlib.import_module(f"comtypes.gen.{lib}")
    out: dict = {}
    for name in sorted(dir(mod)):
        obj = getattr(mod, name)
        specs = getattr(obj, "_methods_", None)
        if not isinstance(obj, type) or not issubclass(obj, comtypes.IUnknown) or not specs:
            continue
        methods: dict = {}
        props: dict = {}
        for spec in specs:
            restype, mname, argtypes, paramflags, idlflags = spec[0], spec[1], spec[2], spec[3], spec[4]
            params = []
            for i, pf in enumerate(paramflags or ()):
                flags, pname = pf[0], (pf[1] if len(pf) > 1 else f"p{i}")
                entry = {"name": pname, "type": _type_name(argtypes[i]) if i < len(argtypes) else "?",
                         "dir": "inout" if flags & PARAM_IN and flags & PARAM_OUT else
                                "out" if flags & PARAM_OUT else "in"}
                if flags & PARAM_OPT or len(pf) > 2:
                    entry["optional"] = True
                    if len(pf) > 2:
                        entry["default"] = repr(pf[2])
                params.append(entry)
            if "propget" in idlflags:
                ret = (params[-1]["type"] if params else "?")
                pname = mname[5:] if mname.startswith("_get_") else mname
                props[pname] = _iface(_type_name(argtypes[-1]) if argtypes else ret)
            elif "propput" in idlflags or "propputref" in idlflags or mname.startswith(("_set_", "_get_")):
                continue
            else:
                methods[mname] = params
        out[name] = {"methods": methods, "properties": props}
    return out


def signature(params: list[dict]) -> str:
    parts = []
    for p in params:
        if p["dir"] == "out" and p["name"] in ("pVal", "pRetVal"):
            continue
        s = ("ref " if p["dir"] != "in" and not p["type"].startswith("ref") else "") + f"{p['type']} {p['name']}"
        if p.get("optional"):
            s += f" = {p['default']}" if "default" in p else " (opt)"
        parts.append(s)
    return "(" + ", ".join(parts) + ")"


def static() -> dict:
    import comtypes.client
    data = {}
    for key, (helper, lib) in LIBS.items():
        try:
            comtypes.client.CreateObject(helper)   # makes comtypes generate comtypes.gen.<lib>
            data[key] = dump_library(lib)
            print(f"{key}: {len(data[key])} interfaces, "
                  f"{sum(len(v['methods']) for v in data[key].values())} methods", flush=True)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{key}: type library not available ({exc})", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for key, lib_data in data.items():
        (OUT / f"{LIBS[key][1]}.json").write_text(json.dumps(lib_data, indent=1), encoding="utf-8")
    rows = {}
    for prog in PROGRAMS:
        for iface, d in data.get(prog, {}).items():
            for m, params in d["methods"].items():
                rows.setdefault((iface, m, "method"), {"sig": signature(params)})[prog] = True
            for p, t in d["properties"].items():
                rows.setdefault((iface, p, "property"), {"sig": t})[prog] = True
    with (OUT / "methods.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["interface", "member", "kind", "ETABS", "SAP2000", "SAFE", "signature"])
        for (iface, m, kind), r in sorted(rows.items()):
            w.writerow([iface, m, kind, *("x" if r.get(p) else "" for p in PROGRAMS), r["sig"]])
    print(f"methods.csv: {len(rows)} members", flush=True)
    return data


def runtime(data: dict) -> dict:
    """Start each program hidden with a blank model; read every SapModel property and their properties."""
    import server  # noqa: F401 - COM thread + bridge, as the MCP uses them
    from actions import bridge
    from csi_bridge import UNIT_PRESETS, api_call
    probe: dict = {}
    for prog in PROGRAMS:
        if prog not in data:
            continue
        lib = data[prog]
        t0 = time.perf_counter()

        def job(prog=prog, lib=lib) -> dict:
            conn = bridge.launch_new(prog, visible=False)
            res: dict = {"started_via": conn.attached_via}
            try:
                sap = conn.sap_model
                api_call("InitializeNewModel", sap.InitializeNewModel, UNIT_PRESETS["kN_m_C"])
                try:
                    api_call("File.NewBlank", sap.File.NewBlank)
                except Exception as exc:  # noqa: BLE001
                    res["NewBlank"] = str(exc)
                level1: dict = {}
                for prop, iface in sorted(lib.get("cSapModel", {}).get("properties", {}).items()):
                    try:
                        child = getattr(sap, prop)
                        entry: dict = {"ok": bool(child), "interface": iface}
                        sub = {}
                        for p2 in sorted(lib.get(iface, {}).get("properties", {})):
                            try:
                                sub[p2] = bool(getattr(child, p2))
                            except Exception as exc:  # noqa: BLE001
                                sub[p2] = f"error: {type(exc).__name__}: {exc}"[:160]
                        if sub:
                            entry["children"] = sub
                    except Exception as exc:  # noqa: BLE001
                        entry = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
                    level1[prop] = entry
                res["sapmodel"] = level1
            finally:
                bridge.disconnect(prog, True, False)
            return res

        try:
            probe[prog] = server.executor.run(job)
            ok = sum(1 for v in probe[prog]["sapmodel"].values() if v.get("ok"))
            print(f"{prog}: runtime {ok}/{len(probe[prog]['sapmodel'])} SapModel objects answer "
                  f"({time.perf_counter() - t0:.0f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            probe[prog] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"{prog}: runtime probe failed: {exc}", flush=True)
    (OUT / "runtime_probe.json").write_text(json.dumps(probe, indent=1), encoding="utf-8")
    import server as srv
    srv.executor.shutdown(bridge.release_all)
    return probe


def summary(data: dict, probe: dict) -> None:
    ifaces = sorted({i for p in PROGRAMS for i in data.get(p, {})})
    sap_props = {p: data.get(p, {}).get("cSapModel", {}).get("properties", {}) for p in PROGRAMS}
    iface_to_prop = {iface: prop for p in PROGRAMS for prop, iface in sap_props[p].items()}
    lines = ["# CSI API capability matrix", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')} by tools/gen_api_matrix.py from the type libraries "
             "installed on this PC. Full member list with signatures: [api/methods.csv](api/methods.csv); per "
             "library: api/<lib>.json; run-time probe: api/runtime_probe.json.", "",
             "Methods per interface in each program's library, and whether the SapModel property that returns it "
             "answered at run time (blank model, hidden instance). A method present in SAFE's library is not proof "
             "SAFE implements it: check the run-time column and test before relying on it.", "",
             "| interface | SapModel property | ETABS | SAP2000 | SAFE | runtime E / S / F |", "|---|---|---|---|---|---|"]
    for iface in ifaces:
        counts = [len(data.get(p, {}).get(iface, {}).get("methods", {})) or "" for p in PROGRAMS]
        prop = iface_to_prop.get(iface, "")
        rt = []
        for p in PROGRAMS:
            e = probe.get(p, {}).get("sapmodel", {}).get(prop) if prop else None
            rt.append("" if e is None else ("ok" if e.get("ok") else "no"))
        lines.append(f"| {iface} | {prop} | {counts[0]} | {counts[1]} | {counts[2]} | {' / '.join(rt) if prop else ''} |")
    for p in PROGRAMS:
        lines += ["", f"- {p}: {len(data.get(p, {}))} interfaces, "
                      f"{sum(len(v['methods']) for v in data.get(p, {}).values())} methods"]
    (ROOT / "docs" / "API_MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("docs/API_MATRIX.md written", flush=True)


if __name__ == "__main__":
    data = static()
    probe = {} if "--static-only" in sys.argv else runtime(data)
    if not probe and (OUT / "runtime_probe.json").exists():
        probe = json.loads((OUT / "runtime_probe.json").read_text(encoding="utf-8"))
    summary(data, probe)
    print("RESULT=OK")
