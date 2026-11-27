"""Offline tests: every action through the MCP layer, against the fake CSI model (no Windows / CSI needed).

    server\\.venv\\Scripts\\python -m pytest tests -q        (run_offline.bat writes tests\\results\\offline.txt)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))

import catalog  # noqa: E402
import server  # noqa: E402
from actions import ACTIONS, bridge  # noqa: E402
from fake_csi import FakeApi  # noqa: E402
from mcp.types import CallToolResult  # noqa: E402


def call(action, params=None, read=True, edit=True):
    out = asyncio.run(server.run_action(None, action, params, read, edit))
    if isinstance(out, CallToolResult):
        return {"_error": True, **(out.structuredContent or {})}
    return out


def fresh(software="ETABS"):
    api = FakeApi()
    bridge.attach_external(software, api)
    return api.SapModel


def test_catalogue_and_help():
    text = catalog.tool_description("all")
    for name in ACTIONS:
        assert f"- {name}" in text
    assert "allow_unlock?" in text and "DESIGN: Claude never chooses" in text
    h = call("help", {"topic": "assign_point_or_frame_load"})
    p = h["assign_point_or_frame_load"]["params"]
    assert p["target_type"]["one_of"] == ["point", "frame"] and p["replace"]["default"] is True
    assert set(catalog.help_data()["readActions"]) >= {"get_model_status", "query_database_table"}
    assert "help" not in ACTIONS
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## " + re.escape(catalog.SERVER_VERSION) + r"\b", changelog, re.M), "CHANGELOG not in step"


def test_validation_and_split():
    fresh()
    r = call("get_model_status", {"software": "ETABS", "colour": "red"})
    assert r["_error"] and r["error_type"] == "InvalidParameters" and "colour" in r["message"]
    r = call("get_model_status", {"software": "Tekla"})
    assert r["_error"] and "software" in r["message"]
    r = call("nope")
    assert r["_error"] and "Unknown action" in r["message"]
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [1, 0, 0],
                                      "section_name": "B300x600"}, edit=False)
    assert r["_error"] and "csi_edit" in r["message"]
    r = call("get_model_status", {"software": "ETABS"}, read=False)
    assert r["_error"] and "csi_read" in r["message"]


def test_status_and_definitions():
    sap = fresh()
    st = call("get_model_status", {"software": "ETABS"})
    assert st["version"] == "22.1.0" and st["object_counts"]["frames"] == 3 and st["object_counts"]["stories"] == 3
    d = call("list_definitions", {"software": "ETABS"})
    assert d["load_patterns"]["items"][1] == {"name": "Live", "type": "Live"}
    assert d["load_cases"]["items"][2]["type"] == "Modal"
    assert sap.units == 6
    progs = call("list_programs")
    assert progs["programs"]["ETABS"]["reachable"] is True


def test_units_scope_restores_and_set_units_persists():
    sap = fresh()
    sap.units = 4
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600"})
    assert r["frame_name"] == "F4" and r["story"] == "Story1"
    assert sap.FrameObj.created[-1][1] == 6 and sap.units == 4
    sets = []
    orig = sap.SetPresentUnits
    sap.SetPresentUnits = lambda c: (sets.append(c), orig(c))[1]
    call("get_model_status", {"software": "ETABS"})
    call("list_definitions", {"software": "ETABS"})
    call("query_database_table", {"software": "ETABS", "table_key": "Load Pattern Definitions", "units": "kip_ft_F"})
    assert sets == []                       # already in kip_ft_F: units never touched
    r = call("set_units", {"software": "ETABS", "unit_preset": "Ton_m_C"})
    assert sap.units == 12 and r["previous_units"] == "kip_ft_F"


def test_lock_guard_never_silent():
    sap = fresh()
    sap.locked = True
    args = {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [0, 0, 3], "section_name": "C500x500"}
    r = call("create_frame_element", args)
    assert r["_error"] and r["status"] == "confirmation_required" and sap.locked
    r = call("create_frame_element", {**args, "allow_unlock": True})
    assert r["model_was_unlocked"] and not sap.locked


def test_bad_section_zero_length():
    fresh()
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [1, 0, 0],
                                      "section_name": "B300x60"})
    assert r["_error"] and "B300x600" in r["hint"]
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [1, 1, 1], "end_xyz": [1, 1, 1],
                                      "section_name": "B300x600"})
    assert r["_error"] and r["error_type"] == "CSIValidationError"


def test_loads():
    sap = fresh()
    r = call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "point", "object_names": ["1", "99"],
                                            "load_pattern": "Live", "values": [50]})
    assert r["_error"] and r["assigned"] == ["1"] and r["failed"][0]["object"] == "99"
    assert sap.PointObj.loads[("1", "Live")][0] == [0, 0, -50.0, 0, 0, 0]
    r = call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "frame", "object_names": ["F1"],
                                            "load_pattern": "Dead", "values": [10, 20], "direction": "Local2"})
    assert sap.FrameObj.dist[("F1", "Dead")][:7] == (1, 2, 0.0, 1.0, 10.0, 20.0, "Local")
    r = call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "frame", "object_names": ["F1"],
                                            "load_pattern": "Wind", "values": [1]})
    assert r["_error"] and "Wind" in r["message"]


def test_analysis_tables_and_summaries():
    sap = fresh()
    r = call("get_analysis_summary", {"software": "ETABS", "result_type": "base_reactions"})
    assert r["_error"] and "run_analysis" in r["hint"]
    r = call("run_analysis", {"software": "ETABS"})
    assert sap.saved == 1 and r["case_status_counts"] == {"Finished": 3}
    t = call("list_available_tables", {"software": "ETABS", "search_term": "drift"})
    assert t["matches"] == 1 and t["tables"][0]["key"] == "Story Drifts"
    q = call("query_database_table", {"software": "ETABS", "table_key": "story drifts",
                                      "load_cases_or_combos": ["DCON1", "ENV"],
                                      "filters": {"Direction": "X", "Drift": ">=0.002"}, "max_rows": 2,
                                      "sort_by": "Drift"})
    assert q["total_rows"] == 24 and q["rows_after_filters"] == 4 and q["returned_rows"] == 2 and q["truncated"]
    assert q["records"][0]["Drift"] == 0.0034 and q["numeric_summary"]["Drift"]["max_at"]["Story"] == "Story2"
    assert sap.DatabaseTables.sel_cases == ["Dead"] and set(sap.DatabaseTables.read_units) == {6}
    bad = call("query_database_table", {"software": "ETABS", "table_key": "Story Drift"})
    assert bad["_error"] and "Story Drifts" in bad["hint"]
    bad = call("query_database_table", {"software": "ETABS", "table_key": "Story Drifts", "filters": {"Storey": "x"}})
    assert bad["_error"] and "Story" in bad["hint"]
    s = call("get_analysis_summary", {"software": "ETABS", "result_type": "story_drifts", "drift_limit": 0.003})
    assert s["summary"]["governing"]["value"] == 0.0034 and s["summary"]["stories_exceeding_limit"] == ["Story2"]
    assert [e["story"] for e in s["summary"]["story_envelope"]] == ["Story3", "Story2", "Story1"]
    s = call("get_analysis_summary", {"software": "ETABS", "result_type": "base_reactions"})
    assert s["summary"]["governing"]["FZ"]["max"]["value"] == 8196.6
    assert s["summary"]["governing"]["FX"]["min"]["value"] == 0 and s["summary"]["modal_rows_excluded"] == 1
    m = call("get_analysis_summary", {"software": "ETABS", "result_type": "modal_periods"})["summary"]["Modal"]
    assert m["fundamental_period"] == 1.25 and m["dominant_modes"]["UY"]["mode"] == 2
    assert m["cumulative_mass"]["SumUX"]["mode_reaching_90pct"] == 4
    s = call("get_analysis_summary", {"software": "ETABS", "result_type": "joint_displacements"})
    assert s["summary"]["governing"]["Uz"]["abs_max"]["value"] == -0.02 and s["rows_analysed"] == 2
    json.dumps(s)  # JSON-safe


def test_sap2000_story_drift_refused():
    sap = fresh("SAP2000")
    sap.analysed = True
    r = call("get_analysis_summary", {"software": "SAP2000", "result_type": "story_drifts"})
    assert r["_error"] and "joint_displacements" in r["hint"]


def test_open_save_validation():
    sap = fresh()
    sap.filename = "(Untitled)"
    assert call("get_model_status", {"software": "ETABS"})["model_file"] is None
    r = call("run_analysis", {"software": "ETABS"})
    assert r["_error"] and "file_path" in r["hint"]
    sap.filename = os.path.join(tempfile.gettempdir(), "noext")
    assert call("open_or_save_model", {"software": "ETABS", "action": "save"})["saved"].endswith("noext.edb")
    r = call("open_or_save_model", {"software": "ETABS", "action": "open", "file_path": r"C:\nope\x.edb"})
    assert r["_error"]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.sdb")
        open(p, "w").close()
        r = call("open_or_save_model", {"software": "ETABS", "action": "open", "file_path": p})
        assert r["_error"] and ".edb" in r["message"]
        r = call("open_or_save_model", {"software": "ETABS", "action": "save", "file_path": os.path.join(d, "n.edb")})
        assert r["saved"].endswith("n.edb")


def test_client_config_merge(tmp_path, monkeypatch):
    import client_config
    appdata = tmp_path / "Roaming"
    (appdata / "Claude").mkdir(parents=True)
    cfg = appdata / "Claude" / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"revit": {"command": "r.exe"}}, "other": 1}), encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert client_config.run({"command": "py.exe", "args": ["server.py"]}) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["revit"] == {"command": "r.exe"} and data["mcpServers"]["csi"]["args"] == ["server.py"]
    assert data["other"] == 1
    assert client_config.run(None) == 0
    assert "csi" not in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_tool_registration():
    tools = asyncio.run(server.create_server().list_tools())
    assert [t.name for t in tools] == ["csi"] and tools[0].inputSchema["additionalProperties"] is False
    os.environ["CSI_MCP_SPLIT"] = "1"
    try:
        tools = asyncio.run(server.create_server().list_tools())
        assert [t.name for t in tools] == ["csi_read", "csi_edit"] and tools[0].annotations.readOnlyHint
    finally:
        del os.environ["CSI_MCP_SPLIT"]


def test_selection_restored_and_skipped_when_unchanged():
    sap = fresh("SAFE")
    sap.analysed = True
    calls = []
    orig = sap.DatabaseTables.SetLoadCasesSelectedForDisplay
    sap.DatabaseTables.SetLoadCasesSelectedForDisplay = lambda names: (calls.append(list(names)), orig(names))[1]
    q = call("query_database_table", {"software": "SAFE", "table_key": "Base Reactions",
                                      "load_cases_or_combos": ["DCON1"]})
    assert all(r["OutputCase"] == "DCON1" for r in q["records"]) and q["rows_after_filters"] == 1
    assert calls == [[], ["Dead"]] and sap.DatabaseTables.sel_cases == ["Dead"]     # set, then put back
    calls.clear()
    sap.DatabaseTables.sel_combos = []
    call("query_database_table", {"software": "SAFE", "table_key": "Base Reactions", "load_cases_or_combos": ["Dead"]})
    assert calls == []                                                              # already selected: untouched


# --------------------------------------------------------------------------- 0.2.1 audit fixes

def test_empty_selection_restore_is_a_warning_not_a_failure():
    """SAFE 23.3: an empty selection reads back as code 1 / count 0 and an empty list cannot be set again."""
    sap = fresh("SAFE")
    sap.analysed = True
    t = sap.DatabaseTables
    t.sel_cases = []
    t.GetLoadCasesSelectedForDisplay = lambda: [0, (), 1] if not t.sel_cases else [len(t.sel_cases), tuple(t.sel_cases), 0]
    orig = t.SetLoadCasesSelectedForDisplay
    t.SetLoadCasesSelectedForDisplay = lambda *a: [a[-1], 1] if not a[-1] else orig(a[-1])
    q = call("query_database_table", {"software": "SAFE", "table_key": "Base Reactions",
                                      "load_cases_or_combos": ["Dead"]})
    assert not q.get("_error"), q
    assert any("was empty" in w for w in q["warnings"])


def test_nonempty_selection_not_restored_is_an_error():
    sap = fresh("SAFE")
    sap.analysed = True
    t = sap.DatabaseTables
    calls = []
    def stuck(*a):
        names = a[-1]
        calls.append(list(names))
        if len(calls) == 1:
            t.sel_cases = list(names)
        return [names, 0]             # second call "succeeds" but changes nothing
    t.SetLoadCasesSelectedForDisplay = stuck
    q = call("query_database_table", {"software": "SAFE", "table_key": "Base Reactions",
                                      "load_cases_or_combos": ["DCON1"]})
    assert q["_error"] and "Restore display selection" in json.dumps(q)


def test_units_restore_failure_never_masks_the_real_error():
    sap = fresh()
    sap.units = 4
    sap.SetPresentUnits = lambda code: 1 if code == 4 else (setattr(sap, "units", code), 0)[1]
    sap.FrameObj.AddByCoord = lambda *a: (_ for _ in ()).throw(OSError("boom"))
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600"})
    assert r["_error"] and "AddByCoord" in json.dumps(r)          # the real error, not the restore one


def test_section_failure_reports_created_frame():
    sap = fresh()
    sap.FrameObj.GetSection = lambda name: ["Other", "", 0]
    sap.FrameObj.SetSection = lambda *a: 1
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600"})
    assert r["_error"] and r["frame_name"] and "section assignment failed" in r["message"]


def test_strict_parameters_and_duplicates():
    fresh()
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": ["0", 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600"})
    assert r["_error"]                                             # no silent "0" -> 0.0
    r = call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "point", "object_names": ["1", "1"],
                                            "load_pattern": "Dead", "values": [0, 0, -10, 0, 0, 0]})
    assert r["_error"] and "only once" in r["message"]


def test_lock_state_unknown_blocks_edit():
    sap = fresh()
    sap.GetModelIsLocked = lambda: None
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600"})
    assert r["_error"] and "lock state" in json.dumps(r)


def test_reshape_rejects_misaligned_data():
    import csi_bridge
    import pytest
    with pytest.raises(csi_bridge.CSIApiError):
        csi_bridge._reshape(["A", "B"], ["1", "2", "3"], 2)
    assert csi_bridge._reshape(["A", "B"], ["1", "2"], 1) == [["1", "2"]]


def test_small_values_not_rounded_away():
    import table_tools
    assert table_tools.py(1.234e-9) == 1.234e-9


# --------------------------------------------------------------------------- 0.3.0 Phase 0 + report R1

def _output(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path / "out"))
    return tmp_path / "out"


def test_dry_run_changes_nothing_and_is_not_logged(tmp_path, monkeypatch):
    out = _output(tmp_path, monkeypatch)
    sap = fresh()
    frames = list(sap.FrameObj.names)
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600", "dry_run": True})
    assert r["dry_run"] and r["would"]["create_frame"]["length"] == 6 and sap.FrameObj.names == frames
    r = call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "point", "object_names": ["1", "77"],
                                            "load_pattern": "Live", "values": [5], "dry_run": True})
    assert r["dry_run"] and r["would"]["objects"] == ["1"] and r["not_found"][0]["object"] == "77"
    assert not sap.PointObj.loads
    units = sap.units
    assert call("set_units", {"software": "ETABS", "unit_preset": "N_mm_C", "dry_run": True})["dry_run"]
    assert sap.units == units
    r = call("run_analysis", {"software": "ETABS", "dry_run": True})
    assert r["would"]["run_cases"] == ["Dead", "Live", "Modal"] and sap.saved == 0 and not sap.analysed
    r = call("open_or_save_model", {"software": "ETABS", "action": "open", "file_path": r"C:\nope\a.sdb",
                                    "dry_run": True})
    assert r["problems"] and r["changed"] is False
    assert not (out / "change_log.jsonl").exists()


def test_change_log_and_snapshot(tmp_path, monkeypatch):
    out = _output(tmp_path, monkeypatch)
    sap = fresh()
    model = tmp_path / "tower.edb"
    model.write_bytes(b"model")
    sap.filename = str(model)
    r = call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [6, 0, 0],
                                      "section_name": "B300x600", "snapshot": True})
    snap = Path(r["snapshot"]["snapshot"])
    assert snap.is_file() and snap.parent.name == "_csi_mcp_snapshots" and snap.read_bytes() == b"model"
    call("create_frame_element", {"software": "ETABS", "start_xyz": [0, 0, 0], "end_xyz": [0, 0, 0],
                                  "section_name": "B300x600"})                  # fails validation: logged as not ok
    lines = [json.loads(x) for x in (out / "change_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["ok"] for x in lines] == [True, False] and lines[0]["action"] == "create_frame_element"
    assert lines[0]["model"] == str(model) and lines[0]["params"]["section_name"] == "B300x600"
    s = call("snapshot_model", {"software": "ETABS", "label": "before review"})
    assert Path(s["snapshot"]).name.endswith("_before_review.edb")
    sap.filename = "(Untitled)"
    r = call("snapshot_model", {"software": "ETABS"})
    assert r["_error"] and "never been saved" in r["message"]


def test_query_auto_export(tmp_path, monkeypatch):
    out = _output(tmp_path, monkeypatch)
    sap = fresh()
    sap.analysed = True
    q = call("query_database_table", {"software": "ETABS", "table_key": "Story Drifts", "max_rows": 3})
    path = Path(q["export_path"])
    assert q["truncated"] and path.parent == out / "exports" and len(path.read_text(encoding="utf-8-sig").splitlines()) == 25
    q = call("query_database_table", {"software": "ETABS", "table_key": "Story Drifts", "max_rows": 100})
    assert q["export_path"] is None
    q = call("query_database_table", {"software": "ETABS", "table_key": "Base Reactions", "export": "json"})
    assert json.loads(Path(q["export_path"]).read_text(encoding="utf-8"))[0]["OutputCase"] == "Dead"


def _media_box(pdf: Path) -> tuple[float, float]:
    m = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf.read_bytes())
    return float(m.group(1)), float(m.group(2))


def test_export_report_default_a4_portrait(tmp_path, monkeypatch):
    out = _output(tmp_path, monkeypatch)
    sap = fresh()
    r = call("export_report", {"software": "ETABS", "project_info": {"title": "Tower A", "job_no": "J-26"}})
    pdf = Path(r["report"])
    assert pdf.parent == out / "reports" and pdf.read_bytes()[:5] == b"%PDF-"
    assert r["page_size"] == "A4" and r["orientation"] == "portrait" and r["pages"] >= 2
    w, h = _media_box(pdf)
    assert abs(w - 595.3) < 1 and abs(h - 841.9) < 1
    assert any("no analysis results" in s["reason"] for s in r["skipped"])          # not analysed: results skipped
    assert sap.units == 6
    second = call("export_report", {"software": "ETABS"})                          # same second: own file name
    assert Path(second["report"]).is_file() and second["report"] != r["report"]


def test_export_report_a3_landscape_tables_results_csv(tmp_path, monkeypatch):
    _output(tmp_path, monkeypatch)
    sap = fresh()
    sap.analysed = True
    sap.units = 4
    target = tmp_path / "rep.pdf"
    r = call("export_report", {"software": "ETABS", "output_path": str(target), "page_size": "A3",
                               "orientation": "landscape", "table_keys": ["Story Drifts", "Nope Table"],
                               "max_rows_per_table": 5})
    assert r["report"] == str(target.resolve()) and r["orientation"] == "landscape"
    w, h = _media_box(target)
    assert abs(w - 1190.6) < 1 and abs(h - 841.9) < 1
    assert r["tables"] == [{"key": "Story Drifts", "rows": 24}]
    assert any("Nope Table" in s["item"] for s in r["skipped"])
    assert r["csv_appendices"] and all(Path(c).is_file() for c in r["csv_appendices"])
    assert "results" in r["sections"] and sap.units == 4                            # user units put back
    again = call("export_report", {"software": "ETABS", "output_path": str(target)})
    assert again["_error"] and "exists" in again["message"]
    bad = call("export_report", {"software": "ETABS", "output_path": str(tmp_path / "x.docx")})
    assert bad["_error"]


# --------------------------------------------------------------------------- Phase 1: model reading

def test_phase1_grids_stories_groups_selected():
    fresh()
    g = call("get_grids", {"software": "ETABS"})
    gs = g["grid_systems"][0]
    assert gs["name"] == "G1" and [x["id"] for x in gs["x_lines"]] == ["A", "B"] and gs["y_lines"][1]["ordinate"] == 4
    s = call("get_stories", {"software": "ETABS"})
    assert s["stories_top_down"][0]["name"] == "Story3" and s["stories_top_down"][-1]["elevation"] == 3
    assert s["stories_top_down"][0]["master"] and s["stories_top_down"][-1]["similar_to"] == "Story3"
    r = call("get_stories", {"software": "SAP2000"})
    assert r["_error"]
    gr = call("get_groups", {"software": "ETABS", "include_members": True})
    edge = next(x for x in gr["groups"] if x["group"] == "EDGE")
    assert edge["objects"] == 2 and edge["point"] == 1 and edge["frame"] == 1 and gr["members"]["count"] == 6
    sel = call("get_selected", {"software": "ETABS"})
    assert sel["selected"] == 2 and sel["by_type"] == {"frame": 1, "area": 1}


def test_phase1_objects_and_story_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    fresh()
    o = call("get_objects", {"software": "ETABS"})
    assert o["points"]["count"] == 6 and o["supports"] == 2 and o["frames"]["count"] == 3 and o["areas"]["count"] == 1
    f1 = next(f for f in o["frames"]["items"] if f["name"] == "F1")
    assert f1["length"] == 6 and f1["orientation"] == "horizontal" and f1["label"] == "BF1"
    c1 = next(f for f in o["frames"]["items"] if f["name"] == "C1")
    assert c1["orientation"] == "vertical" and c1["section"] == "C500x500"
    a1 = o["areas"]["items"][0]
    assert a1["area"] == 24 and a1["points"] == "3 4 6 5" and a1["z_min"] == 3 and a1["story"] == "Story1"
    base = next(p for p in o["points"]["items"] if p["name"] == "1")
    assert base["restraint"] == "fixed" and base["story"] == "Base"
    t = call("get_objects", {"software": "ETABS", "object_type": "points", "max_rows": 2})
    assert t["points"]["truncated"] and Path(t["points"]["export_path"]).exists() and "frames" not in t
    st = call("get_objects", {"software": "ETABS", "story": "Base", "object_type": "points"})
    assert st["points"]["count"] == 2 and st["supports"] == 2


def test_phase1_assignments_and_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    fresh()
    call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "frame", "object_names": ["F1"],
                                        "load_pattern": "Dead", "values": [12.0]})
    fr = call("get_object_assignments", {"software": "ETABS", "object_type": "frames"})
    f1 = next(x for x in fr["items"] if x["name"] == "F1")
    assert f1["releases_j"] == "M3" and f1["modifiers"]["I33"] == 0.35 and f1["distributed_loads"][0]["pattern"] == "Dead"
    c1 = next(x for x in fr["items"] if x["name"] == "C1")
    assert "releases_i" not in c1 and "groups" not in c1          # defaults left out
    full = call("get_object_assignments", {"software": "ETABS", "object_type": "frames", "names": ["C1"],
                                           "non_default_only": False})
    assert full["items"][0]["releases_i"] == "" and full["items"][0]["groups"] == []
    pt = call("get_object_assignments", {"software": "ETABS", "object_type": "points", "names": ["1", "3"]})
    assert pt["items"][0]["restraint"] == "fixed" and pt["items"][0]["groups"] == ["EDGE"] and "restraint" not in pt["items"][1]
    ar = call("get_object_assignments", {"software": "ETABS", "object_type": "areas"})
    assert ar["items"][0]["uniform_loads"][0]["value"] == 2.0 and ar["items"][0]["uniform_loads"][0]["direction"] == "Gravity"
    bad = call("get_object_assignments", {"software": "ETABS", "object_type": "frames", "names": ["ZZ"]})
    assert bad["_error"] and "ZZ" in bad["message"]
    cap = call("get_object_assignments", {"software": "ETABS", "object_type": "points", "max_objects": 2})
    assert cap["_error"] and "max_objects" in cap["message"]
    cut = call("get_object_assignments", {"software": "ETABS", "object_type": "points", "max_rows": 1})
    assert cut["truncated"] and json.loads(Path(cut["export_path"]).read_text(encoding="utf-8"))[0]["name"] == "1"


def test_phase1_definitions_cases_combos_mass():
    fresh()
    m = call("get_definition_details", {"software": "ETABS", "kind": "materials"})
    c30 = m["items"][0]
    assert c30["type"] == "Concrete" and c30["E"] == 24855578 and c30["poisson"] == 0.2
    fs = call("get_definition_details", {"software": "ETABS", "kind": "frame_sections", "names": ["C500x500"]})
    assert fs["count"] == 1 and fs["items"][0]["type"] == "Rectangular" and fs["items"][0]["area"] == 0.25
    a = call("get_definition_details", {"software": "ETABS", "kind": "area_sections"})
    assert a["items"][0]["kind"] == "slab" and a["items"][0]["thickness"] == 0.2
    lp = call("get_definition_details", {"software": "ETABS", "kind": "load_patterns"})
    assert lp["items"][0] == {"name": "Dead", "type": "Dead", "self_weight_multiplier": 1.0}
    lc = call("get_load_case_details", {"software": "ETABS"})
    dead = lc["load_cases"][0]
    assert dead["type"] == "LinearStatic" and dead["loads"] == [{"type": "Load", "name": "Dead", "factor": 1.0}]
    cb = call("get_combo_details", {"software": "ETABS"})
    assert cb["combinations"][0]["type"] == "LinearAdd" and cb["combinations"][1]["type"] == "Envelope"
    assert cb["combinations"][0]["items"][1] == {"kind": "case", "name": "Live", "factor": 1.6}
    ms = call("get_mass_source", {"software": "ETABS"})
    assert ms["mass_sources"][0]["from_elements"] and ms["mass_sources"][0]["load_patterns"][0]["pattern"] == "Dead"


def test_phase1_export_model_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    fresh()
    r = call("export_model_snapshot", {"software": "ETABS"})
    doc = json.loads(Path(r["file"]).read_text(encoding="utf-8"))
    assert r["counts"]["points"] == 6 and r["counts"]["frames"] == 3 and r["counts"]["assignments"]["areas"] == 1
    assert doc["software"] == "ETABS" and doc["stories"][0]["name"] == "Story1" and doc["areas"][0]["coords"][0] == [0, 0, 3]
    assert Path(r["file"]).parent == tmp_path / "exports"
    bad = call("export_model_snapshot", {"software": "ETABS", "output_path": str(tmp_path / "x.txt")})
    assert bad["_error"]
    own = call("export_model_snapshot", {"software": "ETABS", "output_path": str(tmp_path / "m.json"),
                                         "include_assignments": False})
    assert "assignments" not in own["counts"]


def test_report_diagrams_r2(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    fresh()
    call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "frame", "object_names": ["F1"],
                                        "load_pattern": "Dead", "values": [12.0]})
    call("assign_point_or_frame_load", {"software": "ETABS", "target_type": "point", "object_names": ["4"],
                                        "load_pattern": "Live", "values": [50.0]})
    r = call("export_report", {"software": "ETABS", "sections": ["diagrams"]})
    assert not r.get("_error"), r
    # overview + plans (Story1 and Base) + elevations (grid A, B, 1) + loads (Dead, Live)
    assert r["figures"] >= 6 and Path(r["report"]).stat().st_size > 3000
    only = call("export_report", {"software": "ETABS", "sections": ["diagrams"], "diagrams": ["loads"],
                                  "diagram_patterns": ["Dead", "Nope"]})
    assert only["figures"] == 1 and any("Nope" in s["item"] for s in only["skipped"])
    sap_r = call("export_report", {"software": "ETABS", "page_size": "A3", "orientation": "landscape"})
    assert sap_r["figures"] >= 6 and "diagrams" in sap_r["sections"]


# --------------------------------------------------------------------------- Phase 2: results

def _analysed():
    sap = fresh()
    sap.locked = sap.analysed = True
    return sap


def test_phase2_frame_forces_governing_and_setup_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = _analysed()
    sap.Results.Setup.opts["NLStatic"] = 2
    r = call("get_results", {"software": "ETABS", "result_type": "frame_forces", "load_cases_or_combos": ["Dead", "DCON1"],
                             "envelope_by_object": True, "component": "M3", "max_rows": 3})
    assert not r.get("_error"), r
    g = r["governing"]["M3"]
    assert g["max"] == 72 and g["max_at"]["Frame"] == "F1" and g["max_at"]["LoadCase"] == "DCON1" and g["max_at"]["Station"] == 3
    assert r["total_rows"] == 12 and r["truncated"] and Path(r["export_path"]).exists()
    assert r["rows"][0]["M3"] == 72
    env = {e["Frame"]: e for e in r["envelope_by_object"]["rows"]}
    assert env["F1"]["M3_max"] == 72 and env["C1"]["M3_max"] == 0
    seen = sap.Results.seen[-1]
    assert seen[1] == "All" and seen[2] == 2 and seen[3] == 6 and set(seen[4]) == {"Dead", "DCON1"}   # one group call, kN_m
    st = sap.Results.Setup
    assert st.cases == {"Dead": True} and st.combos == {} and st.opts["NLStatic"] == 2   # user's setup back


def test_phase2_named_objects_units_export_xlsx(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = _analysed()
    r = call("get_results", {"software": "ETABS", "result_type": "frame_forces", "names": ["F1"],
                             "load_cases_or_combos": ["Dead"], "units": "N_mm_C", "export": "xlsx"})
    assert sap.Results.seen[-1][1:4] == ("F1", 0, 9) and r["total_rows"] == 3
    assert r["export_path"].endswith(".xlsx") and Path(r["export_path"]).exists()
    re_ = call("get_results", {"software": "ETABS", "result_type": "joint_reactions", "load_cases_or_combos": ["Live"]})
    assert re_["governing"]["F3"]["max"] == 15 and re_["export_path"] is None
    b = call("get_results", {"software": "ETABS", "result_type": "base_reactions"})
    assert b["cases"] == ["Dead", "Live", "Modal"] and b["governing"]["FZ"]["max"] == 60
    e = call("get_results", {"software": "ETABS", "result_type": "buckling_factors"})
    assert e["total_rows"] == 0 and "note" in e


def test_phase2_refusals():
    fresh()
    r = call("get_results", {"software": "ETABS", "result_type": "base_reactions"})
    assert r["_error"] and "no analysis results" in r["message"]
    _analysed()
    r = call("get_results", {"software": "SAP2000", "result_type": "pier_forces"})
    assert r["_error"]
    r = call("get_results", {"software": "ETABS", "result_type": "frame_forces", "component": "M9"})
    assert r["_error"] and "component" in r["message"]
    r = call("get_results", {"software": "ETABS", "result_type": "frame_forces", "load_cases_or_combos": ["NOPE"]})
    assert r["_error"]


def test_phase2_analysis_log(tmp_path):
    from results import parse_log
    model = tmp_path / "m.edb"
    model.write_bytes(b"x")
    log = """
 ETABS Ultimate 64-bit 23.2.0 Build 4400 (Analysis Build 10002/64)
 B E G I N   A N A L Y S I S                                2026/11/26  14:33:47
 NUMBER OF JOINTS                          =         105
 TOTAL NUMBER OF EQUILIBRIUM EQUATIONS     =         315
    NUMBER OF NEGATIVE EIGENVALUES         =           2,  WARNING.
 * * * W A R N I N G * * *
 NUMERICAL PROBLEMS ENCOUNTERED: LOST 8.2 DIGITS OF ACCURACY
 AT JOINT 12 DOF U3

 CASE: DEAD
 Found mode      1 of      9:  EV= 5.1547168E+01, f=    1.142675, T=    0.875140
 NUMBER OF EIGEN MODES FOUND               =           9
 A N A L Y S I S   C O M P L E T E                          2026/11/26  14:33:48
