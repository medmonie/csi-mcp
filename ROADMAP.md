# Roadmap

The full plan (API coverage map, phases, reporting track, decisions) is in the CSI MCP project notes
(`csi-mcp-roadmap.md`). Status:

| Phase | Content | Status |
|---|---|---|
| 0 | Foundation: API capability matrix, reference models, dry run / snapshot / change log, large-output export | **Done in 0.3.0** |
| R1 | export_report PDF: cover, model, definitions, tables, headline results; A4 portrait default, size / orientation per request | **Done in 0.3.0** |
| 1 | Full model read: grids, stories, groups, objects with geometry and assignments, definition details, cases, combinations, mass source, selection, model snapshot to JSON | **Done in 0.4.0** |
| R2 | Report diagrams: 3-D overview, plans per story / level, elevations per grid line, load views per pattern | **Done in 0.4.0** |
| 2 | Results depth: typed results, envelopes, exports, analysis-log diagnostics | **Done in 0.5.0** |
| R3 | Result diagrams: deformed shape, reactions, frame force diagrams, shell contours, ETABS drift / shear charts | **Done in 0.5.0** |
| V1 | Results verified on 38 of CSI's verification examples (SAP2000, ETABS, SAFE): 238 / 238 values match CSI's published results | **Done in 0.5.1** |
| 3 | Definitions and core modelling: materials, sections, patterns / cases / combos, points, areas, walls (first item: the API-built wall that made the ETABS steel-deck template unstable, see tests/diag_etabs_ref.py), loads, groups | **In progress**: 0.6.0 area mesh / divide, joint patterns, area loads incl. surface pressure by joint pattern; 0.7.0 one server per program (etabs / sap2000 / safe packages from one code base); 0.8.0 definitions: materials, frame / area sections, springs, load patterns, linear static / modal cases, combinations, mass source. 0.9.0 objects: create points / frames / areas, point / frame / area assignments, temperature loads, groups, selection, delete. 0.10.0 RS / TH functions and cases (ETABS through database tables). **Phase 3 complete.** |
| V2 | CSI examples rebuilt through the edit actions only (SAP2000 1-001, ETABS Ex 01, SAFE S03a): 30 / 30 values match CSI's published results | **Done in 0.11.0** |
| 4 | Bulk table editing and geometry operations | **Done in 0.12.0**: edit_database_table (diff, apply, import log, read-back check, stale-table guard), move / replicate / divide / merge / extrude / connectivity, new_model, build_model_from_spec (JSON / XLSX) |
| 5 | Program-specific: ETABS towers / piers / auto loads, SAP2000 links / cables / staged, SAFE strips / PT / punching | **Done (0.19.0)**: 0.13.0 stories, diaphragms, piers / spandrels, ASCE 7-16 auto seismic (ETABS / SAP2000), linear links; 0.14.0 user-coefficient seismic, gap / hook / multi-linear links, nonlinear static cases, punching results; 0.15.x result diagrams and contours drawn as each program draws them; 0.16.0 ASCE 7-16 auto wind and user wind loads (SAP2000 / ETABS), SAP2000 diaphragms; 0.17.0 automatic wind / seismic loads by every code the programs offer (Eurocode 1 / 8 checked); 0.18.0 SAFE / ETABS strips, PT tendons, ETABS towers; 0.19.0 SAP2000 cables, solids, tendons, staged construction, hinges + pushover, moving loads (all against hand calculations). V3 on CSI's examples moves to Phase 7 with V5 |
| 6 | Program design with the code the designer chooses (decision D1) (+ R5 design section) | **Done in 0.13.0**: get / set design settings (code, preferences and overwrites through the program's own tables, combinations, groups), run_design, design results, report section with D/C tables, charts and diagrams |
| V4 | CSI's ETABS design examples through the design actions: ACI 318-19 Ex001 / Ex002, AISC 360-16 Ex001 / Ex002 match CSI's published values | **Done in 0.13.0** |
| 7 | Hardening: raw-API escape hatch, large-model speed (e.g. loads by group 'All' in one call: comtypes refused the keyword form in 0.4.0, so loads are read per object), version matrix; V5: CSI's verification examples (CSI Solved Examples folder) as a full regression suite (V2 with Phase 3: rebuild examples with the modelling actions; V3 with Phase 5; V4 design examples with Phase 6) | |

Decisions (26 Nov 2026): D1 design with CSI's engines is allowed using the code the designer names; Claude never
picks codes, combinations or overwrites. D2 report layout per request, A4 portrait by default.

## Later (after Phase 5, decided 27 Nov 2026)
- Model translation SAP2000 <-> ETABS: CSI's API has no cross-program export (ETABS / SAFE ExportFile / ImportFile
  handle .e2k text and database tables only; SAP2000 has neither), so the server would read the model in one program,
  rebuild it in the other through its actions and compare the analysis results. Plus .e2k export / import, and
  ETABS -> SAFE (measured in 0.18.0: SAFE ImportFile returns 0 but imports nothing, or opens its import dialogs).
- Old SAP2000 models through CSI's SAPTRANS translators (SAP2000 27\SAPTRANS), if they run without their window.
- ETABS / SAFE nonlinear cracked slab analysis (load case with the slab's rebar, long-term effects) through the
  program's tables - no API call exists. Until then: area stiffness modifiers (assign_area_properties) given by the
  designer. With it: the ETABS shell assignments Floor Cracking and Wall Rebar Ratio for Creep (no API calls either;
  their assignment tables, which edit_database_table may already reach - to confirm).
