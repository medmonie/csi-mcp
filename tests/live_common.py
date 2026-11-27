"""Shared helpers for the live tests: call actions in-process exactly as the MCP tool does, print compact results."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import server  # noqa: E402
from actions import ActionFailed, bridge  # noqa: E402
from csi_bridge import api_call  # noqa: E402

FAILED: list[str] = []
LIMIT = 3500


def com(fn, *args, **kwargs):
    """Run a raw function on the COM thread (test setup only)."""
    return server.executor.run(fn, *args, **kwargs)


def show(title: str, action: str, params: dict | None = None, *, expect_error: bool = False, limit: int = LIMIT):
    print(f"===== {title}", flush=True)
    t0 = time.perf_counter()
    try:
        out = server.executor.run(server.dispatch, action, params)
    except Exception as exc:  # noqa: BLE001
        dt = time.perf_counter() - t0
        detail = exc.payload if isinstance(exc, ActionFailed) else {"error": f"{type(exc).__name__}: {exc}",
                                                                     "hint": getattr(exc, "hint", "")}
        text = json.dumps(detail, indent=1, default=str)
        print(("EXPECTED ERROR" if expect_error else "FAILED") + f" ({dt:.1f}s): " + text[:limit], flush=True)
        if not expect_error:
            FAILED.append(title)
        return exc
    dt = time.perf_counter() - t0
    text = json.dumps(out, indent=1, default=str)
    print(f"ok ({dt:.1f}s): " + (text if len(text) <= limit else text[:limit] + "\n... (truncated)"), flush=True)
    if expect_error:
        FAILED.append(title + " (an error was expected)")
    return out


def check(title: str, condition: bool, detail: object = "") -> None:
    print(f"CHECK {title}: {'ok' if condition else 'FAILED'} {detail}", flush=True)
    if not condition:
        FAILED.append("check: " + title)


def finish() -> int:
    server.executor.shutdown(bridge.release_all)
    print("\nRESULT=OK" if not FAILED else "\nRESULT=FAILED: " + "; ".join(FAILED), flush=True)
    return 0 if not FAILED else 1


def build_template(sw: str) -> dict:
    """Test set-up through the raw API (not an MCP action): a small model to work on."""
    sap = bridge.sap_model(sw)
    info: dict = {}
    api_call("InitializeNewModel", sap.InitializeNewModel, 6)  # kN_m_C
    if sw == "SAP2000":
        api_call("File.New2DFrame", sap.File.New2DFrame, 0, 2, 3.0, 2, 6.0)   # portal: 2 storeys x 3 m, 2 bays x 6 m
        info["brace"] = ([0, 0, 0], [6, 0, 3])
    elif sw == "ETABS":
        api_call("File.NewSteelDeck", sap.File.NewSteelDeck, 3, 3.5, 4.0, 3, 3, 6.0, 6.0)
        info["brace"] = ([0, 0, 0], [6, 0, 4])
    else:
        info["file_methods"] = sorted(m for m in dir(sap.File) if not m.startswith("_"))[:60]
        api_call("File.NewBlank", sap.File.NewBlank)
        info["brace"] = ([0, 0, 0], [6, 0, 0])
    cases = bridge.name_list(sap, "LoadCases")
    patterns = bridge.name_list(sap, "LoadPatterns")
    info.update(cases=cases, patterns=patterns, sections=bridge.try_name_list(sap, ["PropFrame"])[0] or [])
    if patterns:
        dead = patterns[0]
        api_call("RespCombo.Add", sap.RespCombo.Add, "MCP_COMB1", 0)
        api_call("RespCombo.SetCaseList", sap.RespCombo.SetCaseList, "MCP_COMB1", 0, dead, 1.4)
        info["combo"] = "MCP_COMB1"
        info["dead"] = dead
    return info


def frame_points(sw: str, frame: str) -> list:
    sap = bridge.sap_model(sw)
    return api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, frame, "", "")[:2]
