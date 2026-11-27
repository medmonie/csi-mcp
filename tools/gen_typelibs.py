"""Generate the comtypes wrappers for the installed CSI API type libraries (run by setup_server.bat).

The first COM call otherwise does this inside Claude's first request (10-30 s). Missing programs are reported, not
errors. Prints where comtypes keeps the generated modules (delete them there after a CSI upgrade if calls fail).
"""
import os
import sys

try:
    import comtypes.client
except ImportError:
    print("comtypes not installed (Windows only)")
    sys.exit(0)

for progid in ("ETABSv1.Helper", "SAP2000v1.Helper", "SAFEv1.Helper", "CSiAPIv1.Helper"):
    try:
        comtypes.client.CreateObject(progid)
        print(f"typelib ok: {progid}")
    except OSError as exc:
        print(f"typelib not available: {progid} ({exc})")
import comtypes.gen  # noqa: E402
print("comtypes cache:", os.path.dirname(comtypes.gen.__file__))
