"""SAFE-only API features and differences (from docs/api: SAFEv1 23.3; SAFE uses the ETABS object model)."""
from __future__ import annotations

CAPABILITIES = {
    "area_auto_mesh": "",
    "area_divide": "",
    "joint_patterns": "",
    "surface_pressure": "",
    "stories": "Story.GetStories_2",
    "piers_spandrels": "",
    "bulk_object_readers": "PointObj.GetAllPoints / FrameObj.GetAllFrames / AreaObj.GetAllAreas",
}
USAGE = (
    "SAFE: slabs, beams, columns, walls and supports, usually on the base level; objects carry a label and a story "
    "(get_objects takes story). SAFE analyses out-of-plane bending only (UZ, RX, RY per joint): axial forces, "
    "in-plane loads and uniform temperature changes give no results, and temperature loads on frames are ignored "
    "(measured). No spectrum / time-history functions or cases (they come with floors exported from ETABS). "
    "Slab loads are "
    "uniform area loads (assign_area_load). SAFE meshes with its own options, "
    "which are not in the API; joint patterns, surface pressure, piers and spandrels do not exist in SAFE. New areas "
    "can only be drawn at the base level of a NewGridOnly model (API limit of SAFE 23.3)."
)
ALTERNATIVES = {
    "radial": "SAFE has no replicate API; replicate_objects copies linearly (mode linear), so place rotated copies "
              "with create_points / create_frames / create_areas." ,
    "mirror": "SAFE has no replicate API; draw the mirrored objects with create_points / create_frames / create_areas.",
    "divide_frames": "SAFE has no API to divide frame objects; delete the frame and create the pieces "
                     "(create_frames), or set the frame auto mesh with edit_database_table.",
    "merge_points": "SAFE has no point-merge API; drawing at an existing point reuses it, and move_objects can bring "
                    "points together.",
    "extrude_objects": "SAFE has no extrude API; create the frames / areas with create_frames / create_areas.",
    "response_spectrum": "SAFE has no spectrum functions; it takes response-spectrum cases with a floor exported from "
                         "ETABS.",
    "linear_modal_history": "SAFE has no time-history functions or cases.",
    "frame": "SAFE ignores temperature loads on frames (measured: uniform and both gradients give no forces); model "
             "the effect with loads, or use SAP2000 / ETABS.",
    "story_drifts": "In SAFE use get_results result_type story_drifts.",
    "pier_forces": "SAFE models have no piers (the API call exists but returns nothing).",
    "spandrel_forces": "SAFE models have no spandrels.",
    "story_charts": "Use get_results story_drifts for SAFE drifts.",
    "drift_limit": "Only used with ETABS story drift charts.",
    "face": "SAFE has no surface pressure, so there is no face to choose.",
    "joint_pattern": "SAFE has no joint patterns.",

    "area_auto_mesh": "SAFE meshes slabs with its own mesh options (program settings, not in the API); draw smaller "
                      "areas where a finer mesh is needed.",
    "area_divide": "Draw the slab as several smaller areas.",
    "joint_patterns": "SAFE has no joint patterns; use uniform area loads per region (assign_area_load type uniform).",
    "surface_pressure": "Use uniform area loads (assign_area_load type uniform).",
    "piers_spandrels": "SAFE has no piers or spandrels.",
}
