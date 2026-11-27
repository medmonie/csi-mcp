# CSI MCP - ETABS, SAP2000 and SAFE for Claude

A Model Context Protocol (MCP) server that lets Claude (Claude Desktop, Claude Code, Codex) work with **CSI ETABS,
SAP2000 and SAFE** on this Windows PC through the CSI Open API (COM). It reads models and results, creates frames,
assigns loads, runs the analysis and summarises results, without a plug-in inside the programs.

It follows the same pattern as the `civil3d` and `revit` MCP servers: a catalogue of actions with `help`, live test
scripts that write to `tests\results\`, and one-exe installer packages. Since 0.7.0 there is **one server per
program**, each with one tool named after it: **`etabs(action, params)`, `sap2000(action, params)`,
`safe(action, params)`**. Each lists only what its program's API can do, in that program's terms, and fills
`software` by itself. The three are built from one code base, so a fix reaches all of them.

| Tested on (26 Nov 2026) | Version | Attach to open program | Full edit / analysis / results cycle |
|---|---|---|---|
| SAP2000 | 27.1.0 | OK | OK |
| ETABS | 23.2.0 | OK | OK |
| SAFE | 23.3.2 | OK | OK |

Windows 11, Python 3.15.1 64-bit, `mcp` 1.30.0, comtypes 1.5.0, pandas 3.1.1, reportlab 5.1.2. Version 0.15.2
(roadmap Phases 0-2 with V1 verification: 238 / 238 values match CSI's published results: foundation, full model read, results depth; report stages R1-R3; Phase 3 first batch:
area mesh, joint patterns, area loads; 0.7.0: one server per program; 0.8.0: definitions - materials, sections,
springs, patterns, cases, combinations, mass source; 0.9.0: objects - points, frames, areas, their assignments,
temperature loads, groups, selection, delete; 0.10.0: response-spectrum / time-history functions and cases - Phase 3
complete; 0.11.0: V2 - three CSI examples rebuilt through the actions alone match CSI's published values 30 / 30,
analysis options, frame point loads, line springs; 0.12.0: Phase 4 - database-table editing, geometry operations,
new models and models from a JSON / XLSX spec; 0.13.0: Phase 6 - design with the program's own engines
(configure, run, results as tables, charts and D/C diagrams; V4: CSI's ETABS design examples match) and Phase 5 first
batch - ETABS stories, diaphragms, piers / spandrels, ASCE 7-16 automatic seismic loads, links); next: rest of Phase 5.

## Install

**Packages (no Python needed):** `EtabsMcp.zip`, `Sap2000Mcp.zip`, `SafeMcp.zip` - install the ones you use. Quit
Claude Desktop (tray icon > Quit), unzip, double-click `install.bat`, start Claude again. Each adds its own server
(`etabs`, `sap2000`, `safe`) to Claude Desktop and Codex and removes the old combined `csi` entry of 0.6.0 and
earlier. See `package/INSTALL.txt`.

**From source:**

1. Double-click `setup_server.bat` (needs 64-bit Python 3.10+). It creates `server\.venv`, installs the packages,
   pre-generates the comtypes wrappers for the CSI type libraries and writes `claude_config_snippet.json` and
   `codex_config_snippet.toml`. Log: `setup_result.txt`.
2. Add the entries you need (`etabs`, `sap2000`, `safe`, each `server.py --program <PROGRAM>`) to Claude: Settings >
   Developer > Edit Config, next to `civil3d` and `revit`, or let the server do it (backs up and merges the file,
   keeps your other servers, removes an old `csi` entry):

   ```
   server\.venv\Scripts\python server\server.py --program ETABS --configure-clients
   ```

3. Quit Claude Desktop completely and start it again. Open ETABS, SAP2000 or SAFE with a model and ask:
   *"Check the ETABS model status."*

## The tools

One server per program (`server/edition.py`): the packaged exe takes its program from its name
(`etabs-mcp-server.exe` ...), `server.py` from `--program` or `CSI_MCP_PROGRAM`. The tool is named after the
program: `etabs(action, params)`, `sap2000(...)`, `safe(...)`. With `CSI_MCP_SPLIT=1` there are two tools,
`etabs_read` (marked read-only) and `etabs_edit`, so reading can be allowed without allowing changes. `server.py
--chatgpt` serves the same split tools (short descriptions) over stdio for ChatGPT through a Secure MCP Tunnel.

Each edition lists only its program's actions, parameters and option values, worded for that program (for example
`set_area_mesh`, `define_joint_pattern` and `surface_pressure` only in SAP2000; `get_stories`, story drifts and labels
in ETABS / SAFE; piers and spandrels in ETABS). `software` is filled in; another program is refused with the name of
its tool; an option the program's API does not have is refused with the nearest alternative. Parameter lists:
[docs/ACTIONS.md](docs/ACTIONS.md) (one generated page per program), or `action='help'`.

`server.py` without `--program` serves all three programs in one tool `csi` with `params.software` (development and
offline tests only; `CSI_MCP_PROGRAMS` limits it); it is not packaged.

| Read | Edit |
|---|---|
| `help`, `list_programs`, `get_model_status`, `list_definitions`, `list_available_tables`, `query_database_table`, `get_analysis_summary`, `get_results`, `get_analysis_log`; model reading: `get_grids`, `get_stories`, `get_groups`, `get_objects`, `get_object_assignments`, `get_definition_details`, `get_load_case_details`, `get_combo_details`, `get_mass_source`, `get_selected`; output (writes files only): `snapshot_model`, `export_model_snapshot`, `export_report` | `connect`, `disconnect`, `open_or_save_model`, `set_units`, `create_frame_element`, `assign_point_or_frame_load`, `run_analysis`; definitions: `define_material`, `define_frame_section`, `define_area_section`, `define_spring_property` (ETABS / SAFE), `define_load_pattern`, `define_load_case`, `define_combination`, `set_mass_source`, `define_function` (SAP2000 / ETABS); objects: `create_points`, `create_frames`, `create_areas`, `assign_point_properties`, `assign_frame_properties`, `assign_area_properties`, `assign_temperature_load`, `set_group`, `select_objects`, `delete_objects`; modelling: `set_area_mesh`, `define_joint_pattern` (SAP2000), `assign_area_load` |

`get_analysis_summary` gives `base_reactions` (governing max / min per component and case; modal rows excluded,
since mode shapes have arbitrary scale), `story_drifts` (ETABS: governing, per direction, per-story envelope, optional
`drift_limit` from the engineer), `modal_periods` (periods, dominant mode per direction, cumulative mass and the mode
reaching 90 %) and `joint_displacements` (peak per component with location, top 10 vertical).

`query_database_table` reads any table with case / combination selection, column filters (`'Story3'`,
`'Story3|Story4'`, `'~DCON'`, `'>=0.002'`), sorting and `max_rows`; it always returns the total and filtered row counts
and the min / max of every numeric column with where it occurs, so a large table never floods the conversation.

Model reading (0.4.0) returns the model as it is: `get_objects` (points with coordinates and supports, frames with
section, end points, length and vertical / horizontal / inclined, areas with property, corners, area and level; ETABS
/ SAFE label and story, `story` filter), `get_object_assignments` (restraints, springs, releases, end offsets,
insertion point, modifiers, local axes, diaphragm, pier / spandrel, groups and the loads on each object; defaults
left out), `get_definition_details` (materials with E, Poisson, weight; frame sections with dimensions; area sections
with thickness; springs; load patterns), cases with their loads, combinations with factors, grids, stories, groups,
mass source and the user's selection. ETABS and SAFE are read with the bulk API calls, SAP2000 object by object.
`export_model_snapshot` writes the whole model, assignments and loads included, to one JSON file.

Results (0.5.0): `get_results` reads typed results through the program's results API - joint displacements /
reactions, frame forces at stations, frame joint forces, shell forces / stresses, mode shapes, base reactions, modal
periods / mass ratios / participation factors, buckling factors, story / joint drifts and pier / spandrel forces
(ETABS), section cuts - for every object in one call (group 'All') or for named objects / a group, with the cases and
combinations asked for and `step_option` envelope / step-by-step / last step. It returns the governing max / min /
|max| per component with where and in which case, optionally an envelope per object, and writes long results to
CSV / XLSX / JSON. The output selection the user had in the program is put back. `get_analysis_log` reads the
solver's `.LOG` / `.OUT`: stability check (negative eigenvalues), warnings, cases solved, modes and periods.

`export_report` writes a PDF of the model and its results: cover (project fields), model (object counts, case status,
project information), **diagrams** (3-D overview, plan per story / level with grids, section names, columns and
supports, elevation per grid line, one load view per pattern with the values; `diagrams`, `diagram_patterns`,
`max_plans` choose them), **result diagrams** for one case / combination (deformed shape, support reactions, frame
force diagrams per elevation - by default each member's in-plane bending moment, M3 or M2 from its local axes - shell
contours per level, ETABS story drift and shear charts), definitions (materials, sections, load patterns and cases with type, combinations with
factors), any database tables asked for, and headline results (base reactions, story drifts in ETABS, modal periods
and mass, peak joint displacements). Program / version, model file and units are on every page. **A4 portrait by
default**; `page_size` (A3, A2, Letter, Legal) and `orientation` (landscape, auto) change it per request. Wide tables
are split into column groups that repeat the identifying columns; tables longer than `max_rows_per_table` are cut in
the PDF and written in full to a CSV next to it. Default location `Documents\CsiMcp\reports`.

## Output folder

`CSI_MCP_OUTPUT`, else `Documents\CsiMcp`: `reports\` (PDFs), `exports\` (full tables: `query_database_table` writes
one automatically when the rows are cut to `max_rows`, or always with `export: csv | json`), and `change_log.jsonl`.

## Behaviour that protects the model

- **Dry run on every edit.** `dry_run: true` checks everything (names, sections, patterns, lock state, cases to run,
  files) and reports what the edit would do; nothing changes and nothing is logged. Checked live: object counts
  identical before and after.
- **Snapshots.** `snapshot: true` on create_frame_element, assign_point_or_frame_load and run_analysis, and the
  `snapshot_model` action, copy the model file as last saved to `_csi_mcp_snapshots\<model>_<date-time>.<ext>` next to
  the model. The program is not touched (no Save As), so changes not yet saved are not in the copy.
- **Change log.** Every edit that runs adds one line to `change_log.jsonl`: time, action, program, model file,
  parameters, ok / failed and the outcome.

- **Units on every call.** Actions take `units` (default `kN_m_C`), set them, work, and put the user's display units
  back (checked live: ETABS left in N_mm_C stays N_mm_C). Only `set_units` changes units on purpose.
- **No silent unlocking.** A model with results is locked; changing it returns `confirmation_required` unless the call
  carries `allow_unlock: true`, which the tool description reserves for when the user has agreed (results are deleted).
- **Every return code is checked**, and load assignment reports success or failure per object. ETABS and SAFE return
  0 for a point / frame name that does not exist, so names are checked against the model first.
- **Display selection put back and verified.** Result tables need cases / combinations "selected for display"; the
  server changes the selection only when it differs, reads, restores it and reads it back. If the selection cannot be
  read first, the table is not read. A selection that was empty and cannot be emptied again (SAFE) comes back as a
  warning; any other restore failure is an error. Rows are also filtered in Python.
- **Strict parameters.** No type coercion (`"5"` is not 5), no NaN / infinity, each object name once per load call.
- **One action at a time across clients.** A Windows mutex stops two CSI MCP processes (e.g. Claude and Codex) driving
  CSI at the same time: the second gets "busy", nothing starts. Within one action the program instance is pinned; an
  edit never silently re-attaches to a different instance.
- **Analysis verified per case.** `run_analysis` needs at least one case set to run and fails unless every one of
  them reports Finished. A lock state that cannot be read blocks edits.
- **Launch path limited.** `connect` with `program_path` launches only `ETABS.exe` / `SAP2000.exe` / `SAFE.exe` under
  `%ProgramFiles%\Computers and Structures`, or the exact path set in `CSI_MCP_ETABS_EXE` / `CSI_MCP_SAP2000_EXE` /
  `CSI_MCP_SAFE_EXE`.
- **No design.** The server models, runs and reads. Sections, loads, combinations and limits come from the user.
- **One COM thread.** All CSI calls run in order on one STA thread that initialises COM once; async MCP requests wait
  for it, so there are no `CoInitialize` / wrong-thread errors. Long actions (analysis) send a progress heartbeat
  every 10 s when the client asks for progress. Logging goes to stderr; the protocol pipe is reserved at start-up, so
  even native writes from the CSI libraries cannot corrupt it. Incoming messages are size-limited (1 MB), validated,
  and tool calls are rate-limited (burst 32, 8 / s).

## Tests

| Script | What it does | Output |
|---|---|---|
| `tests\run_offline.bat` | pytest against a fake CSI model (no program needed) | `tests\results\offline.txt` |
| `tests\run_live_read.bat [ETABS] [SAP2000] [SAFE]` | every read action against the OPEN programs; checks units and display selection are unchanged | `tests\results\live_read.txt` |
| `tests\run_live_edit.bat [..] [--hidden]` | in NEW scratch instances (your models are not touched): template model, save, frame, point + frame loads (with a wrong name on purpose), units round trip, analysis, lock guard, summaries, filtered queries, close without saving | `tests\results\live_edit.txt` |
| `tests\run_protocol.bat` | starts `server.py` over stdio like Claude Desktop and checks tools, help, validation | `tests\results\protocol.txt` |
| `tests\run_exe_check.bat [exe]` | the same against the three packaged editions (tool name, program-only text, `software` filled in, another program refused) | `tests\results\exe_check.txt` |
| `tests\run_live_phase0.bat [..] [--hidden]` | 0.3.0 in NEW scratch instances: builds the reference models (saved to `tests\reference_models\`), dry runs change nothing and are not logged, snapshot + change log, analysis, stability check (< 1 m), automatic CSV export, reports A4 portrait and A3 landscape | `tests\results\live_phase0.txt`, PDFs in `tests\results\reports\` |
| `tests\run_live_phase1.bat [..] [--hidden]` | 0.4.0 in NEW scratch instances: builds the reference models and reads them back with every Phase 1 action, compared with the programs' own per-object answers (every point, frame end point, area corner, support, spring, load, section, combination factor), model JSON, report with diagrams | `tests\results\live_phase1.txt`, PDFs in `tests\results\reports\` |
| `tests\run_live_phase2.bat [..] [--hidden]` | 0.5.0 in NEW scratch instances: every result type on the reference models with hand checks (live-load base reaction = applied load, joint reactions add up, beam end shears differ by wL, periods = solver log), output selection unchanged, XLSX export, analysis log, reports with result diagrams | `tests\results\live_phase2.txt`, PDFs in `tests\results\reports\` |
| `tests\run_live_phase3.bat [..] [--hidden]` | 0.6.0 in NEW scratch instances: SAP2000 hydrostatic cantilever wall (joint pattern + surface pressure) checked against statics for meshes 4x8 / 8x16 / 16x32 and after a 2x2 divide (base fixity carried to the new joint); ETABS / SAFE uniform area load = base reaction; program refusals | `tests\results\live_phase3.txt` |
| `tests\run_live_phase3b.bat [..] [--hidden]` | 0.8.0 in NEW scratch instances: every definition made through the actions and read back (materials, frame / area sections, springs, patterns, cases, combinations, mass source); a 4 m cantilever of the new section: tip deflection = PL^3/3EI (4.000 mm), case x 2, combination 1.4 x, envelope; first period = 2 pi sqrt(m/k) (0.1269 s) with mass only from a load pattern; program refusals | `tests\results\live_phase3b.txt` |
| `tests\run_live_phase3c.bat [..] [--hidden]` | 0.9.0 in NEW scratch instances: objects and assignments made through the actions against hand values - releases (PL/4, PL^3/48EI), fixed bar under +30 deg (-E A alpha dT) and gradients (E I alpha g), point spring (P / (12EI/L^3 + k)), slab on subgrade (q / ks), API-built ETABS wall (stable, base shear); groups, selection, delete; read back of all assignments | `tests\results\live_phase3c.txt` |
| `tests\run_live_phase3d.bat [..] [--hidden]` | 0.10.0 in NEW scratch instances: single-mass cantilever, flat spectrum 0.5 g (base shear m Sa, displacement Sa / w^2) and step load in a linear modal history (peak 2F/k undamped in SAP2000, (1 + e^(-pi z / sqrt(1-z^2))) F/k at 5 % in ETABS); ETABS functions and case options through database tables; SAFE refusals | `tests\results\live_phase3d.txt` |
| `tests\run_live_phase4.bat [..] [--hidden]` | 0.12.0 in NEW scratch instances: new_model, spec build (portal / slab) and a refused spec, Load Pattern table edit (LL = 60 + 0.5 DEAD; SAP2000 stale table refused), units of table values, SAP2000 divide / replicate / mirror / radial / merge / extrude, ETABS / SAFE move and copy | `tests\results\live_phase4.txt` |
| `tests\run_live_phase56.bat [..] [--hidden]` | 0.13.0 in NEW scratch instances: SAP2000 links (U = F / k, two-joint and one-joint), ETABS stories / diaphragm / pier read back, ASCE 7-16 auto seismic (Cs = 0.08 by hand, base shear = Cs W), steel beam design with the designer's code AISC 360-16 and combination (preference round trip, LTB overwrite: ratio = Mu / phi Mp = 0.2326), design report; SAFE settings and refusals | `tests\results\live_phase56.txt`, PDFs in `tests\results\phase56_output\` |
| `tests\run_live_phase5b.bat [..] [--hidden]` | 0.14.0: SAP2000 gap / hook / multi-linear links in a nonlinear static case (U by hand, exact), user-coefficient seismic V = C W in SAP2000 and ETABS (8.25 kN) | `tests\results\live_phase5b.txt` |
| `tests\run_verify_v4_safe.bat [--hidden]` | V4 SAFE: CSI's ACI 318-19 RC-SL Ex001 (strip moment, As) and RC-PN Ex001 (punching at grid B-2: stress, capacity, D/C) through the design actions | `tests\results\verify_v4_safe.txt`, `tests\results\verify\v4_safe\` |
| `tests\run_live_diagrams.bat [..] [--keep-open]` | 0.15.0: result diagrams on multi-bay, multi-storey models (12 slabs, a wall) in SAP2000 and ETABS and a SAFE example copy - frame M / V / P per elevation, slab and wall contours - written as reports to compare with the programs' own displays; models kept. `run_live_diagrams_b.bat` writes to `diagrams_output_b` (use it while a model in `diagrams_output` is open) | `tests\results\live_diagrams.txt`, `tests\results\diagrams_output\` |
| `tests\run_verify_v4.bat [--hidden]` | V4: copies of CSI's ETABS design examples (ACI 318-19 Ex001 / Ex002, AISC 360-16 Ex001 / Ex002) designed through the actions with the code and combinations they hold, compared with CSI's published values; design report per example | `tests\results\verify_v4.txt`, `tests\results\verify\v4\` |
| `tests\run_diag_phase5.bat` | raw API: SetASCE716 vs SetASCE716_1 in ETABS / SAP2000, auto seismic tables, SAP2000 design table names | `tests\results\diag_phase5.txt` |
| `tests\run_verify_v1.bat [..] [--hidden] [--only "Example 1-001"]` | V1: 38 of CSI's own verification models (copies) analysed and read through the MCP, 238 values compared with CSI's published program values (needs the "CSI Solved Examples" folder; expected values in `tests\verify\expected_v1.py`, local only) | `tests\results\verify_v1.txt`, `tests\results\verify\v1_results.csv` |
| `tests\run_verify_v2.bat [SAP2000] [ETABS] [SAFE] [--hidden]`, `run_verify_v2_safe.bat` | V2: copies of SAP2000 1-001, ETABS Ex 01 and SAFE S03a read with `export_model_snapshot`, rebuilt in a blank model with the edit actions only, analysed and graded against CSI's published values; every raw API difference original -> rebuilt listed | `tests\results\verify_v2.txt`, `tests\results\verify\v2\` |
| `tests\run_verify_inventory.bat` | cases, joints and frames of the V1 models, used to write the expected values | `tests\results\verify_inventory.txt` |
| `tools\run_api_matrix.bat` | API capability matrix from the installed type libraries + run-time probe of every SapModel object (`docs\API_MATRIX.md`, `docs\api\`) | `tests\results\api_matrix.txt` |
| `tests\run_diag_safe.bat`, `run_diag_safe_ref.bat`, `run_diag_etabs_ref.bat` | raw API diagnostics kept for the findings below | `tests\results\diag_*.txt` |

## CSI API facts found while testing (ETABS 23.2, SAP2000 27.1, SAFE 23.3)

- SAFE registers as **`CSI.SAFE.API.ETABSObject`**; `CSI.SAFE.API.SAFEObject` is not a registered class.
- ETABS and SAFE return `(Untitled)` from `GetModelFilename` for an unsaved model, and ETABS reports its working copy
  `model.$et` while a model is open; the server maps both (to "unsaved" and `model.edb`).
- `PointObj.SetLoadForce` returns 0 in ETABS and SAFE for a name that does not exist (SAP2000 returns 1).
- An empty display selection is reported as return code 1 with count 0 (SAFE); the server treats it as "nothing
  selected". SAFE has an internal load case `~LLRF` that can be selected but is never reported back.
- `GroupName="All"` fails for SAFE's `Base Reactions`; `""` works (the server tries both).
- Changing the display units or the table display selection, even when the server puts them back, marks the model
  as changed, so the program asks to save on exit although the model itself is the same. Reads in the program's own
  display units with the cases already selected change nothing.
- `AreaObj.AddByCoord` in SAFE 23.3 accepts areas only at the base level of a grid-only model (code 1 at the
  story level). `File.NewGridOnly` / `NewSteelDeck` exist in ETABS and SAFE only, `New2DFrame` / `New3DFrame` in
  SAP2000 only; area properties are `SetShell_1` in SAP2000 and `SetSlab` / `SetWall` in ETABS and SAFE.
- `ApplicationExit` in SAFE can return "remote procedure call failed" because SAFE exits before answering;
  `disconnect` treats that as closed when the program no longer responds.
- A concrete wall added through the API to ETABS' NewSteelDeck template made it unstable (hundreds of metres at the
  wall mesh nodes and deck-overhang corners, on an edge or interior grid line); walls are a Phase 3 item.
- A model that is not stable (e.g. an unsupported beam) can leave result tables unreadable after the first read in
  SAFE: check the analysis log before trusting results.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not attach ... Operation unavailable` | The program is not running, or runs at another privilege level than Claude. Start both normally (not "Run as administrator"). |
