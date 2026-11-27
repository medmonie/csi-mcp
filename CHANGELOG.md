# Changelog

## 0.16.0 - 2026-11-27

Phase 5: wind loads. 2 new actions, 1 extended.
- `set_auto_wind` (SAP2000, ETABS): a wind pattern becomes an ASCE 7-16 automatic wind load with the designer's
  parameters - exposure from diaphragms (direction angle, Cpw / Cpl) or shells, ASCE case 1-4 (ETABS: or all), V
  in mph, m/s or km/h (converted to mph exactly), exposure category, Kzt, G, Kd, ETABS Ke and parapet, load range
  (SAP2000 elevations, ETABS stories). SAP2000 through LoadPatterns.AutoWind.SetASCE716; ETABS has no wind API
  (its AutoWind object has no methods - measured) so through 'Load Pattern Definitions - Auto Wind - ASCE 7-16'.
  Read back, with the exposure widths ETABS works out per diaphragm.
- `set_wind_user_loads` (SAP2000, ETABS): the designer's Fx / Fy / Mz per diaphragm at a point - for codes CSI does
  not list (e.g. ECP 201). SAP2000 SetUserLoad; ETABS 'Load Pattern Definitions - Auto Wind - User Loads'.
- `define_diaphragm` works in SAP2000: a rigid diaphragm constraint about Z on the joints (areas give their corner
  joints) - SAP2000 puts diaphragm wind on these.
- Measured (tests\run_live_wind.bat, V = 40 m/s, exposure C, Cpw 0.8, Cpl 0.5, G 0.85, Kd 0.85, 12 m wide, levels
  3.5 / 7 m): the two programs distribute the same ASCE 7-16 load differently. SAP2000 takes the pressure at each
  diaphragm's elevation over its tributary height, the lowest diaphragm down to the base: 51.073 / 17.973 kN (hand:
  same to 1e-7). ETABS integrates the pressure over the height and gives each storey's load to the levels below and
  above as simple spans, the base half to the supports: 34.214 / 17.554 kN (hand: same to 2e-5). Base shear 69.05
  vs 51.77 kN for the same building - stated in the action's answer. User loads: base shear -50.000 kN in both.

## 0.15.2 - 2026-11-27

- `set_area_mesh` works in ETABS (it has no API call; through the program's own tables): walls get auto
  rectangular mesh, and the model's wall / floor mesh size is set (mode max_size), or walls go back to Default (mode
  none). Measured: an ETABS wall on 'Default' is one analysis element per panel - its contour was one bilinear patch
  per storey; 'Auto Rectangular' at 1 m gives 24 elements per 6 x 3.5 m panel. The size tables follow the units
  given (checked in kN-m, kip-in, N-mm). Read back in the answer.
- Contour scale as the programs make it, measured again: 15 bands of a round step starting just below the minimum
  (SAP2000 M11 -35 ... 17.5 by 3.5; ETABS M11 -32 ... 28 by 4, F22 -220 ... 80 by 20), the programs' own 15 colours
  (sampled from their colour bars; the same palette in SAP2000, ETABS and SAFE), inner boundaries labelled. The
  0.15.0 'symmetric about zero' ETABS / SAFE scale was wrong.
- Finer interpolation inside each element (quads on a 4 x 4 bilinear grid up to 400 elements per view).
- Report parameter `contour_averaging`: program_default (SAP2000 averaged, ETABS / SAFE not) | joints | none.
- live_diagrams: meshes the ETABS slabs and wall at 1 m too; writes to a new folder when the last output is still
  held by a program (an ETABS model left open locks its result files: 'FILE OPERATION ERROR 232' in the .LOG).

## 0.15.1 - 2026-11-27

- Shell contours are smooth and continuous like the programs' displays: each element is split into linear triangles
  (quads with bilinear values on a 3 x 3 / 2 x 2 grid) and each triangle is clipped exactly at the band boundaries,
  so the band edges run straight across the elements instead of in steps. Mesh lines are left out above 800
  elements per view.
- tests\run_live_diagrams_b.bat (live_diagrams.py --out=<folder>): writes to another folder while the models of
  the last run are open in the programs.

## 0.15.0 - 2026-11-27

Result diagrams drawn as SAP2000, ETABS and SAFE draw them (report `result_diagrams`). No new actions; 1 new report
parameter. Checked side by side with the programs' displays on multi-bay, multi-storey models (12 slabs, a wall,
SAP2000 meshed at 1 m) and a SAFE example copy (tests\run_live_diagrams.bat).
- Frame diagrams (M, V, P, T): split at the zero crossings and drawn on the tension side for moments; the moment
  is curved between the output stations from the shear (exact for distributed loads: the 2-span test beam gives
  9wL2/128 and wL2/8). SAP2000 reports: filled, positive blue / negative red, as SAP2000 shows them. ETABS / SAFE
  reports: one colour, not filled, hatched across, as those programs show them by default. Values at the member
  ends and at each peak.
- Shell contours, slabs per level and walls per plane (new `wall_component`, default F22): values interpolated over
  each element and coloured in bands with the program's colours and labels. SAP2000: averaged at all joints (its
  default), round band values from max to min (e.g. 14 / 10.5 / ... / -31.5), blue = most positive ... purple = most
  negative, vertical bar. ETABS / SAFE: not averaged (their default), a scale symmetric about zero (e.g. -32 ... 32
  by 4; -350 ... 350 by 50), purple ... yellow below zero and green ... blue above, horizontal bar. The ranges in
  the reports matched the programs' own min / max (ETABS M11 -31.57 / 16.58; SAP2000 max 16.42 vs 16.44). SAP2000
  reports also give the unaveraged range.
