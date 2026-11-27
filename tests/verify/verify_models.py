"""V1 model list and the copy rule: originals in "CSI Solved Examples" are never opened in place."""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "CSI Solved Examples"
DEST = ROOT / "tests" / "results" / "verify" / "models"
DIRS = {"SAP2000": SRC / "Sap2000" / "Models" / "Analysis Examples",
        "ETABS": SRC / "Etabs" / "Models" / "Analysis Examples",
        "SAFE": SRC / "Safe" / "Models" / "Analysis Examples"}
MODEL_EXT = {".sdb", ".edb", ".fdb"}

_SAP = ["Example 1-001", "Example 1-003", "Example 1-004", "Example 1-007a", "Example 1-007b", "Example 1-007c",
        "Example 1-008", "Example 1-011", "Example 1-012a", "Example 1-012b", "Example 1-012c", "Example 1-017a",
        "Example 1-017b", "Example 1-019a", "Example 1-019b", "Example 1-019c", "Example 1-020", "Example 1-022",
        "Example 1-024", "Example 2-005a-thin", "Example 2-005b-thin", "Example 2-005e-thin", "Example 2-005f-thin",
        "Example 2-006-thin"]
_ETABS = ["Example 01", "Example 02", "Example 04", "Example 05", "Example 06"]
_SAFE = ["S01a-Thin", "S01b-Thin", "S01c-Thin", "S02a-Thin", "S02b-Thin", "S02c-Thin", "S03a-Thin", "S03b-Thin",
         "S03c-Thin"]
MODELS: dict[str, tuple[str, str]] = {**{n: ("SAP2000", n + ".sdb") for n in _SAP},
                                      **{n: ("ETABS", n + ".edb") for n in _ETABS},
                                      **{n: ("SAFE", n + ".FDB") for n in _SAFE}}


def model_copy(name: str) -> Path:
    """Fresh copy of the model and of the non-model files beside it (spectrum / history functions)."""
    prog, file = MODELS[name]
    src_dir = DIRS[prog]
    src = next((p for p in src_dir.iterdir() if p.name.lower() == file.lower()), None)
    if src is None:
        raise FileNotFoundError(f"{file} not in {src_dir}")
    dest_dir = DEST / prog
    dest_dir.mkdir(parents=True, exist_ok=True)
    for aux in src_dir.iterdir():
        if aux.is_file() and aux.suffix.lower() not in MODEL_EXT and aux.suffix.lower() not in (".xls", ".mdb"):
            target = dest_dir / aux.name
            if not target.exists():
                shutil.copy2(aux, target)
    dest = dest_dir / src.name
    for old in dest_dir.glob(src.stem + ".*"):      # leftovers of an earlier run (results, logs)
        if old.suffix.lower() not in (".txt",) and old.name != src.name:
            old.unlink(missing_ok=True)
    shutil.copy2(src, dest)
    return dest
