r"""Diagnostic: why AreaObj.AddByCoord fails in the SAFE reference model. Scratch instance, closed at the end.
Output: tests\results\diag_safe_ref.txt"""
import sys

from live_common import bridge, com, finish  # noqa: I001
from csi_bridge import as_list, split_result  # noqa: E402


def run():
    conn = bridge.launch_new("SAFE", visible=True)
    try:
        sap = conn.sap_model
        print("init", sap.InitializeNewModel(6))
        print("NewGridOnly", sap.File.NewGridOnly(1, 3.0, 3.0, 3, 3, 6.0, 6.0))
        st = sap.Story.GetStories_2()
        print("GetStories_2", st)
        print("material", sap.PropMaterial.SetMaterial("C30", 2))
        print("slab", sap.PropArea.SetSlab("MAT500", 0, 1, "C30", 0.5))
        print("area props", sap.PropArea.GetNameList())
        for z in (3.0, 0.0, 6.0):
            for prop in ("MAT500", "Default"):
                r = sap.AreaObj.AddByCoord(4, [0.0, 6.0, 6.0, 0.0], [0.0, 0.0, 6.0, 6.0], [z] * 4, "", prop, "", "Global")
                print(f"AddByCoord z={z} prop={prop}:", split_result(r)[0], r[-2:] if isinstance(r, (list, tuple)) else r)
        print("areas", sap.AreaObj.GetNameList())
        print("NewBlank", sap.File.NewBlank(), "-> after blank AddByCoord z=0:",
              sap.AreaObj.AddByCoord(4, [0.0, 6.0, 6.0, 0.0], [0.0, 0.0, 6.0, 6.0], [0.0] * 4, "", "Default", "", "Global"))
        print("stories after blank", sap.Story.GetStories_2())
    finally:
        bridge.disconnect("SAFE", True, False)


com(run)
sys.exit(finish())