- Fixed: whole members were coloured by the sign of their peak, diagrams crossed themselves at zero, stations were
  joined by straight lines only, and the contour palette was reversed with no averaging.

## 0.14.0 - 2026-11-27

Phase 5 second batch and SAFE design verification. 1 new action, 3 extended.
- `set_seismic_coefficient` (ETABS / SAP2000): a user-coefficient seismic load, V = C x W (W from the mass source)
  distributed with the height exponent K - for codes CSI does not list (e.g. ECP 201), with C worked out by the
  designer; direction, eccentricity, load range (ETABS stories, SAP2000 elevations), read back. SAP2000 through
  AutoSeismic.SetUserCoefficient; ETABS has no API setter, so the program's input table 'Load Pattern Definitions -
  Auto Seismic - User Coefficient' is written and read back. Live hand check (both programs): all mass from a 100 kN
  point load (mass source: loads only), C = 0.0825 -> base shear 8.25 kN exactly.
- `define_link_property` adds nonlinear types: gap (compression after an opening), hook (tension after an opening)
  and multi-linear elastic (force-deformation points through 0, 0), with the effective linear stiffness per
  direction; read back by type (GetTypeOAPI codes measured: linear 1, gap 3, hook 4, multi-linear elastic 8).
- `define_load_case` adds nonlinear_static (load patterns with factors, geometric nonlinearity none / P-delta /
  P-delta + large displacements (SAP2000), initial case); the readers report nonlinear static loads, geometric
  nonlinearity and initial case. Live hand check (SAP2000, nonlinear static): gap 0.01 m / 1000 kN/m with a 1 kN/m
  spring under 20 kN -> U = -(20 + 10) / 1001 = -0.029970 m; hook the same in tension (+0.029970); multi-linear
  elastic 40 kN on (0.02, 30) - (0.1, 50) -> 0.060 m - all exact.
- Design: punching shear checks (ETABS / SAFE, from 'Concrete Slab Design - Punching Shear Data') in
  get_design_results for concrete_slab and in the report (table and a punching D/C bar chart).
