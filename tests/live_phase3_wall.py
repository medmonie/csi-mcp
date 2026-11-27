"""Hydrostatic test wall used by live_phase3 and diag_divide (SAP2000, raw API set-up)."""
from csi_bridge import api_call, as_list

B, H, GAMMA = 2.0, 4.0, 10.0
KN_M_C = 6


def build_wall(sap):
    api_call("InitializeNewModel", sap.InitializeNewModel, KN_M_C)
    api_call("File.NewBlank", sap.File.NewBlank)
    api_call("PropMaterial.SetMaterial", sap.PropMaterial.SetMaterial, "C30", 2)
    api_call("PropArea.SetShell_1", sap.PropArea.SetShell_1, "WALL300", 1, True, "C30", 0.0, 0.3, 0.3)
    o = api_call("AreaObj.AddByCoord", sap.AreaObj.AddByCoord, 4, [0.0, B, B, 0.0], [0.0, 0.0, 0.0, 0.0],
                 [0.0, 0.0, H, H], "", "WALL300", "W1", "Global")
    wall = next(str(x) for x in o if isinstance(x, str) and x)
    pts = [str(x) for x in as_list(api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, wall)[1])]
    for pt in pts:
        z = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, pt, 0.0, 0.0, 0.0)[2]
        if abs(z) < 1e-6:
            api_call("PointObj.SetRestraint", sap.PointObj.SetRestraint, pt, [True] * 6)
    api_call("LoadPatterns.Add", sap.LoadPatterns.Add, "HYDRO", 8, 0.0, True)
    top = [p for p in pts if abs(api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, p, 0.0, 0.0,
                                          0.0)[2] - H) < 1e-6]
    return wall, top