| `CreateObject('ETABSv1.Helper') ... Invalid class string` | The API is not registered: run `RegisterETABS.exe` / `RegisterSAP2000.exe` / `RegisterSAFE.exe` as administrator from the program folder. `setup_result.txt` lists which type libraries were found. |
| The wrong version answers | The most recently registered version answers COM; re-run the Register tool of the one you want, or `connect` with `launch` and `program_path`. |
| Errors after upgrading a CSI program | Delete everything except `__init__.py` in `server\.venv\Lib\site-packages\comtypes\gen` and run `setup_server.bat` again. |
| A call hangs | A dialog is open in the program; close it. |
| Claude does not show the tool | Check `%APPDATA%\Claude\logs\mcp-server-etabs.log` (or `-sap2000`, `-safe`); the entry must point at the installed exe, or at `server\.venv\Scripts\python.exe` with `server\server.py --program <PROGRAM>`. |

## Layout

```
server/        server.py (MCP, CLI --program / --configure-clients), edition.py (which program this server is),
               actions.py (each action: parameters + implementation), catalog.py (wording per program, help),
               programs/ (sap2000.py, etabs.py, safe.py: program-only API code, capabilities, usage notes), csi_bridge.py (COM thread, connection, units, lock, tables),
               table_tools.py (filters, summaries; no COM), safety.py (output folder, export, change log, snapshots),
               model_read.py (Phase 1 model reading), definitions.py + objects.py + modelling.py + db_edit.py (Phase 3), results.py (Phase 2 results, analysis log), report.py (export_report PDF), diagrams.py (report
               drawings), client_config.py, requirements.txt
tests/         offline tests + fake CSI model, reference_models.py, live_read / live_edit / live_phase0 / live_phase1 / live_phase2 / live_phase3 / live_phase3b / live_phase3c / live_phase3d /
               protocol_check / diag, run_*.bat
tools/         write_client_configs.py, gen_typelibs.py, gen_docs.py (docs/ACTIONS*.md), package_editions.py,
               gen_api_matrix.py
docs/          ACTIONS.md + ACTIONS-ETABS / -SAP2000 / -SAFE.md, API_MATRIX.md, api/ (per-library JSON, methods.csv,
               runtime_probe.json)
package/       install.bat, uninstall.bat, INSTALL.txt (build_package.bat makes dist\EtabsMcp.zip, Sap2000Mcp.zip,
               SafeMcp.zip from one server build)
```

`publish_to_github.bat` pushes to `github.com/medmonie/csi-mcp` (create the empty repository first; commit message in
`tools\commit_message.txt`).

## Licence

Copyright (c) 2026 Mohamed Ahmed Hamed.

This project is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md):

- You may read, modify and use it for non-commercial purposes (personal projects, study, research, hobby work, and
  use by charities, schools and public bodies).
- Commercial use, including use on paid projects and selling, needs written permission from the copyright holder.
  Contact the author via GitHub to ask for a commercial licence.

Contributions are welcome through pull requests. By submitting one, you agree that it may be distributed under this
licence.

ETABS, SAP2000 and SAFE are products of Computers and Structures, Inc.; this project is not affiliated with CSI. CSI's
verification models and PDFs are not part of this repository (the verification scripts expect a local copy).
