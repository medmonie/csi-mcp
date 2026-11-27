"""ETABS-only API features and differences (from docs/api: ETABSv1 23.2)."""
from __future__ import annotations

CAPABILITIES = {
    "area_auto_mesh": "",
    "area_divide": "",
    "joint_patterns": "",
    "surface_pressure": "",
    "stories": "Story.GetStories_2",
    "piers_spandrels": "PierLabel / SpandrelLabel, Results.PierForce / SpandrelForce",
    "bulk_object_readers": "PointObj.GetAllPoints / FrameObj.GetAllFrames / AreaObj.GetAllAreas",
}
USAGE = (
    "ETABS: models are story-based; points, frames and areas carry a label and a story (get_objects takes story). "
    "Grids come from grid systems (get_grids), levels from get_stories. Story drifts, story shears, pier and spandrel "
    "forces are ETABS results (get_results). Older models pad story names with spaces: compare trimmed names. Column "
    "local axes vary from model to model: read them before calling a moment M2 or M3. ETABS meshes floors and walls "
    "with its own options, which are not in the API; joint patterns and surface pressure do not exist in ETABS (use "
    "uniform area loads, per strip for a load that varies). Joints at the Base level without a restraint are held "
    "fixed in the analysis (measured): build members that must deflect above the Base level. User spectrum / time-history functions and the RS / "
    "modal-history case options (combinations, steps) go through the program's database tables (other rows kept); "
    "these cases use the model's modal damping (0.05 by default)."
)
ALTERNATIVES = {
    "radial": "ETABS has no replicate API; replicate_objects copies linearly (mode linear), so place rotated copies "
              "with create_points / create_frames / create_areas." ,
    "mirror": "ETABS has no replicate API; draw the mirrored objects with create_points / create_frames / create_areas.",
    "divide_frames": "ETABS has no API to divide frame objects. For analysis, ETABS meshes frames automatically at "
                     "intersecting objects (table 'Frame Assignments - Frame Auto Mesh Options' through "
                     "edit_database_table); for objects, delete the frame and create the pieces (create_frames).",
    "merge_points": "ETABS has no point-merge API; drawing at an existing point reuses it (create_points / "
                    "create_frames), and move_objects can bring points together.",
    "extrude_objects": "ETABS has no extrude API; create the frames / areas with create_frames / create_areas.",
    "face": "ETABS has no surface pressure, so there is no face to choose.",
    "joint_pattern": "ETABS has no joint patterns.",

    "area_auto_mesh": "ETABS meshes floors and walls with its own auto-mesh options (program settings / assignment "
                      "tables, not in the API); in the model, draw the area as smaller areas if a finer mesh is needed.",
    "area_divide": "Draw the area as several smaller areas.",
    "joint_patterns": "ETABS has no joint patterns. For a load that varies over a wall, split the wall into strips "
                      "and give each its own uniform load (assign_area_load type uniform).",
    "surface_pressure": "Use uniform area loads (assign_area_load type uniform), per strip for a varying load.",
}


def story_name(raw: str) -> str:
    """Older ETABS models keep story names padded with spaces ('1ST     '); compare on the trimmed name."""
    return str(raw).strip()