"""
    model.with_suffix(".LOG").write_text(log, encoding="latin-1")
    r = parse_log(log)
    assert r["negative_eigenvalues"] == 2 and r["stable"] is False and r["finished"] and r["modes_found"] == 9
    assert r["warnings"] >= 1 and any("LOST 8.2 DIGITS" in m["text"] for m in r["messages"])
    assert r["counts"]["Number Of Joints"] == 105 and r["cases_solved"] == ["DEAD"] and r["periods"] == [0.87514]
    nl = parse_log(""" BASIC STABILITY CHECK FOR FORCE-CONTROLLED NONLINEAR STATIC LOAD CASES:
    NUMBER OF NEGATIVE STIFFNESS EIGENVALUES SHOULD BE ZERO FOR STABILITY.
    NUMBER FOUND AT FINAL CONVERGED STATE  =           0,  OK.
 A N A L Y S I S   C O M P L E T E                          2026/11/26  16:09:58""")
    assert nl["stable"] is True and nl["negative_eigenvalues"] == 0     # nonlinear case wording (SAP2000 1-012b)
    out = call("get_analysis_log", {"software": "ETABS", "model_path": str(model)})
    assert out["stable"] is False and "LOG" in out["files"]
    miss = call("get_analysis_log", {"software": "ETABS", "model_path": str(tmp_path / "none.edb")})
    assert miss["_error"]


def test_report_result_diagrams_r3(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = _analysed()
    r = call("export_report", {"software": "ETABS", "sections": ["result_diagrams"], "result_case": "Dead"})
    assert not r.get("_error"), r
    assert r["figures"] >= 2 and Path(r["report"]).exists()        # reactions + M3 elevation at least
    items = " ".join(s["item"] for s in r["skipped"])
    assert "joint displacements" in items                          # fake has no JointDispl: listed, not fatal
    none = call("export_report", {"software": "ETABS", "sections": ["result_diagrams"], "result_case": "Dead",
                                  "result_diagrams": ["frame_forces"], "frame_component": "V2"})
    assert none["figures"] >= 1
    sap.locked = sap.analysed = False
    nr = call("export_report", {"software": "ETABS", "sections": ["result_diagrams"]})
    assert nr["figures"] == 0 and any("no analysis results" in s["reason"] for s in nr["skipped"])


# --------------------------------------------------------------------------- Phase 3 (first batch)

def sap_fresh():
    api = FakeApi()
    bridge.attach_external("SAP2000", api)
    return api.SapModel


def test_program_filter_and_tags(monkeypatch):
    t = catalog.tool_description("all")
    assert "- set_area_mesh(" in t and "[SAP2000]" in t and "get_stories(software, units?) [ETABS, SAFE]" in t
    monkeypatch.setenv("CSI_MCP_PROGRAMS", "ETABS,SAFE")
    t2 = catalog.tool_description("all")
    assert "define_joint_pattern" not in t2 and "ETABS | SAFE" in t2 and len(t2) < len(t)
    fresh()
    r = call("get_model_status", {"software": "SAP2000"})
    assert r["_error"] and "not enabled" in r["message"]
    r = call("set_area_mesh", {"software": "ETABS", "area_names": ["A1"]})
    assert r["_error"]
    h = call("help", {"topic": "get_stories"})
    assert h["get_stories"]["programs"] == ["ETABS", "SAFE"]
    monkeypatch.setenv("CSI_MCP_PROGRAMS", "sap2000")
    assert "ETABS" not in call("list_programs")["programs"]


def test_area_mesh_auto_and_divide(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = sap_fresh()
    d = call("set_area_mesh", {"software": "SAP2000", "area_names": ["A1"], "n1": 4, "n2": 8, "dry_run": True})
    assert d["dry_run"] and sap.AreaObj.mesh == {}
    r = call("set_area_mesh", {"software": "SAP2000", "area_names": ["A1"], "n1": 4, "n2": 8})
    assert r["done"] == 1 and sap.AreaObj.mesh["A1"][:3] == (1, 4, 8)
    a = call("get_object_assignments", {"software": "SAP2000", "object_type": "areas"})
    assert a["items"][0]["auto_mesh"] == {"mode": "divisions", "n1": 4, "n2": 8}
    r = call("set_area_mesh", {"software": "SAP2000", "group": "All", "mode": "max_size", "max_size_1": 0.5,
                               "max_size_2": 0.5, "units": "kN_m_C"})
    assert sap.AreaObj.mesh["A1"][:1] == (2,) and sap.AreaObj.mesh["A1"][3] == 0.5
    bad = call("set_area_mesh", {"software": "SAP2000", "area_names": ["A1"], "mode": "max_size"})
    assert bad["_error"] and "max_size_1" in bad["message"]
    sap.PointObj.restraints.update({"3": (True,) * 6, "4": (True,) * 6})   # edge 3-4 of A1 fixed
    dv = call("set_area_mesh", {"software": "SAP2000", "area_names": ["A1"], "method": "divide", "n1": 2, "n2": 2})
    assert dv["areas_created"] == 4 and "A1" not in sap.AreaObj.names
    assert dv["edge_joints_restrained"] == 1 and sap.PointObj.restraints["E"] == (True,) * 6
    no = call("set_area_mesh", {"software": "SAP2000", "area_names": ["ZZ"]})
    assert no["_error"] and "ZZ" in no["message"]
    et = call("set_area_mesh", {"software": "ETABS", "area_names": ["A1"]})
    assert et["_error"] and "mode max_size or none" in et["message"]     # ETABS: tables, max_size / none only


def test_hydrostatic_pattern_and_surface_pressure(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = sap_fresh()
    r = call("define_joint_pattern", {"software": "SAP2000", "name": "WATER", "method": "hydrostatic",
                                      "fluid_level": 2.0, "unit_weight": 10.0})
    assert r["created"] and r["rule"]["C"] == -10 and r["rule"]["D"] == 20
    vals = {s["point"]: s["value"] for s in r["sample_values"]}
    assert vals["1"] == 20 and vals["3"] == 0            # z = 0: 10 x 2 = 20; z = 3 above the level: 0
    lin = call("define_joint_pattern", {"software": "SAP2000", "name": "LIN", "method": "linear", "a": 1.0,
                                        "point_names": ["2"]})
    assert lin["sample_values"][0]["value"] == 6
    bad = call("define_joint_pattern", {"software": "SAP2000", "name": "W2", "method": "hydrostatic"})
    assert bad["_error"]
    p = call("assign_area_load", {"software": "SAP2000", "area_names": ["A1"], "load_pattern": "Dead",
                                  "load_type": "surface_pressure", "value": 1.0, "joint_pattern": "WATER"})
    assert p["assigned"] == 1 and sap.AreaObj.pressure["A1"] == [("Dead", -1, 1.0, "WATER")]
    a = call("get_object_assignments", {"software": "SAP2000", "object_type": "areas"})
    assert a["items"][0]["surface_pressure_loads"][0] == {"pattern": "Dead", "face": "bottom", "value": 1.0,
                                                          "joint_pattern": "WATER"}
    pts = call("get_object_assignments", {"software": "SAP2000", "object_type": "points", "names": ["1"]})
    assert pts["items"][0]["joint_pattern_values"]["WATER"] == 20
    jp = call("get_definition_details", {"software": "SAP2000", "kind": "joint_patterns"})
    w = next(x for x in jp["items"] if x["name"] == "WATER")
    assert w["max"] == 20 and w["max_z"] == 0
    nop = call("assign_area_load", {"software": "SAP2000", "area_names": ["A1"], "load_pattern": "Dead",
                                    "load_type": "surface_pressure", "value": 1.0, "joint_pattern": "NOPE"})
    assert nop["_error"] and "define_joint_pattern" in nop["hint"]
    et = call("assign_area_load", {"software": "ETABS", "area_names": ["A1"], "load_pattern": "Dead",
                                   "load_type": "surface_pressure", "value": 1.0})
    assert et["_error"] and "per strip" in et["hint"]


def test_uniform_area_loads_etabs(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = fresh()
    d = call("assign_area_load", {"software": "ETABS", "group": "All", "load_pattern": "Live", "load_type": "uniform",
                                  "value": 3.0, "dry_run": True})
    assert d["would"]["areas"] == 1 and sap.AreaObj.uniform["A1"] == [("Live", 10, 2.0)]
    r = call("assign_area_load", {"software": "ETABS", "area_names": ["A1"], "load_pattern": "Live",
                                  "load_type": "uniform", "value": 3.0})
    assert r["assigned"] == 1 and sap.AreaObj.uniform["A1"] == [("Live", 10, 3.0)]
    tf = call("assign_area_load", {"software": "ETABS", "area_names": ["A1"], "load_pattern": "Dead",
                                   "load_type": "uniform_to_frame", "value": 1.5, "distribution": "one_way"})
    assert tf["assigned"] == 1
    a = call("get_object_assignments", {"software": "ETABS", "object_type": "areas"})
    assert a["items"][0]["uniform_to_frame_loads"][0]["distribution"] == "one_way"
    ghost = call("assign_area_load", {"software": "ETABS", "area_names": ["A9"], "load_pattern": "Live",
                                      "load_type": "uniform", "value": 1.0})
    assert ghost["_error"] and "A9" in ghost["message"]          # caught before ETABS answers 0 for it
    log = [json.loads(x) for x in (tmp_path / "change_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["action"] for x in log] == ["assign_area_load", "assign_area_load", "assign_area_load"]


# --------------------------------------------------------------------------- 0.7.0 editions (one server per program)

def test_edition_from_exe_name_and_tools(monkeypatch):
    import edition
    assert edition.from_exe(r"C:\x\etabs-mcp-server.exe") == "ETABS"
    assert edition.from_exe("sap2000-mcp-server.exe") == "SAP2000" and edition.from_exe("safe-mcp-server.exe") == "SAFE"
    assert edition.from_exe("csi-mcp-server.exe") is None
    monkeypatch.setenv("CSI_MCP_PROGRAM", "SAP2000")
    tools = asyncio.run(server.create_server().list_tools())
    assert [t.name for t in tools] == ["sap2000"] and tools[0].annotations.title == "SAP2000"
    monkeypatch.setenv("CSI_MCP_SPLIT", "1")
    tools = asyncio.run(server.create_server().list_tools())
    assert [t.name for t in tools] == ["sap2000_read", "sap2000_edit"]


def test_edition_text_is_program_specific(monkeypatch):
    for prog, present, absent in (
            ("ETABS", ["get_stories", "story_drifts", "pier / spandrel", "with label and story", ".edb"],
             ["surface_pressure", "joint_patterns", ".sdb", "SAP2000:"]),
            ("SAP2000", ["set_area_mesh", "define_joint_pattern", "surface_pressure", ".sdb", "'Grid Lines' table"],
             ["get_stories", "story_drifts", "pier / spandrel", "with label and story", ".edb", "story?"]),
            ("SAFE", ["get_stories", "story and joint drifts", ".fdb", "with label and story"],
             ["set_area_mesh", "surface_pressure", "pier / spandrel", "story_charts", ".edb", "face?"])):
        monkeypatch.setenv("CSI_MCP_PROGRAM", prog)
        text = catalog.tool_description("all")
        listing = text.split("READ ACTIONS")[1]
        assert "software" not in listing and "[[" not in text, prog
        for word in present:
            assert word in text, (prog, word)
        for word in absent:
            assert word not in listing, (prog, word)
        h = catalog.help_data("assign_area_load")["assign_area_load"]["params"]
        assert ("surface_pressure" in h["load_type"]["one_of"]) == (prog == "SAP2000")
        assert ("face" in h) == (prog == "SAP2000") and "software" not in h
        r = catalog.help_data("get_results")["get_results"]["params"]["result_type"]["one_of"]
        assert ("pier_forces" in r) == (prog == "ETABS") and ("story_drifts" in r) == (prog != "SAP2000")


def test_edition_fills_software_and_refuses_others(monkeypatch):
    fresh("SAP2000")
    monkeypatch.setenv("CSI_MCP_PROGRAM", "SAP2000")
    r = call("get_model_status")
    assert r["software"] == "SAP2000" if "software" in r else not r.get("_error")
    bad = call("get_model_status", {"software": "ETABS"})
    assert bad["_error"] and "SAP2000 server" in bad["message"] and "etabs tool" in bad["hint"]
    st = call("get_analysis_summary", {"result_type": "story_drifts"})
    assert st["_error"] and "not available in SAP2000" in st["message"] and "joint_displacements" in st["hint"]
    monkeypatch.setenv("CSI_MCP_PROGRAM", "ETABS")
    fresh("ETABS")
    sp = call("assign_area_load", {"area_names": ["A1"], "load_pattern": "Dead", "load_type": "surface_pressure",
                                   "value": 1.0})
    assert sp["_error"] and "per strip" in sp["hint"]
    fc = call("assign_area_load", {"area_names": ["A1"], "load_pattern": "Dead", "load_type": "uniform",
                                   "value": 1.0, "face": "top"})
    assert fc["_error"] and "face does not apply in ETABS" in fc["message"]
    ok = call("assign_area_load", {"area_names": ["A1"], "load_pattern": "Dead", "load_type": "uniform", "value": 1.0,
                                   "dry_run": True})
    assert ok.get("dry_run") is True
    ms = call("set_area_mesh", {"area_names": ["A1"]})
    assert ms["_error"] and "mode max_size or none" in ms["message"]


def test_edition_client_config_replaces_combined_entry(tmp_path, monkeypatch):
    import client_config
    appdata = tmp_path / "Roaming"
    (appdata / "Claude").mkdir(parents=True)
    cfg = appdata / "Claude" / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"revit": {"command": "r.exe"}, "csi": {"command": "old.exe"}}}),
                   encoding="utf-8")
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "x"\n\n[mcp_servers.csi]\ncommand = "old.exe"\n\n[mcp_servers.csi.env]\nA = "1"\n',
                     encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CSI_MCP_PROGRAM", "ETABS")
    assert client_config.run({"command": "etabs-mcp-server.exe"}) == 0
    servers = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert servers == {"revit": {"command": "r.exe"}, "etabs": {"command": "etabs-mcp-server.exe"}}
    toml = codex.read_text(encoding="utf-8")
    assert "[mcp_servers.etabs]" in toml and "mcp_servers.csi" not in toml and 'model = "x"' in toml
    monkeypatch.setenv("CSI_MCP_PROGRAM", "SAFE")
    assert client_config.run({"command": "safe-mcp-server.exe"}) == 0
    assert set(json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]) == {"revit", "etabs", "safe"}
    assert client_config.run(None) == 0
    assert set(json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]) == {"revit", "etabs"}


def test_client_config_finds_claude_data_file(tmp_path, monkeypatch):
    """Claude Desktop 6.x reads %LOCALAPPDATA%\\Claude-Data\\claude_<id>\\claude_desktop_config.json (other settings kept)."""
    import client_config
    local = tmp_path / "Local"
    live = local / "Claude-Data" / "claude_pzs8sxrjxfjjc" / "claude_desktop_config.json"
    live.parent.mkdir(parents=True)
    live.write_text(json.dumps({"preferences": {"x": 1}, "mcpServers": {"civil3d": {"command": "c.exe"},
                                                                         "csi": {"command": "py.exe"}}}),
                    encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CSI_MCP_PROGRAM", "SAP2000")
    assert live in client_config.claude_files()
    assert client_config.run({"command": "sap2000-mcp-server.exe"}) == 0
    data = json.loads(live.read_text(encoding="utf-8"))
    assert data["preferences"] == {"x": 1}
    assert data["mcpServers"] == {"civil3d": {"command": "c.exe"}, "sap2000": {"command": "sap2000-mcp-server.exe"}}


# --------------------------------------------------------------------------- 0.8.0 Phase 3 batch 2: definitions

def test_define_material_user_and_library():
    sap = fresh("ETABS")
    d = call("define_material", {"software": "ETABS", "name": "C40", "type": "concrete", "E": 29700000.0,
                                 "poisson": 0.2, "unit_weight": 25.0, "fc": 40000.0, "dry_run": True})
    assert d["dry_run"] and "C40" not in sap.PropMaterial.names
    r = call("define_material", {"software": "ETABS", "name": "C40", "type": "concrete", "E": 29700000.0,
                                 "poisson": 0.2, "unit_weight": 25.0, "fc": 40000.0})
    b = r["read_back"]
    assert b["type"] == "Concrete" and b["E"] == 29700000.0 and b["poisson"] == 0.2 and b["fc"] == 40000.0
    assert b["weight_per_volume"] == 25.0 and "thermal_coeff" in r["kept_program_defaults"]
    again = call("define_material", {"software": "ETABS", "name": "C40", "type": "concrete", "E": 1.0, "poisson": 0.2,
                                     "unit_weight": 25.0, "fc": 1.0})
    assert again["_error"] and "already exists" in again["message"] and "replace" in again["hint"]
    s = call("define_material", {"software": "ETABS", "name": "S355", "type": "steel", "E": 210000000.0,
                                 "poisson": 0.3, "unit_weight": 78.5, "fy": 355000.0, "fu": 510000.0})
    assert s["read_back"]["fy"] == 355000.0 and sap.PropMaterial.mat["S355"]["efy"] == 1.1 * 355000.0
    miss = call("define_material", {"software": "ETABS", "name": "X", "type": "steel", "E": 2e8, "poisson": 0.3,
                                    "unit_weight": 78.5})
    assert miss["_error"] and "fy" in miss["message"]
    lib = call("define_material", {"software": "ETABS", "name": "A992Fy50", "type": "steel", "source": "library",
                                   "region": "United States", "standard": "ASTM A992", "grade": "Grade 50"})
    assert lib["name_in_model"] == "A992Fy50" and lib["read_back"]["type"] == "Steel"


def test_define_sections_per_program(monkeypatch):
    sap = fresh("SAP2000")
    r = call("define_frame_section", {"software": "SAP2000", "name": "COL400", "shape": "rectangle",
                                      "material": "C30", "depth": 0.4, "width": 0.4})
    assert r["read_back"]["t3_depth"] == 0.4 and r["read_back"]["material"] == "C30"
    assert r["read_back"]["properties"]["area"] == 0.16
    i = call("define_frame_section", {"software": "SAP2000", "name": "W1", "shape": "i_section", "material": "C30",
                                      "depth": 0.5, "width": 0.2})
    assert i["_error"] and "flange_thickness" in i["message"]
    f = call("define_frame_section", {"software": "SAP2000", "name": "W14X90", "shape": "from_file",
                                      "material": "A615Gr60", "file_name": "AISC16.xml", "section_in_file": "W14X90",
                                      "modifiers": [1, 1, 1, 0.1, 1, 1, 1, 1]})
    assert not f.get("_error") and sap.PropFrame.modifiers["W14X90"][3] == 0.1
    nomat = call("define_frame_section", {"software": "SAP2000", "name": "X", "shape": "circle", "material": "NOPE",
                                          "depth": 0.3})
    assert nomat["_error"] and "Material 'NOPE'" in nomat["message"]
    sh = call("define_area_section", {"software": "SAP2000", "name": "WALL250", "kind": "shell_thick",
                                      "material": "C30", "thickness": 0.25})
    assert sh["read_back"]["kind"] == "shell" and sh["read_back"]["shell_type"] == 2
    bad = call("define_area_section", {"software": "SAP2000", "name": "S1", "kind": "slab", "material": "C30",
                                       "thickness": 0.2})
    assert bad["_error"] and "not available in SAP2000" in bad["message"]
    fresh("ETABS")
    sl = call("define_area_section", {"software": "ETABS", "name": "MAT800", "kind": "slab", "slab_type": "mat",
                                      "shell_type": "shell_thick", "material": "C30", "thickness": 0.8,
                                      "modifiers": [1, 1, 1, 0.25, 0.25, 0.25, 1, 1, 1, 1]})
    b = sl["read_back"]
    assert b["kind"] == "slab" and b["subtype"] == 5 and b["shell_type"] == 2 and b["modifiers"][3] == 0.25
    w = call("define_area_section", {"software": "ETABS", "name": "W300", "kind": "wall", "material": "C30",
                                     "thickness": 0.3})
    assert w["read_back"]["kind"] == "wall"
    plate = call("define_area_section", {"software": "ETABS", "name": "P", "kind": "plate_thin", "material": "C30",
                                         "thickness": 0.3})
    assert plate["_error"] and "not available in ETABS" in plate["message"]
    bt = call("define_area_section", {"software": "ETABS", "name": "P", "kind": "slab", "material": "C30",
                                      "thickness": 0.3, "bending_thickness": 0.2})
    assert bt["_error"] and "bending_thickness does not apply" in bt["message"]


def test_define_springs_etabs_safe_only():
    sap = fresh("SAFE")
    r = call("define_spring_property", {"software": "SAFE", "name": "SOIL", "kind": "area",
                                        "stiffness": [0, 0, 20000.0]})
    assert r["read_back"]["stiffness"] == [0.0, 0.0, 20000.0]
    p = call("define_spring_property", {"software": "SAFE", "name": "PILE", "kind": "point",
                                        "stiffness": [1e4, 1e4, 2e5, 0, 0, 0]})
    assert p["read_back"]["stiffness"][2] == 200000.0
    d = call("get_definition_details", {"software": "SAFE", "kind": "springs"})
    assert {x["name"] for x in d["items"]} == {"SOIL", "PILE"}
    bad = call("define_spring_property", {"software": "SAFE", "name": "X", "kind": "point", "stiffness": [1, 2, 3]})
    assert bad["_error"] and "6 stiffnesses" in bad["message"]
    fresh("SAP2000")
    no = call("define_spring_property", {"software": "SAP2000", "name": "X", "kind": "point",
                                         "stiffness": [1, 1, 1, 1, 1, 1]})
    assert no["_error"] and "not available in SAP2000" in no["message"]


def test_define_patterns_cases_combos_mass():
    sap = fresh("SAP2000")
    r = call("define_load_pattern", {"software": "SAP2000", "name": "SDL", "type": "superdead"})
    assert r["read_back"] == {"name": "SDL", "type": "SuperDead", "self_weight_multiplier": 0.0}
    assert "SDL" in sap.LoadCases.names
    ex = call("define_load_pattern", {"software": "SAP2000", "name": "Live", "type": "live"})
    assert ex["_error"] and "already exists" in ex["message"]
    ch = call("define_load_pattern", {"software": "SAP2000", "name": "Live", "type": "reducelive", "replace": True})
    assert ch["read_back"]["type"] == "ReduceLive"
    c = call("define_load_case", {"software": "SAP2000", "name": "DL+SDL", "type": "linear_static",
                                  "loads": [{"name": "Dead"}, {"name": "SDL", "factor": 1.0}]})
    assert [x["name"] for x in c["read_back"]["loads"]] == ["Dead", "SDL"]
    nop = call("define_load_case", {"software": "SAP2000", "name": "X", "type": "linear_static",
                                    "loads": [{"name": "NOPE"}]})
    assert nop["_error"] and "NOPE" in nop["message"]
    m = call("define_load_case", {"software": "SAP2000", "name": "MODAL20", "type": "modal_eigen", "max_modes": 20})
    assert m["read_back"]["type"] == "Modal" and sap.LoadCases.ModalEigen.modes["MODAL20"] == (20, 1)
    rz = call("define_load_case", {"software": "SAP2000", "name": "RITZ", "type": "modal_ritz",
                                   "loads": [{"kind": "acceleration", "name": "ux"},
                                             {"kind": "acceleration", "name": "UY"}]})
    assert not rz.get("_error") and sap.LoadCases.ModalRitz.loads["RITZ"] == [("Accel", "UX"), ("Accel", "UY")]
    acc = call("define_load_case", {"software": "SAP2000", "name": "X", "type": "modal_ritz",
                                    "loads": [{"kind": "acceleration", "name": "UQ"}]})
    assert acc["_error"] and "UQ" in acc["message"]
    co = call("define_combination", {"software": "SAP2000", "name": "ULS1",
                                     "items": [{"name": "Dead", "factor": 1.4}, {"name": "SDL", "factor": 1.4},
                                               {"name": "Live", "factor": 1.6}]})
    assert co["read_back"]["type"] == "LinearAdd" and [i["factor"] for i in co["read_back"]["items"]] == [1.4, 1.4, 1.6]
    env = call("define_combination", {"software": "SAP2000", "name": "ENV2", "type": "envelope",
                                      "items": [{"name": "ULS1"}, {"name": "DCON1"}]})
    assert env["read_back"]["type"] == "Envelope" and {i["kind"] for i in env["read_back"]["items"]} == {"combination"}
    rep = call("define_combination", {"software": "SAP2000", "name": "ULS1", "replace": True,
                                      "items": [{"name": "Dead", "factor": 1.35}]})
    assert [i["factor"] for i in rep["read_back"]["items"]] == [1.35]
    self_ref = call("define_combination", {"software": "SAP2000", "name": "Z", "items": [{"name": "Z"}]})
    assert self_ref["_error"] and "itself" in self_ref["message"]
    ms = call("set_mass_source", {"software": "SAP2000", "from_loads": True,
                                  "loads": [{"pattern": "SDL"}, {"pattern": "Live", "factor": 0.25}]})
    b = ms["read_back"]
    assert b["name"] == "MSSSRC1" and b["from_loads"] and b["load_patterns"][1] == {"pattern": "Live", "factor": 0.25}
    fresh("ETABS")
    me = call("set_mass_source", {"software": "ETABS", "from_elements": True, "from_masses": False,
                                  "from_loads": True, "loads": [{"pattern": "Live", "factor": 0.3}]})
    assert me["read_back"]["from_masses"] is False and me["read_back"]["load_patterns"][0]["factor"] == 0.3
    nm = call("set_mass_source", {"software": "ETABS", "name": "MS2", "from_elements": True})
    assert nm["_error"] and "name does not apply in ETABS" in nm["message"]


def test_definitions_respect_lock_and_tags(monkeypatch):
    sap = fresh("ETABS")
    sap.locked = True
    r = call("define_load_pattern", {"software": "ETABS", "name": "W", "type": "wind"})
    assert r.get("status") == "confirmation_required" or (r.get("_error") and "allow_unlock" in json.dumps(r))
    assert "W" not in sap.LoadPatterns.names
    monkeypatch.setenv("CSI_MCP_PROGRAM", "SAP2000")
    text = catalog.tool_description("all")
    assert "[Definitions]" in text and "define_spring_property" not in text and "membrane" in text
    monkeypatch.setenv("CSI_MCP_PROGRAM", "SAFE")
    h = catalog.help_data("define_area_section")["define_area_section"]["params"]
    assert h["kind"]["one_of"] == ["slab", "wall"] and "bending_thickness" not in h


# --------------------------------------------------------------------------- 0.9.0 Phase 3 batch 3: objects

def test_create_points_frames_areas():
    sap = fresh("ETABS")
    d = call("create_points", {"software": "ETABS", "points": [{"xyz": [12, 0, 0]}], "dry_run": True})
    assert d["dry_run"] and "P7" not in sap.PointObj.names
    p = call("create_points", {"software": "ETABS", "points": [{"xyz": [12, 0, 0], "name": "B1"},
                                                                {"xyz": [0, 0, 0]}], "restraint": "pinned"})
    assert p["point_names"] == ["B1", "1"] and sap.PointObj.restraints["B1"] == (True, True, True, False, False, False)
    f = call("create_frames", {"software": "ETABS", "section": "B300x600",
                               "frames": [{"start": "2", "end": "B1"}, {"start": [12, 0, 0], "end": [12, 0, 3]}]})
    c = f["created"]
    assert c[0]["points"] == ["2", "B1"] and c[0]["label"] == "B" + c[0]["name"] and c[0]["story"] == "Story1"
    assert c[1]["points"][0] == "B1"            # the coordinate end reuses the existing point
    bad = call("create_frames", {"software": "ETABS", "section": "NOPE", "frames": [{"start": "1", "end": "2"}]})
    assert bad["_error"] and "NOPE" in bad["message"]
    ghost = call("create_frames", {"software": "ETABS", "section": "B300x600", "frames": [{"start": "1", "end": "Q"}]})
    assert ghost["_error"] and "Q" in ghost["message"]
    a = call("create_areas", {"software": "ETABS", "section": "Slab200",
                              "areas": [{"points": [[0, 0, 0], [6, 0, 0], [6, 0, 3], [0, 0, 3]], "name": "W1"}]})
    assert a["created"][0]["name"] == "W1" and a["created"][0]["points"] == ["1", "2", "4", "3"]
    o = call("create_areas", {"software": "ETABS", "opening": True, "areas": [{"points": ["3", "4", "6"]}]})
    assert sap.AreaObj.opening[o["created"][0]["name"]] is True
    none = call("create_areas", {"software": "ETABS", "areas": [{"points": ["3", "4", "6"]}]})
    assert none["_error"] and "section" in none["message"]


def test_assign_point_frame_area_properties():
    sap = fresh("SAFE")
    r = call("assign_point_properties", {"software": "SAFE", "point_names": ["3", "4"], "restraint": "roller_z",
                                         "spring_stiffness": [0, 0, 1000, 0, 0, 0], "mass": [1, 1, 0, 0, 0, 0],
                                         "diaphragm": "D2"})
    b = r["read_back"][0]
    assert b["restraint"] and b["spring_K"][2] == 1000 and b["mass"][0] == 1 and b["diaphragm"] == "D2"
    assert r["diaphragm_created"] is True and "D2" in sap.Diaphragm.names
    rm = call("assign_point_properties", {"software": "SAFE", "point_names": ["3"], "spring_stiffness": [0] * 6})
    assert "spring_K" not in rm["read_back"][0]
    nop = call("assign_point_properties", {"software": "SAFE", "point_names": ["3"], "spring_property": "NOPE"})
    assert nop["_error"] and "NOPE" in nop["message"]
    none = call("assign_point_properties", {"software": "SAFE", "point_names": ["3"]})
    assert none["_error"] and "at least one" in none["message"]
    fr = call("assign_frame_properties", {"software": "SAFE", "frame_names": ["F1"], "section": "C500x500",
                                          "releases": {"i": ["M2", "M3"], "j": ["M3"]},
                                          "end_offsets": {"length_i": 0.25, "length_j": 0.25,
                                                          "rigid_zone_factor": 0.5},
                                          "insertion_point": {"cardinal_point": 8},
                                          "modifiers": [1, 1, 1, 0.1, 0.35, 0.35, 1, 1], "local_axis_angle": 90})
    b = fr["read_back"][0]
    assert b["section"] == "C500x500" and b["releases_i"] == "M2 M3" and b["releases_j"] == "M3"
    assert b["end_offsets"]["length_i"] == 0.25 and b["insertion_point"]["cardinal_point"] == 8
    assert b["modifiers"]["J"] == 0.1 and b["local_axis_angle"] == 90
    unstable = call("assign_frame_properties", {"software": "SAFE", "frame_names": ["F1"],
                                                "releases": {"i": ["T"], "j": ["T"]}})
    assert unstable["_error"]
    ar = call("assign_area_properties", {"software": "SAFE", "area_names": ["A1"], "modifiers": [1] * 3 + [0.25] * 3
                                         + [1] * 4, "diaphragm": "D1", "local_axis_angle": 30})
    b = ar["read_back"][0]
    assert b["modifiers"]["m11"] == 0.25 and b["diaphragm"] == "D1" and b["local_axis_angle"] == 30
    sg = call("assign_area_properties", {"software": "SAFE", "area_names": ["A1"], "subgrade_modulus": 20000})
    assert sg["_error"] and "subgrade_modulus does not apply in SAFE" in sg["message"]
    fresh("SAP2000")
    s2 = call("assign_area_properties", {"software": "SAP2000", "area_names": ["A1"], "subgrade_modulus": 20000})
    assert s2["read_back"][0]["springs"] == 1
    dg = call("assign_area_properties", {"software": "SAP2000", "area_names": ["A1"], "diaphragm": "D1"})
    assert dg["_error"] and "diaphragm does not apply in SAP2000" in dg["message"]


def test_temperature_groups_select_delete():
    sap = fresh("SAP2000")
    t = call("assign_temperature_load", {"software": "SAP2000", "target_type": "frame", "object_names": ["F1"],
                                         "load_pattern": "Dead", "value": 30})
    assert t["read_back"][0]["loads"] == [{"pattern": "Dead", "type": 1, "value": 30.0}]
    ag = call("get_object_assignments", {"software": "SAP2000", "object_type": "frames", "names": ["F1"],
                                         "include_loads": True})
    assert ag["items"][0]["temperature_loads"][0]["type"] == "uniform"
    bad = call("assign_temperature_load", {"software": "SAP2000", "target_type": "area", "object_names": ["A1"],
                                           "load_pattern": "Dead", "kind": "gradient_2", "value": 5})
    assert bad["_error"] and "gradient_3" in bad["message"]
    g = call("set_group", {"software": "SAP2000", "operation": "define", "name": "BEAMS"})
    assert g["group_holds"] == {}
    g = call("set_group", {"software": "SAP2000", "operation": "add", "name": "BEAMS", "frames": ["F1", "C1"],
                           "points": ["3"]})
    assert g["group_holds"] == {"frame": 2, "point": 1}
    g = call("set_group", {"software": "SAP2000", "operation": "remove", "name": "BEAMS", "frames": ["C1"]})
    assert g["group_holds"] == {"frame": 1, "point": 1}
    mods = call("assign_frame_properties", {"software": "SAP2000", "group": "BEAMS", "local_axis_angle": 45})
    assert mods["frames"] == 1 and sap.FrameObj.axes == {"F1": 45}
    s = call("select_objects", {"software": "SAP2000", "mode": "group", "group": "BEAMS"})
    assert s["selected"] == {"frame": 1, "point": 1}
    s = call("select_objects", {"software": "SAP2000", "mode": "names", "areas": ["A1"], "keep_existing": True})
    assert s["selected_total"] == 3
    s = call("select_objects", {"software": "SAP2000", "mode": "clear"})
    assert s["selected_total"] == 0
    dd = call("delete_objects", {"software": "SAP2000", "kind": "frames", "names": ["C2"], "dry_run": True})
    assert dd["dry_run"] and "C2" in sap.FrameObj.names
    d = call("delete_objects", {"software": "SAP2000", "kind": "frames", "names": ["C2"]})
    assert d["deleted"] == 1 and "C2" not in sap.FrameObj.names
    dp = call("delete_objects", {"software": "SAP2000", "kind": "points", "names": ["2", "3"]})
    assert dp["deleted"] == 1 and dp["kept"] == ["3"] and "2" not in sap.PointObj.names
    gd = call("set_group", {"software": "SAP2000", "operation": "delete", "name": "BEAMS"})
    assert gd["deleted"] and "BEAMS" not in sap.GroupDef.names and "F1" in sap.FrameObj.names


def test_safe_refuses_frame_temperature_and_sap_modifiers_placeholder():
    fresh("SAFE")
    r = call("assign_temperature_load", {"software": "SAFE", "target_type": "frame", "object_names": ["F1"],
                                         "load_pattern": "Dead", "value": 30})
    assert r["_error"] and "not available in SAFE" in r["message"] and "ignores temperature" in r["hint"]
    a = call("assign_temperature_load", {"software": "SAFE", "target_type": "area", "object_names": ["A1"],
                                         "load_pattern": "Dead", "kind": "gradient_3", "value": 10})
    assert "bending" in a["note"]
    sap = fresh("SAP2000")
    calls = []
    real = sap.FrameObj.GetModifiers
    sap.FrameObj.GetModifiers = lambda name, *a: (calls.append(a), real(name))[1] if a else [(), 1]
    r = call("get_object_assignments", {"software": "SAP2000", "object_type": "frames", "names": ["F1"]})
    assert r["items"][0]["modifiers"]["I33"] == 0.35 and calls and len(calls[0][0]) == 8


def test_etabs_warns_free_base_level_points():
    fresh("ETABS")
    f = call("create_frames", {"software": "ETABS", "section": "B300x600",
                               "frames": [{"start": "1", "end": [3, 0, 0]}]})
    assert f["free_base_level_points"] and "Base level" in f["note"]      # Story base elevation 0 in the fake
    up = call("create_frames", {"software": "ETABS", "section": "B300x600",
                                "frames": [{"start": "3", "end": [3, 0, 3]}]})
    assert "note" not in up
    fresh("SAP2000")
    s = call("create_frames", {"software": "SAP2000", "section": "B300x600",
                               "frames": [{"start": "1", "end": [3, 0, 0]}]})
    assert "note" not in s


# --------------------------------------------------------------------------- 0.10.0 RS / TH functions and cases

def test_functions_sap_api_and_etabs_tables():
    sap = fresh("SAP2000")
    r = call("define_function", {"software": "SAP2000", "name": "FLAT", "kind": "response_spectrum",
                                 "points": [[0, 0.5], [10, 0.5]], "damping_ratio": 0.05})
    assert r["read_back"]["points"] == [[0.0, 0.5], [10.0, 0.5]] and "table_edit" not in r
    bad = call("define_function", {"software": "SAP2000", "name": "X", "kind": "time_history",
                                   "points": [[0, 0], [0, 1]]})
    assert bad["_error"] and "increase" in bad["message"]
    e = fresh("ETABS")
    e.Func.etabs_style = True
    t = call("define_function", {"software": "ETABS", "name": "STEP", "kind": "time_history",
                                 "points": [[0, 1], [10, 1]]})
    assert t["read_back"]["points"] == [[0.0, 1.0], [10.0, 1.0]]
    assert t["table_edit"]["rows_before"] == 2 and t["table_edit"]["rows_after"] == 4     # UnifTH rows kept
    assert "UnifTH" in e.Func.f
    rs = call("define_function", {"software": "ETABS", "name": "FLAT", "kind": "response_spectrum",
                                  "points": [[0, 0.5], [4, 0.5]], "damping_ratio": 0.03})
    assert e.Func.f["FLAT"][3] == 0.03 and "UnifRS" in e.Func.f
    again = call("define_function", {"software": "ETABS", "name": "FLAT", "kind": "response_spectrum",
                                     "points": [[0, 0.4], [4, 0.4]]})
    assert again["_error"] and "already exists" in again["message"]
    rep = call("define_function", {"software": "ETABS", "name": "FLAT", "kind": "response_spectrum", "replace": True,
                                   "points": [[0, 0.4], [4, 0.4]]})
    assert rep["read_back"]["points"] == [[0.0, 0.4], [4.0, 0.4]]
    neg = call("define_function", {"software": "ETABS", "name": "NEG", "kind": "response_spectrum",
                                   "points": [[0, -1], [4, 1]]})
    assert neg["_error"] and "ERROR" in neg["message"] and "NEG" not in e.Func.f
    fresh("SAFE")
    no = call("define_function", {"software": "SAFE", "name": "F", "kind": "time_history", "points": [[0, 1], [1, 1]]})
    assert no["_error"] and "not available in SAFE" in no["message"]


def test_rs_and_modal_history_cases():
    sap = fresh("SAP2000")
    call("define_function", {"software": "SAP2000", "name": "FLAT", "kind": "response_spectrum",
                             "points": [[0, 0.5], [10, 0.5]]})
    call("define_function", {"software": "SAP2000", "name": "STEP", "kind": "time_history",
                             "points": [[0, 1], [10, 1]]})
    r = call("define_load_case", {"software": "SAP2000", "name": "RSX", "type": "response_spectrum",
                                  "loads": [{"kind": "acceleration", "name": "UX", "function": "FLAT",
                                             "factor": 9.80665}],
                                  "modal_combination": "srss", "damping": 0.02})
    b = r["read_back"]
    assert b["loads"] == [{"direction": "U1", "function": "FLAT", "factor": 9.80665}]
    assert b["modal_case"] == "Modal" and b["modal_combination"] == "srss" and b["damping"] == 0.02
    nf = call("define_load_case", {"software": "SAP2000", "name": "X", "type": "response_spectrum",
                                   "loads": [{"kind": "acceleration", "name": "U1", "function": "NOPE"}]})
    assert nf["_error"] and "NOPE" in nf["message"]
    pat = call("define_load_case", {"software": "SAP2000", "name": "X", "type": "response_spectrum",
                                    "loads": [{"name": "Dead", "function": "FLAT"}]})
    assert pat["_error"] and "accelerations" in pat["message"]
    h = call("define_load_case", {"software": "SAP2000", "name": "THX", "type": "linear_modal_history",
                                  "loads": [{"name": "Dead", "function": "STEP"}], "steps": 200,
                                  "step_size": 0.005, "damping": 0.0})
    b = h["read_back"]
    assert b["steps"] == 200 and b["step_size"] == 0.005 and b["damping"] == 0.0 and b["loads"][0]["name"] == "Dead"
    ns = call("define_load_case", {"software": "SAP2000", "name": "X", "type": "linear_modal_history",
                                   "loads": [{"name": "Dead", "function": "STEP"}]})
    assert ns["_error"] and "steps" in ns["message"]
    e = fresh("ETABS")
    e.Func.etabs_style = True
    call("define_function", {"software": "ETABS", "name": "STEP", "kind": "time_history", "points": [[0, 1], [10, 1]]})
    call("define_function", {"software": "ETABS", "name": "FLAT", "kind": "response_spectrum",
                             "points": [[0, 0.5], [10, 0.5]]})
    rs = call("define_load_case", {"software": "ETABS", "name": "RSX", "type": "response_spectrum",
                                   "loads": [{"kind": "acceleration", "name": "U1", "function": "FLAT",
                                              "factor": 9.80665}], "modal_combination": "srss",
                                   "directional_combination": "abs"})
    assert e.LoadCases.ResponseSpectrum.cases["RSX"]["comb"] == "SRSS" and rs["read_back"]["modal_combination"] == "srss"
    assert rs["table_edit"]["rows_changed"] == 1 and "damping_note" in rs
    dmp = call("define_load_case", {"software": "ETABS", "name": "RS2", "type": "response_spectrum", "damping": 0.02,
                                    "loads": [{"kind": "acceleration", "name": "U1", "function": "FLAT"}]})
    assert dmp["_error"] and "damping does not apply in ETABS" in dmp["message"]
    th = call("define_load_case", {"software": "ETABS", "name": "THX", "type": "linear_modal_history",
                                   "loads": [{"name": "Dead", "function": "STEP"}], "steps": 40,
                                   "step_size": 0.003})
    assert th["read_back"]["steps"] == 40 and th["read_back"]["step_size"] == 0.003
    fresh("SAFE")
    s = call("define_load_case", {"software": "SAFE", "name": "RSX", "type": "response_spectrum",
                                  "loads": [{"kind": "acceleration", "name": "U1", "function": "F"}]})
    assert s["_error"] and "not available in SAFE" in s["message"]


def test_frame_point_load_and_material_strength_read():
    sap = fresh("SAP2000")
    r = call("assign_point_or_frame_load", {"software": "SAP2000", "target_type": "frame", "object_names": ["F1"],
                                            "load_pattern": "Dead", "frame_load_shape": "point", "values": [15, 0.5],
                                            "direction": "Gravity"})
    assert r["rel_dist"] == 0.5 and sap.FrameObj.pointloads[("F1", "Dead")] == (1, 10, 0.5, 15.0, "Global")
    ag = call("get_object_assignments", {"software": "SAP2000", "object_type": "frames", "names": ["F1"]})
    assert ag["items"][0]["point_loads"][0]["value"] == 15.0
    bad = call("assign_point_or_frame_load", {"software": "SAP2000", "target_type": "frame", "object_names": ["F1"],
                                              "load_pattern": "Dead", "frame_load_shape": "point", "values": [15]})
    assert bad["_error"] and "relative distance" in bad["message"]
    m = call("get_definition_details", {"software": "SAP2000", "kind": "materials"})
    c30 = next(x for x in m["items"] if x["name"] == "C30")
    assert c30["fc"] == 30000.0 and next(x for x in m["items"] if x["name"] == "A615Gr60")["fy"] == 413685.0


def test_frame_output_stations():
    sap = fresh("SAP2000")
    r = call("assign_frame_properties", {"software": "SAP2000", "frame_names": ["F1"],
                                         "output_stations": {"min_sections": 8}})
    assert sap.FrameObj.stations["F1"][:3] == (2, 24.0, 8)
    assert r["read_back"][0]["output_stations"]["min_sections"] == 8
    bad = call("assign_frame_properties", {"software": "SAP2000", "frame_names": ["F1"], "output_stations": {}})
    assert bad["_error"]


def test_set_analysis_options():
    sap = fresh("SAP2000")
    r = call("set_analysis_options", {"software": "SAP2000", "active_dof": "plane_xz",
                                      "run_cases": {"*": False, "Dead": True}})
    assert r["after"]["active_dof"] == ["UX", "UZ", "RY"]
    assert r["after"]["run_cases"] == {"Dead": True, "Live": False, "Modal": False}
    assert sap.Analyze.dof == (True, False, True, False, True, False)
    bad = call("set_analysis_options", {"software": "SAP2000", "run_cases": {"Nope": True}})
    assert bad["_error"] and "Unknown" in bad["message"]
    snap = call("get_model_status", {"software": "SAP2000"})
    assert "_error" not in snap


def test_line_spring_define_and_assign():
    sap = fresh("SAFE")
    r = call("define_spring_property", {"software": "SAFE", "name": "EDGE", "kind": "line",
                                        "stiffness": [0, 0, 1.0e9, 5.0e5], "nonlinear": "compression_only"})
    assert "_error" not in r, r
    assert r["read_back"]["stiffness"] == [0.0, 0.0, 1.0e9, 5.0e5] and r["read_back"]["nonlinear"] == "compression_only"
    frame = sap.FrameObj.names[0]
    a = call("assign_frame_properties", {"software": "SAFE", "frame_names": [frame], "spring_property": "EDGE"})
    assert a["read_back"][0]["line_spring_property"] == "EDGE"
    bad = call("assign_frame_properties", {"software": "SAFE", "frame_names": [frame], "spring_property": "X"})
    assert bad["_error"]
    sap2 = call("assign_frame_properties", {"software": "SAP2000", "frame_names": ["F1"], "spring_property": "EDGE"})
    assert sap2["_error"]
    nope = call("define_spring_property", {"software": "SAFE", "name": "E2", "kind": "line", "stiffness": [1, 0, 5, 0]})
    assert nope["_error"] and "[0, 0, U3, R1]" in nope["message"]


def test_floor_mesh_option():
    sap = fresh("SAFE")
    r = call("set_analysis_options", {"software": "SAFE", "floor_mesh": {"type": "rectangular", "max_size": 12.0}})
    assert "_error" not in r, r
    assert r["before"]["floor_mesh"] == {"type": "general", "max_size": 1.2}
    assert r["after"]["floor_mesh"] == {"type": "rectangular", "max_size": 12.0, "localized": True,
                                        "merge_joints": True}
    assert sap.DatabaseTables.floor_mesh == ("Rectangular", "Yes", "Yes", "12.0")
    bad = call("set_analysis_options", {"software": "SAP2000", "floor_mesh": {"type": "general", "max_size": 1}})
    assert bad["_error"]


# --------------------------------------------------------------------------- 0.12.0 Phase 4: table editing
def test_edit_database_table_read_and_dry_run():
    sap = fresh("ETABS")
    r = call("edit_database_table", {"software": "ETABS", "table_key": "generic test table", "where": {"Kind": "Beam"}})
    assert r["table_key"] == "Generic Test Table" and r["matching_rows"] == 2 and r["fields"][0] == "Name"
    d = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                     "dry_run": True,
                                     "update": [{"match": {"Kind": "Beam"}, "set": {"Value": 9}, "expect": 2}],
                                     "delete": [{"match": {"Name": "C"}}], "add_rows": [{"Name": "D", "Kind": "Brace"}]})
    w = d["would"]
    assert d["dry_run"] and w["rows_changed"] == 2 and w["rows_deleted"] == 1 and w["rows_added"] == 1
    assert w["changed_preview"][0]["Value"] == "1.5 -> 9" and not hasattr(sap.DatabaseTables, "applied_generic")
    bad = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                       "update": [{"match": {"Kind": "Beam"}, "set": {"Value": 9}, "expect": 3}]})
    assert bad["_error"] and "expected 3" in bad["message"]
    bad = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                       "update": [{"match": {"Nope": 1}, "set": {"Value": 9}}]})
    assert bad["_error"] and "no fields" in bad["message"]
    bad = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                       "delete": [{"match": {"GUID": x}} for x in ("g1", "g2", "g3")]})
    assert bad["_error"] and "empty" in bad["message"]


def test_edit_database_table_apply_upsert_verify_and_refusal(tmp_path):
    sap = fresh("ETABS")
    f = tmp_path / "rows.csv"
    f.write_text("Name,Kind,Value\nA,Beam,7.25\nE,Slab,4\n", encoding="utf-8")
    r = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                     "dry_run": False, "snapshot": False, "rows_file": str(f), "key_fields": ["Name"],
                                     "upsert_rows": [{"Name": "B", "Value": 2.0}]})
    assert "_error" not in r, r
    assert r["applied"] and r["rows_changed"] == 1 and r["rows_added"] == 1  # B unchanged (2.0 == 2)
    assert r["verification"] == {"rows_checked": 2, "rows_verified": 2}
    names = [g["Name"] for g in sap.DatabaseTables.generic]
    assert names == ["A", "B", "C", "E"] and sap.DatabaseTables.generic[0]["Value"] == "7.25"
    bad = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                       "dry_run": False, "snapshot": False, "add_rows": [{"Name": "BAD"}]})
    assert bad["_error"] and "bad name" in bad["message"] and sap.DatabaseTables.pending is None
    same = call("edit_database_table", {"software": "ETABS", "table_key": "Generic Test Table", "mode": "edit",
                                        "dry_run": False, "snapshot": False,
                                        "update": [{"match": {"Name": "A"}, "set": {"Value": "7.250"}}]})
    assert same["applied"] is False
    res = call("edit_database_table", {"software": "ETABS", "table_key": "Frame Assignments - Sections",
                                       "mode": "edit", "add_rows": [{"x": 1}]})
    assert res["_error"]


# --------------------------------------------------------------------------- 0.12.0 Phase 4: geometry
def test_move_objects_moves_shared_points_and_restores_selection():
    sap = fresh("SAP2000")
    r = call("move_objects", {"software": "SAP2000", "frames": ["F1"], "offset": [0, 0, 0.5], "snapshot": False,
                              "dry_run": False})
    assert "_error" not in r, r
    assert r["points_moved"] == 2 and {m["point"] for m in r["moved"]} == {"3", "4"}
    assert r["moved"][0]["after"][2] == 3.5
    assert sap.SelectObj.sel == [(2, "F1"), (5, "A1")]          # user's selection back
    d = call("move_objects", {"software": "ETABS", "group": "EDGE", "offset": [1, 0, 0], "dry_run": True})
    assert d["dry_run"] and d["would"]["objects"] == {"points": 1, "frames": 1, "areas": 0}


def test_replicate_linear_sap_and_etabs_copy():
    fresh("SAP2000")
    r = call("replicate_objects", {"software": "SAP2000", "frames": ["F1"], "offset": [0, 4, 0], "number": 2,
                                   "snapshot": False, "dry_run": False})
    assert r["new_counts"]["frames"] == 2 and r["new_counts"]["points"] == 2   # y = 4 reuses points 5, 6
    ys = sorted({c[1] for c in r["new_point_coordinates"].values()})
    assert ys[-1] == 8.0
    sap = fresh("ETABS")
    e = call("replicate_objects", {"software": "ETABS", "frames": ["C1"], "offset": [0, 4, 0], "number": 1,
                                   "snapshot": False, "dry_run": False})
    assert e["new_counts"]["frames"] == 1 and "loads" in e["not_copied"]
    new = e["new"]["frames"][0]
    assert sap.FrameObj.sections[new] == "C500x500"
    pts = e["new"]["points"]
    assert any(sap.PointObj.restraints.get(q) == (True,) * 6 for q in pts)   # base fixity copied
    m = call("replicate_objects", {"software": "ETABS", "frames": ["C1"], "mode": "mirror",
                                   "mirror_plane": "parallel_z", "point_1": [3, 0, 0], "point_2": [3, 1, 0]})
    assert m["_error"] and "no replicate API" in m["hint"]


def test_divide_merge_extrude_connectivity():
    sap = fresh("SAP2000")
    r = call("divide_frames", {"software": "SAP2000", "frames": ["F1"], "mode": "ratio", "number": 3,
                               "snapshot": False, "dry_run": False})
    assert r["pieces_total"] == 3 and r["pieces"]["F1"][1]["i"] == (2.0, 0.0, 3.0)
    bad = call("divide_frames", {"software": "SAP2000", "frames": ["C1"], "mode": "distance", "distance": 5})
    assert bad["_error"] and "not inside" in bad["message"]
    e = call("divide_frames", {"software": "ETABS", "frames": ["C1"], "mode": "ratio"})
    assert e["_error"] and "Frame Auto Mesh" in e["hint"]
    sap.PointObj.AddCartesian(0.0, 0.0, 3.001, "", "Q")
    d = call("merge_points", {"software": "SAP2000", "tolerance": 0.01, "dry_run": True})
    assert ["3", "Q"] in d["would"]["close_pairs"]
    m = call("merge_points", {"software": "SAP2000", "tolerance": 0.01, "snapshot": False, "dry_run": False})
    assert m["points_removed"] == ["Q"]
    x = call("extrude_objects", {"software": "SAP2000", "kind": "points_to_frames", "names": ["5"],
                                 "section": "B300x600", "offset": [0, 0, -1.5], "number": 2, "snapshot": False,
                                 "dry_run": False})
    assert x["new_total"] == 2
    c = call("change_connectivity", {"software": "SAP2000", "frame": "C2", "points": ["2", "6"], "snapshot": False,
                                     "dry_run": False})
    assert c["connected_to"][1] == {"point": "6", "xyz": (6.0, 4.0, 3.0)}
    a = call("change_connectivity", {"software": "ETABS", "area": "A1", "points": ["3", "4", "6"]})
    assert a["_error"]


# --------------------------------------------------------------------------- 0.12.0 Phase 4: spec builder
def _example():
    import copy
    import model_spec
    ex = copy.deepcopy(model_spec.EXAMPLE)
    ex["materials"][0]["replace"] = True          # the fake model already has a C30
    return ex


def test_spec_validation_reports_every_problem_before_changing_anything():
    sap = fresh("SAP2000")
    n_frames = len(sap.FrameObj.names)
    spec = _example()
    spec["frames"].append({"name": "B9", "start": "3", "end": "99", "section": "NOPE"})
    spec["loads"][0]["load_pattern"] = "SNOW"
    spec["materials"][0]["E"] = "stiff"
    spec["bogus"] = []
    r = call("build_model_from_spec", {"software": "SAP2000", "spec": spec})
    assert r["_error"] and "nothing was changed" in r["message"]
    for bit in ("NOPE", "'99'", "SNOW", "materials[0]", "bogus"):
        assert bit in r["hint"], bit
    assert len(sap.FrameObj.names) == n_frames


def test_spec_dry_run_and_build(tmp_path):
    sap = fresh("SAP2000")
    d = call("build_model_from_spec", {"software": "SAP2000", "spec": _example(), "dry_run": True})
    assert d["dry_run"] and d["would"]["calls_by_section"]["frames"] == 2   # two sections -> two create calls
    spec = _example()
    for q in spec["points"]:                                               # fake model already has points 1-4
        q["name"] = "N" + q["name"]
        q["xyz"] = [q["xyz"][0] + 20, q["xyz"][1], q["xyz"][2]]
    for f in spec["frames"]:
        f["start"], f["end"] = "N" + f["start"], "N" + f["end"]
        f["name"] = "X" + f["name"]
    spec["loads"][0]["object_names"] = ["XB1"]
    spec["loads"][1]["object_names"] = ["N3"]
    r = call("build_model_from_spec", {"software": "SAP2000", "spec": spec})
    assert "_error" not in r, r
    assert r["completed"], r
    assert {"XC1", "XC2", "XB1"} <= set(sap.FrameObj.names) and sap.FrameObj.sections["XB1"] == "BM300x600"
    assert sap.PointObj.restraints["N1"] == (True, True, True, False, False, False)
    assert r["counts_now"]["frames"] == r["counts_before"]["frames"] + 3


def test_spec_name_clash_stops_and_xlsx_template_round_trip(tmp_path):
    sap = fresh("SAP2000")
    spec = _example()          # its points 1-4: the fake model's 1 and 2 sit at (0,0,0) / (6,0,0) -> reused
    spec["points"][2]["xyz"] = [0, 0, 3]                                   # point "3" there already, as "3"
    spec["points"][3] = {"name": "Z4", "xyz": [0, 4, 3]}                   # fake point 5 is there
    spec["frames"] = [{"name": "ZB", "start": "3", "end": "Z4", "section": "BM300x600"}]
    spec["loads"] = []
    r = call("build_model_from_spec", {"software": "SAP2000", "spec": spec})
    assert r["completed"] is False and "names not kept" in r["failed_step"]["error"] and "Z4" in r["failed_step"]["error"]
    t = call("build_model_from_spec", {"software": "SAP2000", "write_template": str(tmp_path / "t.xlsx")})
    assert "points" in t["sheets"]
    import model_spec
    back = model_spec.read_spec_file(str(tmp_path / "t.xlsx"))
    import copy
    ex = copy.deepcopy(model_spec.EXAMPLE)
    assert back["units"] == "kN_m_C" and back["points"][0] == ex["points"][0]
    assert back["combinations"] == ex["combinations"] and back["loads"][1]["values"] == [20, 0, 0, 0, 0, 0]
    assert back["materials"][0]["E"] == 2.6e7


def test_new_model_guards_open_model(tmp_path):
    sap = fresh("SAP2000")
    r = call("new_model", {"software": "SAP2000", "file_path": str(tmp_path / "a.sdb")})
    assert r["_error"] and "discard_open_model" in r["hint"]
    r = call("new_model", {"software": "SAP2000", "file_path": str(tmp_path / "a.edb"), "discard_open_model": True})
    assert r["_error"] and ".sdb" in r["message"]
    r = call("new_model", {"software": "SAP2000", "file_path": str(tmp_path / "a.sdb"), "kind": "grid_only",
                           "discard_open_model": True})
    assert r["_error"]
    ok = call("new_model", {"software": "SAP2000", "file_path": str(tmp_path / "a.sdb"), "discard_open_model": True,
                            "dry_run": True})
    assert ok["dry_run"]


def test_spec_clash_with_existing_definition_listed_upfront():
    fresh("SAP2000")
    spec = _example()
    spec["load_patterns"][0]["name"] = "dead"       # the fake model has 'Dead'
    r = call("build_model_from_spec", {"software": "SAP2000", "spec": spec, "dry_run": True})
    assert r["_error"] and "load pattern 'Dead'" in r["hint"] and "replace: true" in r["hint"]
    d = call("define_load_pattern", {"software": "SAP2000", "name": "DEAD", "type": "dead"})
    assert d["_error"] and "upper / lower case" in d["message"]


def test_edit_database_table_refuses_a_stale_table():
    sap = fresh("SAP2000")
    assert len(sap.LoadPatterns.names) > 1
    r = call("edit_database_table", {"software": "SAP2000", "table_key": "Load Pattern Definitions", "mode": "edit",
                                     "update": [{"match": {"LoadPat": sap.LoadPatterns.names[0]},
                                                 "set": {"SelfWtMult": 0.5}}]})
    assert r["_error"] and "would delete them" in r["message"] and sap.LoadPatterns.names[1] in r["message"]
    assert getattr(sap.DatabaseTables, "pending", None) is None


# --------------------------------------------------------------------------- 0.13.0 Phase 6: design
def test_design_settings_read_and_set_with_read_back():
    sap = fresh("ETABS")
    combo = sap.RespCombo.names[0]
    g = call("get_design_settings", {"software": "ETABS", "kind": "steel_frame"})
    assert g["code"] == "AISC 360-16" and g["preferences"]["PatLLF"] == "0.75"
    r = call("set_design_settings", {"software": "ETABS", "kind": "steel_frame", "preferences": {"SRatioLimit": 0.9},
                                     "strength_combinations": {combo: True}})
    assert "_error" not in r, r
    assert r["settings_now"]["preferences"]["SRatioLimit"] == "0.9" and combo in r["settings_now"]["combinations"]["strength"]
    bad = call("set_design_settings", {"software": "ETABS", "kind": "steel_frame", "preferences": {"Nope": 1}})
    assert bad["_error"] and "no fields" in bad["message"]
    bad = call("set_design_settings", {"software": "ETABS", "kind": "steel_frame", "code": "Steel Code 9000"})
    assert bad["_error"] and sap.DesignSteel.code == "AISC 360-16"
    ok = call("set_design_settings", {"software": "ETABS", "kind": "steel_frame", "code": "AISC 360-22"})
    assert ok["done"]["code"] == "AISC 360-22"
    bad = call("set_design_settings", {"software": "ETABS", "kind": "steel_frame",
                                       "strength_combinations": {"NOPE": True}})
    assert bad["_error"]
    s = call("get_design_settings", {"software": "SAP2000", "kind": "shear_wall"})
    assert s["_error"]


def test_run_design_and_results():
    sap = fresh("ETABS")
    r = call("run_design", {"software": "ETABS", "kind": "steel_frame"})
    assert r["_error"] and "combinations" in r["message"]          # nothing chosen for the designer
    sap.DesignSteel.strength.append(sap.RespCombo.names[0])
    r = call("run_design", {"software": "ETABS", "kind": "steel_frame"})
    assert r["_error"] and "no analysis results" in r["message"]
    sap.locked = True
    r = call("run_design", {"software": "ETABS", "kind": "steel_frame"})
    assert "_error" not in r, r
    assert r["code"] == "AISC 360-16" and r["failing_members"] == ["C1"]
    res = call("get_design_results", {"software": "ETABS", "kind": "steel_frame"})
    assert res["governing"][0] == {"frame": "C1", "ratio": 1.12} and res["over_1"] == ["C1"]
    assert res["steel"][0]["DesignSection"]
    sap.DesignConcrete.ran = True
    c = call("get_design_results", {"software": "ETABS", "kind": "concrete_frame", "names": ["F1", "C1"]})
    assert c["beams"][0]["BotArea"] == 0.0012 and c["columns"][0]["PMMRatio"] == 0.95


def test_export_report_design_section(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path))
    sap = fresh("ETABS")
    sap.DesignSteel.ran = True
    r = call("export_report", {"software": "ETABS", "sections": ["design"], "output_path": str(tmp_path / "d.pdf")})
    assert "_error" not in r, r
    assert r["figures"] >= 3 and r["pages"] >= 1
    import pytest
    PdfReader = pytest.importorskip("pypdf").PdfReader
    text = "".join(pg.extract_text() for pg in PdfReader(str(tmp_path / "d.pdf")).pages)
    assert "Design - steel frame" in text and "AISC 360-16" in text and "1.120" in text


def test_program_default_combinations_only_when_the_designer_says():
    sap = fresh("ETABS")
    sap.locked = True
    r = call("run_design", {"software": "ETABS", "kind": "steel_frame"})
    assert r["_error"] and "program_default_combinations" in r["hint"]
    r = call("run_design", {"software": "ETABS", "kind": "steel_frame", "program_default_combinations": True})
    assert "_error" not in r and r["uses_program_default_combinations"], r


def test_phase5_stories_diaphragm_piers():
    sap = fresh("ETABS")
    r = call("define_stories", {"software": "ETABS", "base_elevation": 0.0,
                                "stories": [{"name": "GF", "height": 4.0}, {"name": "L1", "height": 3.2}]})
    assert "_error" not in r, r
    assert [(s["name"], s["elevation"]) for s in r["read_back"]["stories"]] == [("GF", 4.0), ("L1", 7.2)]
    bad = call("define_stories", {"software": "ETABS", "stories": [{"name": "A", "height": 3},
                                                                   {"name": "B", "height": 3, "similar_to": "X"}]})
    assert bad["_error"]
    r = call("define_diaphragm", {"software": "ETABS", "name": "D2", "semi_rigid": True, "areas": ["A1"]})
    assert "_error" not in r and r["read_back"]["areas"] == {"A1": "D2"}, r
    assert call("define_diaphragm", {"software": "ETABS", "name": "d2"})["_error"]   # case twin
    r = call("assign_pier_spandrel", {"software": "ETABS", "kind": "pier", "label": "P1", "areas": ["A1"]})
    assert "_error" not in r and r["read_back"] == {"A1": "P1"} and "P1" in sap.PierLabel.names, r
    assert call("define_stories", {"software": "SAP2000", "stories": [{"name": "A", "height": 3}]})["_error"]


def test_phase5_auto_seismic_and_links():
    sap = fresh("ETABS")
    sap.LoadPatterns.Add("EQX", 5)
    p = {"software": "ETABS", "load_pattern": "EQX", "code": "ASCE 7-16", "directions": ["X", "X+e"],
         "period": "approximate", "R": 8, "omega": 3, "Cd": 5.5, "importance": 1.0, "Ss": 1.5, "S1": 0.6,
         "TL": 8, "site_class": "D"}
    r = call("set_auto_seismic", p)
    assert "_error" not in r, r
    assert r["read_back"]["R"] == 8 and r["read_back"]["site_class"] == "D" and r["read_back"]["period"] == "approximate"
    assert r["read_back"]["stories"]["top"] == "Story3" and r["read_back"]["stories"]["bottom"] == "Base"
    assert "defaulted" in r["read_back"]["stories"]
    assert call("set_auto_seismic", {**p, "top_story": "Nope"})["_error"]
    assert call("set_auto_seismic", {**p, "load_pattern": "Dead"})["_error"]          # not a quake pattern
    assert call("set_auto_seismic", {**p, "site_class": "F"})["_error"]               # F needs Fa / Fv
    r = call("define_link_property", {"software": "ETABS", "name": "K1", "stiffness": {"U1": 1000.0},
                                      "fixed": ["U2"]})
    assert "_error" not in r and r["read_back"]["stiffness"] == {"U1": 1000.0} and r["read_back"]["fixed"] == ["U2"]
    r = call("create_links", {"software": "ETABS", "property": "K1", "links": [{"point1": "1", "point2": "2"},
                                                                              {"point1": "1"}]})
    assert "_error" not in r and r["created"][1]["one_joint"] and r["created"][0]["point2"] == "2", r
    assert call("create_links", {"software": "ETABS", "property": "K1", "links": [{"point1": "zz"}]})["_error"]


def test_014_seismic_coefficient_sap_and_etabs():
    for sw in ("SAP2000", "ETABS"):
        sap = fresh(sw)
        sap.LoadPatterns.Add("EQX", 5)
        r = call("set_seismic_coefficient", {"software": sw, "load_pattern": "EQX", "direction": "X", "C": 0.0825,
                                             "K": 1.0})
        assert "_error" not in r and r["read_back"]["C"] == 0.0825, r
        assert call("set_seismic_coefficient", {"software": sw, "load_pattern": "Dead", "direction": "X",
                                                "C": 0.1})["_error"]
    assert call("set_seismic_coefficient", {"software": "SAFE", "load_pattern": "EQX", "direction": "X",
                                            "C": 0.1})["_error"]


def test_014_gap_link_and_nonlinear_case():
    sap = fresh("SAP2000")
    r = call("define_link_property", {"software": "SAP2000", "name": "G1", "type": "gap", "stiffness": {"U1": 0.0},
                                      "fixed": ["U2"], "nonlinear": {"U1": {"stiffness": 1000, "opening": 0.01}}})
    assert "_error" not in r and r["read_back"]["type"] == "gap", r
    assert r["read_back"]["nonlinear"] == {"U1": {"stiffness": 1000.0, "opening": 0.01}}
    r = call("define_link_property", {"software": "SAP2000", "name": "M1", "type": "multilinear_elastic",
                                      "nonlinear": {"U1": {"points": [[-0.1, -50], [0, 0], [0.1, 50]]}}})
    assert "_error" not in r and len(r["read_back"]["nonlinear"]["U1"]["points"]) == 3, r
    assert call("define_link_property", {"software": "SAP2000", "name": "M2", "type": "multilinear_elastic",
                                         "nonlinear": {"U1": {"points": [[-0.1, -50], [0.1, 50]]}}})["_error"]
    assert call("define_link_property", {"software": "SAP2000", "name": "G2", "type": "gap",
                                         "nonlinear": {"U1": {"stiffness": 1}}})["_error"]
    r = call("define_load_case", {"software": "SAP2000", "name": "NL", "type": "nonlinear_static",
                                  "loads": [{"name": "Dead", "factor": 1.0}], "geometric_nonlinearity": "p_delta"})
    assert "_error" not in r, r
    rb = r["read_back"]
    assert rb["type"] in ("NonlinearStatic", "nonlinear_static", "StaticNonlinear") or rb["geometric_nonlinearity"] == "p_delta", rb
    assert rb["geometric_nonlinearity"] == "p_delta" and rb["loads"][0]["name"] == "Dead", rb


def test_contour_scales_match_programs():
    import diagrams as dg
    # SAP2000 27: M11 -33.70 / 16.44 -> 15 bands -35 ... 17.5 by 3.5 (labels 14 ... -31.5)
    b = dg.csi_bounds(-33.701, 16.441, "sap")
    assert len(b) == 16 and b[0] == -35 and b[-1] == 17.5 and b[1:-1][-1] == 14
    # ETABS 23: M11 -31.57 / 16.58 -> -32 ... 28 by 4; F22 -> -220 ... 80 by 20
    b = dg.csi_bounds(-31.566, 16.584, "etabs")
    assert b[0] == -32 and b[-1] == 28
    b = dg.csi_bounds(-213.6, 66.0, "etabs")
    assert b[0] == -220 and b[-1] == 80
    assert len(dg.CSI15) == 15


def test_wind_params():
    import pytest
    from pydantic import ValidationError
    from program_specific import MPH, WindP
    base = dict(software="SAP2000", load_pattern="WX", code="ASCE 7-16", asce_case="1", wind_speed=40,
                wind_speed_unit="m/s", exposure_category="C", Kzt=1, gust_factor=0.85, Kd=0.85)
    WindP(**base, Cpw=0.8, Cpl=0.5)
    with pytest.raises(ValidationError):
        WindP(**base, Cpw=0.8)                       # Cpw without Cpl
    assert abs(40 * MPH["m/s"] - 89.4775) < 1e-3