- **V4 SAFE** (tests\run_verify_v4_safe.bat, CSI's SAFE examples, copies): ACI 318-19 RC-PN Ex001 punching at grid
  B-2: D/C 1.2230 (CSI 1.22), stress 0.193 ksi (0.193), capacity 0.158 ksi (0.158); ACI 318-19 RC-SL Ex001
  slab strip: As+ 0.2127 in2 (CSI 0.213), strip moment 55.63 k-in (CSI's table 55.22: +0.7 %, accepted at 1 % - the
  FE strip integration in SAFE 23.3; the steel area matches).

## 0.13.0 - 2026-11-27

Phase 6 (design with the program's own engines) and Phase 5 first batch (program-specific modelling). 10 new actions.

**Design (group Design).** Configure -> run the program's own design -> results as tables, charts and printed
diagrams. Claude never chooses a code, combination, preference, overwrite or limit: the code is the one set in the
model or named by the designer, and every answer states the code, the combinations and the units.
- `get_design_settings`: code, strength / deflection combinations, design groups, whether results exist, and the
  program's own preferences table for that code (e.g. 'Steel Frame Design Preferences - AISC 360-16': frame type,
  SDC, R, Omega0, Cd, Sds, phi factors, D/C limit ...) and its overwrites tables. Kinds: steel_frame, concrete_frame
  (all three), concrete_slab (ETABS / SAFE), shear_wall (ETABS).
- `set_design_settings`: the code the designer names (SetCode, read back; the old code is restored if the program
  reports another), preferences through the program's preferences table (any field, read back), strength /
  deflection combinations and design groups (checked against the model, read back), member overwrites through the
  overwrites table (match / set rows), dry_run, snapshot. Works for every code in the program's list - no per-code
  mapping.
- `run_design`: needs a code and, for frames, selected strength combinations - otherwise it stops and asks for them;
  program_default_combinations: true only when the designer accepts the program's own default design combinations
  (CSI's examples are delivered that way). Needs analysis results (run_analysis_first: true runs it). Answers with
  the combinations actually used (the program adds DStlS / DStlD / DConS ... combinations), failing and unchecked
  members, design sections that differ from the analysis sections.
- `get_design_results`: steel (governing ratio and type, station, combination, errors / warnings, design section),
  concrete beams (top / bottom / shear / torsion steel per station) and columns (PMM area or ratio, shear), slab
  strips, shear-wall piers; governing top 20, members over 1.0; CSV / XLSX export; the program's own design result
  tables listed.
- Report section `design` (export_report, R5): code and combinations, the preferences table, summary sentence,
  steel / concrete / slab / pier tables, members over 1.0 or with errors, D/C ratio 3-D view, bar chart of the
  highest ratios, D/C band histogram and elevations with the ratio on each member (blue < 0.5, green, yellow,
  orange 0.9-1.0, red > 1.0). Kinds without a design-results flag (slab strips, piers) are only read when asked:
  reading them before a design made ETABS 23.2 hang the report (measured).
- **V4 (CSI's ETABS design examples, copies):** ACI 318-19 Ex001 As = 2.3706 in2 (CSI 2.37), Av/s at the critical
  section 0.04102 in2/in (CSI 0.041); Ex002 column D/C 0.99998 (CSI 1.000); AISC 360-16 Ex001 phi Mn from the
  ratios 378.750 / 307.109 / 94.361 k-ft (CSI 378.750 / 307.108 / 94.361); Ex002 phi Pn 497.935 kips (CSI 497.9).
- **Live hand check:** I-section d 18 / bf 7.5 / tf 0.57 / tw 0.355 in, Fy 50, simply supported 240 in, wu 0.145 k/in:
  with the LTB overwrite 0.1 (Lb 24 in < Lp) ratio = Mu / phi Mp = 1044 / 4488 = 0.2326 - ETABS and SAP2000 both 0.232602; with the
  full length LTB governs (ETABS 0.4289, SAP2000 0.4317).

**Program-specific modelling (Phase 5, first batch).**
- `define_stories` (ETABS): base elevation and stories (height, master / similar-to, splice), read back with
  elevations (measured 144 / 264 in).
- `define_diaphragm` (ETABS / SAFE): rigid or semi-rigid, assigned to points and / or floor areas, read back.
- `assign_pier_spandrel` (ETABS): pier / spandrel label (made if missing) on wall areas and frames, read back.
- `set_auto_seismic` (ETABS / SAP2000): ASCE 7-16 automatic seismic load on a quake pattern with the designer's
  parameters only (directions, eccentricity, period option, R, Omega, Cd, Ie, Ss, S1, TL, site class, Fa / Fv for F,
  load elevations; ETABS: top / bottom story), read back with the program's site coefficients Fa / Fv. Measured:
  ETABS 23.2 returned 1 for every SetASCE716 call and 0 for SetASCE716_1 (used first; SAP2000's plain call stored
  Fa = Fv = 0, the _1 call fills them from the site class); the Get call's direction array is not laid out like the
  Set call's (7 flags in ETABS, 3 in SAP2000), so directions are read from the program's input table; and the ETABS
  API leaves the load's story range at Base - Base (base shear 0), so the range is set through 'Load Pattern
  Definitions - Auto Seismic - ASCE 7-16' (designer's stories, else the highest story and Base, stated). Live hand
  check (ETABS, site B: Fa 0.9 / Fv 0.8 read back, R 8, T 0.5 s): Cs = min(0.9 / 8, 0.32 / 4) = 0.08; base shear /
  W ('Mass Summary by Story' above the base x g) = 0.08001.
- `define_link_property` / `create_links` (ETABS / SAP2000): linear link properties (spring stiffness and damping per
  direction, rigid directions) and two-joint or one-joint links, read back. Live hand check (SAP2000): U = F / k =
  -0.010 m (k 1000 kN/m) and -0.020 m (k 500 kN/m, one-joint link), exact.
- SAP2000 table names ('Preferences - Steel Design - ...') are found as well as ETABS / SAFE ones; slab design
  reports the concrete design code (no code call of its own).

## 0.12.0 - 2026-11-26

Phase 4: bulk editing, geometry operations and models built from a spec. 9 new actions.

- `edit_database_table` (group Bulk editing): read any table with its editing field keys (filters), or edit an
  importable one - add rows, upsert rows on key fields, update / delete rows matched by field values (with an
  optional expected row count), rows from CSV / XLSX. dry_run returns the diff (rows added, changed field old -> new,
  deleted). Applying reads the table, keeps every row it was not asked to change, applies with the import log, cancels
  the pending edit on errors, and reads the table back to verify every value it was given (numbers compared as
  numbers). Results tables are refused; import-type-2 tables need allow_unlock on an analysed model; an edit that
  would empty a table needs allow_empty. Values follow `units` (Joint Coordinates: m -> mm = x 1000, measured).
  **Stale-table guard:** for load patterns, materials, sections, points, frames, areas, groups, cases and
  combinations, every name the program's API lists must be in the table before it is written - SAP2000 27.1 kept
  'Load Pattern Definitions' at DEAD only after DL / LL / WX were added through the API (also after saving and
  reopening, measured), and writing that table back would have deleted them. The edit is refused and the specific
  action named instead.
- Geometry (group Geometry): `move_objects` (all three; the objects' points are selected too, and a move that moves
  nothing is an error), `replicate_objects` (SAP2000 linear / radial / mirror with every assignment and load;
  ETABS / SAFE linear copies made with the create calls, carrying section, restraints, frame releases and local axis,
  restraints read back; loads and other assignments listed as not copied), `divide_frames` (SAP2000: ratio, distance,
  intersections), `merge_points` (SAP2000; pair by pair, judged by which points are gone - Merge with the pair
  selected merged it but returned 1, measured), `extrude_objects` (SAP2000: points to frames, frames to areas),
  `change_connectivity` (frames in all three, areas in SAP2000). ETABS / SAFE refusals name what to do instead.
- `new_model` (group Model): blank (all) or grid-only (ETABS / SAFE: stories, grid lines) in `units`, saved at once;
  an open model is closed only with discard_open_model: true.
- `build_model_from_spec` (group Bulk editing): a JSON object, .json or .xlsx (a sheet per section, header row =
  fields, lists as JSON, a.b for nested fields, a settings sheet) for materials, functions, sections, springs,
  patterns, points, frames, areas, assignments, groups, loads, cases, combinations, mass source, analysis options and
  table edits, optionally starting with new_model and ending with save_as. Every step is validated with its action's
  parameters, references (materials, sections, points, load patterns) and clashes with existing definitions
  (case-insensitive, needs replace: true) are checked before anything changes; then the steps run in dependency order
  through the same actions and stop at the first refusal with the step, what was done and the snapshot. Object names
  the program does not keep (an existing point at the place) stop the run. write_template writes an example workbook
  (portal frame) and its JSON.
- create_points reports points the program merged on creation as an error with the surviving names: SAP2000
  replaces a point drawn within its merge tolerance of another (measured: Q2 0.5 mm from Q1 removed Q1; NEAR 0.5 mm
  from a frame end removed that end point and the frames now end at NEAR). Definitions refuse a name that differs
  from an existing one only in case (ETABS / SAFE LoadPatterns.Add returned 1 for DEAD next to Dead).
- Tests: offline 63 -> 74; live_phase4 (new_model, spec build of the portal / slab, refused spec, table edit with
  LL = 60 + 0.5 DEAD (240 + 0.5 DEAD in SAFE), units, divide / replicate (LL x 2) / mirror / radial / merge / extrude
  in SAP2000, move / copy in ETABS and SAFE); diag_phase4.

## 0.11.0 - 2026-11-26

V2 verification: three of CSI's verification examples rebuilt from a blank model through the edit actions alone and
checked against CSI's published values - all 30 values match (SAP2000 Example 1-001 7 / 7, ETABS Example 01 19 / 19,
SAFE S03a 4 / 4). Every gap the rebuild found is now an action or a reader fix:

- `set_analysis_options` (new, group Analysis): active degrees of freedom (presets space, plane_xz, plane_yz,
  plane_xy, grid_xy, truss_xz, truss_3d, or a list), which load cases run (`{"*": false}` clears all), ETABS / SAFE
  automatic floor mesh (general / rectangular, maximum size, localized, merge joints; database table). Read back, and
  part of `export_model_snapshot` (`analysis_options`). Example 1-001 is a 2-D frame (UX UZ RY): with all six DOF
  the rebuilt model was unstable out of plane and 3-5 % off; S03a uses a rectangular 480 in mesh ("mesh a"): with
  the 48 in default the deflections were 0.3-2.3 % lower (nearer the exact plate solution, not CSI's mesh-a values).
- `assign_point_or_frame_load`: concentrated forces / moments on frames (`frame_load_shape: point`, values
  [P, relative distance]).
- `assign_frame_properties`: output stations (max segment or min sections, at element ends / point loads) and, ETABS
  / SAFE, a line spring property along the frame. `create_frames` takes section `none` in ETABS / SAFE (null line,
  e.g. a support line).
- `define_spring_property`: line springs (U1 U2 U3 R1 per length) and a compression- / tension-only option for line
  and area springs. SAFE line springs go through the database table: SAFE 23.3's GetLineSpringProp does not return
  the stiffness the model holds (measured: table VertStiff 1.751e12 kN/m/m, getter U2 = 0.001).
- Readers: material strengths (f'c, fy, fu), section-level property modifiers (1-001: A x1000, As2 = As3 = 0), the
  full properties of General sections (GetSectProps), frame output stations, ETABS / SAFE frame supports and line
  springs, spring properties in the model snapshot. Small magnitudes keep 7 significant digits (unit weight 8.68e-05
  was rounded to 8.7e-05). ETABS end offsets are reported when automatic offsets are OFF (new ETABS frames have them
  on; with them on, Ex 01's output stations moved).
- Tests: offline 58 -> 63; `tests\run_verify_v2.bat` (all three) and `run_verify_v2_safe.bat` RESULT=OK; V1 re-run
  238 / 238. The rebuild script also lists every raw API difference between the original and the rebuilt model.

## 0.10.0 - 2026-11-26

Phase 3, last part: response-spectrum and time-history functions and cases. Phase 3 is complete.

- `define_function` (SAP2000 / ETABS): user response-spectrum function (period, value, damping ratio) or time-history
  function (time, value) from the user's points; read back with Func.GetValues.
- `define_load_case` gains `response_spectrum` (accelerations U1 / U2 / U3 ... with function and scale, modal case,
  modal combination CQC / SRSS / ABS, directional combination SRSS / ABS; SAP2000 damping) and
  `linear_modal_history` (load patterns or accelerations with functions, steps and step size, modal case; SAP2000
  damping). Read back with the loads, modal case, combinations, damping, steps.
- ETABS has no API setter for user functions or for these case options (type libraries: SAP2000 only), so they go
  through the program's database tables (`server/db_edit.py`): the table is read first and every other row kept
  (SetTableForEditingArray replaces the whole table), field keys from GetTableForEditingArray, ApplyEditedTables
  with the import log, errors stop the action with the log lines. ETABS cases use the model's modal damping (0.05).
- SAFE: no functions, RS or TH cases (refused with the reason; SAFE takes RS cases with floors exported from ETABS).
- Build: definitions, objects and db_edit added to the hidden imports.
- Tests: offline 56 -> 58; `tests/run_live_phase3d.bat` RESULT=OK: single-mass cantilever (T = 0.126897 s = hand),
  flat spectrum 0.5 g -> base shear 5.0000 kN = m Sa and tip 2.0000 mm = Sa / w^2 in SAP2000 and ETABS; step load in
  a linear modal history, step Td / 40 -> peak 8.0001 mm = 2F/k (SAP2000, damping 0) and 7.4179 mm = (1 + e^(-pi z
  / sqrt(1-z^2))) F/k (ETABS, 5 %).

## 0.9.0 - 2026-11-26

Phase 3, batch 3: objects. Ten actions (new group "Objects"); edits take dry_run, snapshot, allow_unlock and units,
targets by names or a group, names checked first, answers read back.

- `create_points` (by coordinates, optional restraint), `create_frames` (between point names or coordinates, one
  section; ETABS / SAFE labels and stories), `create_areas` (by points or coordinates, one section, or openings;
  answers with the corner points).
- `assign_point_properties`: restraint (presets or six flags), spring stiffnesses, additional mass; ETABS / SAFE
  spring property and diaphragm (a missing diaphragm is created rigid).
- `assign_frame_properties`: section, end releases, rigid end offsets, insertion point, modifiers, local axis angle.
- `assign_area_properties`: section, modifiers, local axis, opening; ETABS / SAFE diaphragm and area spring property;
  SAP2000 area springs from a subgrade modulus (bottom face, normal).
- `assign_temperature_load`: uniform or gradient on frames and areas. SAFE: frames refused (SAFE ignores frame
  temperature loads, measured), areas with a note (bending-only analysis).
- `set_group` (define, add, remove, delete), `select_objects` (names, group, all, clear; the model is not changed),
  `delete_objects` (frames / areas; points only when nothing uses them, the rest listed; end points the program
  removes with a frame are reported).
- ETABS: create actions warn when they leave a free joint at the Base level - ETABS holds such joints fixed in the
  analysis (measured: a free Base-level joint gave zero displacement and moment where the same beam at Story1 gives
  PL/4). Noted in the ETABS usage text.
- Fix: SAP2000 frame / area modifiers were never read (FrameObj.GetModifiers returns code 1 through comtypes unless
  the ref array is passed in; get_object_assignments and the read-backs now pass it).
- The ETABS "API-built wall unstable" item from 0.6: a wall made with create_areas on a grid-only model is stable,
  base shear = the load; the instability was specific to the steel-deck template.
- Tests: offline 51 -> 56; `tests/run_live_phase3c.bat` RESULT=OK (52 checks): releases PL/4 = 10.000 kNm and
  PL^3/48EI = 0.25000 mm, fixed bar under +30 deg -1188.00 kN = -E A alpha dT (SAP2000, ETABS), gradients
  E I alpha g = 5.2800 kNm (SAP2000, ETABS), point spring 5.0000 mm, slab on subgrade q / ks = 0.5000 mm, in all
  three programs where they apply.

## 0.8.0 - 2026-11-26

Phase 3, batch 2: definitions. Eight edit actions (new group "Definitions"), each with dry_run, snapshot,
allow_unlock, name checks, `replace` to change an existing definition, and an answer read back from the program.

- `define_material`: concrete / steel / rebar / tendon / aluminum / cold-formed / other from the user's E, Poisson, unit
  weight and f'c or fy / fu (only the values given are set; the program's defaults for the type are kept for the
  rest, e.g. stress-strain parameters, and listed), or from the program's library (region, standard, grade).
- `define_frame_section`: rectangle, circle, I, pipe, tube, angle, channel, tee, general (12 properties from the user)
  or from a property file; optional modifiers; read back with the program's section properties (GetSectProps).
- `define_area_section`: SAP2000 shell / plate (thin, thick) / membrane with bending thickness; ETABS / SAFE slab
  (slab, drop, stiff, mat, footing) or wall as thin / thick shell or membrane; optional modifiers. Kinds and
  parameters are per edition.
- `define_spring_property` (ETABS / SAFE): point (6 stiffnesses) or area (subgrade-type) spring properties;
  `get_definition_details` kind springs now reports the stiffnesses.
- `define_load_pattern` (type, self-weight multiplier, own linear static case), `define_load_case` (linear static
  with factors, modal eigen, modal Ritz with starting vectors), `define_combination` (linear add, envelope, absolute
  add, SRSS, range; cases and combinations found by name), `set_mass_source` (elements / added masses / load patterns;
  SAP2000 named mass sources).
- Tests: offline 46 -> 51 (stateful fake definitions); `tests/run_live_phase3b.bat`: in all three programs every
  definition read back, and a 4 m cantilever of the new 0.4 x 0.4 section (E = 25 GPa) matches hand values: tip
  deflection PL^3/3EI = 4.000 mm (4.00003, shear deformation suppressed by modifiers), case x 2 = 8.000, combination
  1.4 SDL = 5.600, envelope 5.600; SAP2000 / ETABS first period with mass only from a 10 kN load = 2 pi sqrt(m/k) =
  0.12690 s (0.126897). RESULT=OK.

## 0.7.1 - 2026-11-26

- Installers: Claude Desktop 6.x reads `%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json`, not the
  two files install.bat wrote (`%APPDATA%\Claude\` and the Store package's `LocalCache`), so 0.7.0 added `etabs`,
  `sap2000`, `safe` and removed `csi` in files Claude no longer reads. That file is now configured too (its other
  settings kept, backed up first). `tools/diag_claude_config.py` (`tests\run_diag_claude_config.bat`) lists every
  Claude config file with its server names, read-only.
- Tests: offline 45 -> 46.

## 0.7.0 - 2026-11-26

One server per program: ETABS, SAP2000 and SAFE each get their own package, MCP server and tool. One code base.

- Editions (`server/edition.py`): `etabs-mcp-server.exe`, `sap2000-mcp-server.exe`, `safe-mcp-server.exe` are one build
  under three names; the exe takes its program from its name (`server.py --program X` or `CSI_MCP_PROGRAM` in
  development). Server and tool name `etabs` / `sap2000` / `safe` (split: `etabs_read` / `etabs_edit` ...).
- Each edition lists only its program's actions, parameters and option values, worded for that program; the tool
  text opens with the program's usage notes (`programs/*.py` USAGE). `software` is hidden and filled in; another
  program is refused with the name of its tool. Program-only options (`surface_pressure`, `joint_patterns`,
  `springs`, `story_drifts`, `pier_forces`, `story_charts` ...) and parameters (`face`, `joint_pattern`, `story`,
  `drift_limit`) are declared on the action, hidden from the other editions and refused there with the program's
  own alternative (e.g. "split the wall into strips ... per strip uniform load" in ETABS).
- Audit of the shared code against the installed type libraries: every remaining program branch matches an API
  difference (bulk readers, grid / story API, diaphragms and spring properties in ETABS / SAFE; auto mesh, divide,
  joint patterns, surface pressure in SAP2000), so the engine stays shared. Changes from it:
  `create_frame_element` returns `label` / `story` in SAFE as well as ETABS (`FrameObj.GetLabelFromName` exists in
  both; the keys were `etabs_label` / `etabs_story`); pier / spandrel forces offered in ETABS only (SAFE's API has
  the call but SAFE models have no piers).
- Packages: `build_package.bat` builds the server once and `tools/package_editions.py` makes `EtabsMcp.zip`,
  `Sap2000Mcp.zip`, `SafeMcp.zip` (exe, install / uninstall, INSTALL.txt and ACTIONS.md of that program). install.bat
  installs to `%LOCALAPPDATA%\CsiMcp\<program>\`, adds the program's entry to Claude Desktop / Codex and removes the
  old combined `csi` entry and exe. Windows lock per program, so the three servers do not block each other.
- `server.py` without `--program` still serves all three in one tool `csi` (development and offline tests; not
  packaged).
- Docs: `docs/ACTIONS-ETABS.md`, `-SAP2000.md`, `-SAFE.md` (generated), `docs/ACTIONS.md` index.
- Tests: offline 41 -> 45 (edition from exe name, tool names, program-only text and options, software filled in,
  other program refused, client config replaces `csi`); `run_exe_check.bat` checks the three packaged exes.

## 0.6.0 - 2026-11-26

Phase 3, first batch: one package with a program layer, area mesh, joint patterns and area loads.

- Program layer (`server/programs/`): `CSI_MCP_PROGRAMS` (e.g. `ETABS,SAFE`) limits the server to some programs -
  only their actions are listed (shorter tool text), other `software` values are refused, `list_programs` checks
  only them. Each action declares the programs whose API has it; the listing tags program-only actions
  (`get_stories [ETABS, SAFE]`, `set_area_mesh [SAP2000]`). Program-specific code and the capability table (from
  the API matrix) are in `programs/sap2000.py`, `etabs.py`, `safe.py`; a request a program's API cannot do is
  refused with the nearest alternative.
- `set_area_mesh` (SAP2000): auto mesh of areas by divisions, max size or the general divide tool, or none; or
  divide areas into smaller area objects; restraints carried to the new edge joints by default. Divide uses
  `EditArea.Divide` (through comtypes `Divide_1` refuses every argument count on SAP2000 27.1, see
  `tests/diag_divide.py`); since `Divide` has no edge-restraint option, the server gives each new joint on an
  original edge the restraint its two corners share.
- `define_joint_pattern` (SAP2000): hydrostatic (unit weight x depth below a fluid level, 0 above) or linear
  (A*x + B*y + C*z + D, with positive / negative-only restriction); reports values at the highest and lowest joints.
- `assign_area_load`: uniform and uniform-to-frame (one- / two-way) in all programs; surface pressure on a shell
  face scaled by a joint pattern in SAP2000 (hydrostatic / earth pressure on walls). Names checked first (ETABS /
  SAFE answer 0 for unknown names).
- Reading: `get_object_assignments` shows area auto mesh, surface-pressure and uniform-to-frame loads and joint
  pattern values; `get_definition_details` kind `joint_patterns` (joints with a value, min / max and where).
- Tests: offline 37 -> 41; `tests/run_live_phase3.bat`: hydrostatic cantilever wall in SAP2000 checked against
  statics for three meshes (|FY| = 160 kN, |MX| = 213.33 kNm to 1e-7; tip deflection converges 6.042 -> 5.983 ->
  5.968 mm), divide 2 x 2 with the base fixity carried and statics unchanged, uniform area loads in ETABS and SAFE
  against the base reaction. RESULT=OK.

## 0.5.1 - 2026-11-26

V1 verification against CSI's own verification examples (closes Phase 2).

- `tests/run_verify_v1.bat`: 38 of CSI's example models (SAP2000 frames, response spectrum, modal and direct
  time history, buckling, plates and shells; ETABS Examples 01, 02, 04, 05, 06; SAFE plate Examples 1-3 in three
  meshes) are copied, opened, analysed and read through the MCP's own actions (`run_analysis`, `get_results`,
  `get_analysis_log`) and compared with CSI's published program values: 238 / 238 match (within the rounding of
  the published value or 0.05 %), every solver log stable. `tests/run_verify_inventory.bat` lists the models'
  cases, joints and frames (used to write the expected values). The examples stay in "CSI Solved Examples"
  (excluded from Git and from the source zip); the expected values file is local only.
- `get_analysis_log`: also reads the stability check of nonlinear cases ("NUMBER FOUND AT FINAL CONVERGED STATE")
  and keeps the worst count over all cases (`stability_checks` = number of checks found).

## 0.5.0 - 2026-11-26

Phase 2 (results depth) and report stage R3 (result diagrams) of the roadmap. All read only.

- `get_results` (server/results.py): typed results through the program's results API - joint displacements /
  reactions, frame forces at the output stations, frame joint forces, shell forces and stresses, mode shapes, base
  reactions, modal periods / participating mass ratios / participation factors, buckling factors, story and joint
  drifts (ETABS / SAFE), pier / spandrel forces (ETABS), section cuts. Every object of the group 'All' in one call
  (or named objects / a group); chosen cases and combinations; `step_option` envelope | step_by_step | last_step for
  nonlinear, history, multi-step cases and multi-valued combinations; governing max / min / |max| per component with
  where and in which case; `envelope_by_object`; `component` sorts the rows; `export` auto | csv | xlsx | json.
  The output selection and step options the user had in Results.Setup are put back afterwards.
- `get_analysis_log`: the solver's <model>.LOG / .OUT - stability check (negative stiffness eigenvalues), warning
  and info messages, cases solved, modes found, model counts, start / complete time.
- Report R3: section `result_diagrams` in `export_report` for one case / combination (`result_case`, default the
  first combination, else the first linear static case): deformed shape, support reactions with F3, frame force
  diagrams per elevation (default `bending` = each member's in-plane moment, M3 or M2 from its local axes read with
  FrameObj.GetTransformationMatrix, drawn on its local -2 / -3 side), shell contours per level
  (`shell_component`), ETABS story drift and story shear charts (`drift_limit` from the engineer).
- Newest method variants where CSI has them, older ones as fallback: RespCombo.GetCaseList_1 (mode numbers),
  LoadCases.GetTypeOAPI_1 (design type, auto-created), PropFrame.GetISection_1 / GetTube_1 (fillet / corner radius).
- XLSX export (openpyxl, new requirement: run setup_server.bat again).
- Tests: offline 32 -> 37; `tests/run_live_phase2.bat` with hand checks on the reference models.

## 0.4.0 - 2026-11-26

Phase 1 (full model read) and report stage R2 (model diagrams) of the roadmap. All read only.

- New group "Model reading" (`server/model_read.py`): `get_grids` (ETABS / SAFE grid API; SAP2000 'Grid Lines'
  table), `get_stories`, `get_groups` (counts per object type, members on request), `get_objects` (points with
  coordinates and support, frames with section, end points, coordinates, length and vertical / horizontal /
  inclined, areas with property, corner points, area and levels; ETABS / SAFE label and story; `story` filter),
  `get_object_assignments` (points: restraint, springs, diaphragm, mass, groups, joint loads; frames: section,
  releases, end offsets, insertion point, modifiers, local axis, material overwrite, pier / spandrel, groups,
  distributed and point loads; areas: property, modifiers, local axis, opening, diaphragm, springs, pier /
  spandrel, groups, uniform loads; defaults left out unless `non_default_only` is false), `get_definition_details`
  (materials, frame sections, area sections, springs, load patterns), `get_load_case_details`, `get_combo_details`,
  `get_mass_source`, `get_selected`, and in Output `export_model_snapshot` (the whole model, assignments and loads
  included, to one JSON file).
- ETABS / SAFE use the bulk readers (PointObj.GetAllPoints, FrameObj.GetAllFrames, AreaObj.GetAllAreas,
  PropFrame.GetAllFrameProperties_2, *.GetLabelNameList); SAP2000 has none and is read object by object. Lists
  longer than max_rows also go in full to CSV / JSON in the output folder (export_path).
- Report R2: new `diagrams` section in `export_report` (on by default): 3-D overview, plan per story / level (grid
  lines with bubbles, beams with section names, columns as squares, areas, supports), elevation per grid line (or
  per frame plane in SAP2000) with story lines, and one load view per load pattern with the assigned values; drawn
  with reportlab.graphics (`server/diagrams.py`, no new dependency). Parameters `diagrams`, `diagram_patterns`,
  `max_plans`; the result gives the number of `figures`.
- Tests: offline 26 -> 32 (fake model now has geometry, grids, stories, groups, loads); `tests/run_live_phase1.bat`
  reads the three reference models back and compares with the programs' per-object answers.

## 0.3.0 - 2026-11-26

Phase 0 (foundation) and report stage R1 of the roadmap. Decisions recorded: design with the code the designer
chooses is allowed (Phase 6); report layout per request, A4 portrait by default.

- API capability matrix (`tools/run_api_matrix.bat`): every interface, method and signature of the installed type
  libraries (ETABS 23.2: 129 interfaces / 1338 methods, SAP2000 27.1: 171 / 2135, SAFE 23.3: 130 / 1343) in
  `docs/api/` and `docs/API_MATRIX.md`, plus a run-time probe of every SapModel object in each program (all answer).
- Edit safety: `dry_run` on every edit action (checks everything, reports what would happen, changes nothing);
  `snapshot` on create_frame_element, assign_point_or_frame_load and run_analysis, and the `snapshot_model` action
  (copy of the model file as last saved in `_csi_mcp_snapshots` next to the model; the program is not touched);
  change log `Documents\CsiMcp\change_log.jsonl`, one line per edit that ran (ok or failed), dry runs not logged.
- Large-output policy: `query_database_table` `export` = auto (default: full filtered table to CSV in
  `Documents\CsiMcp\exports` when the rows are cut to max_rows) | csv | json | none; `export_path` returned.
  Output folder: `CSI_MCP_OUTPUT`, else `Documents\CsiMcp`.
- Report R1: `export_report` - PDF (reportlab) with cover, model (counts, case status, project information),
  definitions (materials, sections, patterns, cases, combinations with factors), chosen database tables and headline
  results; program / version, model file and units on every page; wide tables split into column groups; tables
  longer than max_rows_per_table also written to CSV; A4 portrait by default, page_size A3 / A2 / Letter / Legal and
  orientation landscape / auto on request.
- Reference models (`tests/reference_models.py`): SAP2000 portal + shell plate, ETABS 3-storey steel deck + wall,
  SAFE mat on soil springs with column loads; built and saved by `tests/run_live_phase0.bat`.
- requirements: reportlab.

## 0.2.1 - 2026-11-26

Hardening from an external code audit (ChatGPT), reviewed and merged selectively; offline tests 13 -> 21.

- Parameters: Pydantic strict mode, NaN / infinity refused, duplicate object names in one load call refused; tool
  arguments other than `action` / `params` refused with a tool error.
- COM thread: bounded queue (32 pending), COM-initialisation failure reported to callers, shutdown cancels pending
  calls and releases COM on the STA thread; COM released on every exit.
- Protocol: original stdout pipe reserved before COM loads; own stdio framing (1 MB frames, strict JSON-RPC validation,
  initialisation order, duplicate ids, rate limit, 8 MB result cap); heartbeat only with a progress token; `help`
  answers without waiting for the COM thread; `--chatgpt` mode (split tools, short descriptions).
- Model safety: Windows mutex between CSI MCP processes; connection pinned per action (no silent re-attach during an
  edit); lock state that cannot be read blocks edits; `program_path` limited to the CSI install folder or the
  configured `CSI_MCP_<PROGRAM>_EXE`; `disconnect` keeps the connection if `ApplicationExit` fails; a new blank model
  failing on launch is reported.
- Return codes: a missing / non-integer return code is an error; table record count x field count must equal the data
  length; duplicate column keys refused; 10 M-cell limit per table read.
- Analysis: run flags must be readable and at least one case selected; every selected case must finish (status read
  failure reported); frame created but section assignment failed -> error naming the frame.
- Display selection: snapshot required before changing it; restored and read back; errors during restore never hide
  the read error.
- Units: restore checked against `GetPresentUnits`; a restore failure is raised only when it is the only failure.
- Tables: values no longer rounded to 6 decimals (small rotations / drifts kept); numeric columns validated before
  summaries; `!=` filter ignores blanks; OR filters limited to 64 parts; `LoadCombo` kept as text.
- Not taken from the audit: dropping the `(count, list)` signature fallback for `Set...SelectedForDisplay`, dropping
  the keyword fallback for summary tables (both proven on the installed versions), hiding the exception text from the
  model, the unused `tomlkit` requirement, and failing a read when SAFE cannot re-set an empty selection.

## 0.2.0 - 2026-11-26

Restructured to the civil3d / revit MCP pattern and tested live on SAP2000 27.1.0, ETABS 23.2.0 and SAFE 23.3.2.

- One tool `csi(action, params)` (split into `csi_read` / `csi_edit` with `CSI_MCP_SPLIT=1`); every action defined
  once in `server/actions.py` (Pydantic parameters, unknown parameters refused); `catalog.py` renders the tool
  description and `help`; `docs/ACTIONS.md` generated from it.
- Actions: help, list_programs, connect, disconnect, get_model_status, list_definitions, open_or_save_model,
  set_units, create_frame_element, assign_point_or_frame_load, run_analysis, list_available_tables,
  query_database_table, get_analysis_summary.
- Failures come back as tool errors with error_type, message and hint; partial load assignment and analysis problems
  return the full result as the error.
- Progress heartbeat every 10 s while an action runs (analysis, opening files).
- Fixes found by the live tests:
  - SAFE ProgID is `CSI.SAFE.API.ETABSObject` only.
  - `(Untitled)` and ETABS' `.$et` working copy no longer break save / run_analysis.
  - Point / frame names are checked against the model (ETABS and SAFE return 0 for unknown names).
  - Empty display selection (code 1, count 0) read as "nothing selected"; SAFE's internal `~LLRF` case skipped.
  - Display selection changed only when it differs, and restored.
  - Identifier columns (Label, Joint, Story ...) stay text.
  - Base reaction summaries leave out modal (mode-shape) rows.
- `setup_server.bat` (server\.venv, comtypes wrappers pre-generated, client config snippets), `--configure-clients`
  / `--remove-clients`, `build_package.bat` (one-exe package), `install.bat` / `uninstall.bat`,
  `publish_to_github.bat`.
- Tests: offline (fake CSI model), live_read (open programs, units and selection unchanged), live_edit (scratch
  instances, full cycle), protocol / exe checks, SAFE table diagnostic.

## 0.1.0 - 2026-11-26

First version: FastMCP server with nine tools, STA COM thread, units scoping, lock guard, table filters and summaries
(not tested on the CSI programs).
