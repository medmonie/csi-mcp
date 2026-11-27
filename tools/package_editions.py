"""Make the three program packages from ONE server build (run by build_package.bat).

    package_editions.py <built exe>      e.g. dist\\_build\\csi-mcp-server.exe

For each program: dist\\<Package>\\ with <program>-mcp-server.exe (the same build, renamed: the exe takes its program
from its name, see server/edition.py), install.bat / uninstall.bat, INSTALL.txt, ACTIONS.md of that edition,
README.md, CHANGELOG.md, LICENSE.md; then dist\\<Package>.zip.
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
import catalog  # noqa: E402
import edition  # noqa: E402
from gen_docs import actions_markdown  # noqa: E402

MIN = {"ETABS": "ETABS 18", "SAP2000": "SAP2000 21", "SAFE": "SAFE 20"}


def main(built: str) -> int:
    exe = Path(built)
    if not exe.is_file():
        print("built exe not found:", exe)
        return 1
    dist = ROOT / "dist"
    for prog in edition.ALL:
        pkg = edition.PACKAGE[prog]
        out = dist / pkg
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        shutil.copy2(exe, out / edition.EXE[prog])
        for f in ("install.bat", "uninstall.bat"):
            shutil.copy2(ROOT / "package" / f, out / f)
        for f in ("README.md", "CHANGELOG.md", "LICENSE.md"):
            shutil.copy2(ROOT / f, out / f)
        text = (ROOT / "package" / "INSTALL.txt").read_text(encoding="utf-8")
        for k, v in {"{PROGRAM}": prog, "{VERSION}": catalog.SERVER_VERSION, "{PACKAGE}": pkg,
                     "{NAME}": prog.lower(), "{EXE}": edition.EXE[prog], "{MIN}": MIN[prog],
                     "{REGISTER}": f"Register{prog}.exe"}.items():
            text = text.replace(k, v)
        (out / "INSTALL.txt").write_text(text, encoding="utf-8")
        edition.force(prog)
        (out / "ACTIONS.md").write_text(actions_markdown(), encoding="utf-8")
        edition.force(None)
        zpath = dist / f"{pkg}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.iterdir()):
                z.write(f, f.name)
        print(f"{pkg}: {zpath.name} {zpath.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "dist" / "_build" / "csi-mcp-server.exe")))
