"""
csi_bridge.py - Windows COM bridge between Python and the CSI Open API (OAPI)
for ETABS, SAP2000 and SAFE.

Design notes
------------
* COM apartment model: every CSI COM proxy is created on, and only ever used
  from, ONE dedicated single-threaded-apartment (STA) worker thread owned by
  `ComExecutor`. MCP tool handlers are async and may run on any thread, so they
  hand their work to that thread. This removes "CoInitialize has not been
  called" errors and the subtler RPC_E_WRONG_THREAD failures you get when a
  cached COM object is touched from a different thread than the one that
  created it. CoInitializeEx/CoUninitialize are called exactly once, on that
  thread, at start-up and shutdown.
* Return codes: CSI methods return 0 on success. With comtypes, a method with
  ByRef/out parameters returns a list whose LAST element is the return code,
  e.g. FrameObj.AddByCoord -> [Name, ret]. `api_call` normalises both shapes
  and raises `CSIApiError` on ret != 0.
* Units: every read/write that involves numbers runs inside `units_scope`,
  which sets SapModel.SetPresentUnits explicitly and restores the user's
  previous units afterwards so the GUI is left as it was found.
* stdout hygiene: the MCP stdio transport owns stdout. comtypes may print while
  generating wrappers for a type library, so all COM work runs with stdout
  redirected to stderr.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import importlib
import logging
import os
import queue
from pathlib import Path
import re
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Literal, Sequence, TypeVar, get_args

try:  # comtypes is Windows-only; importing on other platforms enables offline tests.
    import comtypes
    import comtypes.client
    from comtypes import COMError

    COM_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on non-Windows hosts only
    comtypes = None  # type: ignore[assignment]
    COM_AVAILABLE = False

    class COMError(Exception):  # type: ignore[no-redef]
        """Stand-in so `except COMError` clauses stay valid without comtypes."""


log = logging.getLogger("csi_mcp.bridge")

T = TypeVar("T")

# --------------------------------------------------------------------------- #
# Static configuration
# --------------------------------------------------------------------------- #

Software = Literal["ETABS", "SAP2000", "SAFE"]
SOFTWARE_NAMES: tuple[str, ...] = get_args(Software)

# eUnits enumeration (identical in ETABS, SAP2000 and SAFE OAPI).
UnitPreset = Literal[
    "lb_in_F", "lb_ft_F", "kip_in_F", "kip_ft_F",
    "kN_mm_C", "kN_m_C", "kgf_mm_C", "kgf_m_C",
    "N_mm_C", "N_m_C", "Ton_mm_C", "Ton_m_C",
    "kN_cm_C", "kgf_cm_C", "N_cm_C", "Ton_cm_C",
]
UNIT_PRESETS: dict[str, int] = {name: code for code, name in enumerate(get_args(UnitPreset), start=1)}
UNIT_CODES: dict[int, str] = {code: name for name, code in UNIT_PRESETS.items()}


def describe_units(preset: str) -> dict[str, str]:
    """'kN_m_C' -> {'preset': 'kN_m_C', 'force': 'kN', 'length': 'm', 'temperature': 'C'}."""
    if preset not in UNIT_PRESETS:
        return {"preset": preset}
    force, length, temp = preset.split("_")
    return {"preset": preset, "force": force, "length": length, "temperature": temp}


@dataclass(frozen=True)
class SoftwareSpec:
    """Per-program API identifiers and quirks."""

    name: str
    progids: tuple[str, ...]          # ProgIDs of the running application object
    helper_progids: tuple[str, ...]   # cHelper COM classes (program-specific first, cross-product last)
    model_extensions: tuple[str, ...]
    group_all_candidates: tuple[str, ...]  # GroupName values meaning "all objects" for table reads
    register_tool: str                # registration utility in the install folder
    has_stories: bool


SPECS: dict[str, SoftwareSpec] = {
    "ETABS": SoftwareSpec(
        name="ETABS",
        progids=("CSI.ETABS.API.ETABSObject",),
        helper_progids=("ETABSv1.Helper", "CSiAPIv1.Helper"),
        model_extensions=(".edb",),
        group_all_candidates=("All", ""),
        register_tool="RegisterETABS.exe",
        has_stories=True,
    ),
    "SAP2000": SoftwareSpec(
        name="SAP2000",
        progids=("CSI.SAP2000.API.SapObject",),
        helper_progids=("SAP2000v1.Helper", "CSiAPIv1.Helper"),
        model_extensions=(".sdb",),
        group_all_candidates=("All", "ALL", ""),
        register_tool="RegisterSAP2000.exe",
        has_stories=False,
    ),
    "SAFE": SoftwareSpec(
        name="SAFE",
        # SAFE 20+ is built on the ETABS platform and registers as "CSI.SAFE.API.ETABSObject"
        # (checked on SAFE 23.3: "CSI.SAFE.API.SAFEObject" is not a registered class).
        progids=("CSI.SAFE.API.ETABSObject",),
        helper_progids=("SAFEv1.Helper", "CSiAPIv1.Helper"),
        model_extensions=(".fdb",),
        group_all_candidates=("", "All"),  # GroupName is inactive in SAFE table reads
        register_tool="RegisterSAFE.exe",
        has_stories=False,
    ),
}

LOAD_PATTERN_TYPES = {
    1: "Dead", 2: "SuperDead", 3: "Live", 4: "ReduceLive", 5: "Quake", 6: "Wind",
    7: "Snow", 8: "Other", 9: "Move", 10: "Temperature", 11: "RoofLive", 12: "Notional",
    13: "PatternLive", 34: "PrestressFinal", 59: "PrestressTransfer",   # prestress: measured in SAFE / ETABS
}
LOAD_CASE_TYPES = {
    1: "LinearStatic", 2: "NonlinearStatic", 3: "Modal", 4: "ResponseSpectrum",
    5: "LinearHistory", 6: "NonlinearHistory", 7: "LinearDynamic", 8: "NonlinearDynamic",
    9: "MovingLoad", 10: "Buckling", 11: "SteadyState", 12: "PowerSpectralDensity",
    13: "LinearStaticMultiStep", 14: "HyperStatic", 15: "ExternalResults",
    16: "StagedConstruction", 17: "NonlinearStaticMultiStep",
}
CASE_STATUS = {1: "NotRun", 2: "CouldNotStart", 3: "NotFinished", 4: "Finished"}


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #

class CSIError(Exception):
    """Base error; `hint` carries an actionable next step for the user/LLM."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class CSIConnectionError(CSIError):
    """The CSI application could not be reached over COM."""


class CSIApiError(CSIError):
    """A CSI API call returned a non-zero code or raised a COM error."""

    def __init__(self, method: str, ret: Any = None, detail: str = "", hint: str = "") -> None:
        msg = f"CSI API call '{method}' failed"
        if ret is not None:
            msg += f" (return code {ret})"
        if detail:
            msg += f": {detail}"
        super().__init__(msg, hint)
        self.method = method
        self.ret = ret


class CSIValidationError(CSIError):
    """Invalid user input detected before touching the model."""


# --------------------------------------------------------------------------- #
# Paths given by the model
# --------------------------------------------------------------------------- #

# Folder variables a path may use. Nothing else in the environment is expanded (other names stay as typed): a path
# parameter must not be a way to read variables (API keys, tokens) back through the path echoed in an answer or an
# error message.
PATH_VARIABLES = ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
                  "PUBLIC", "TEMP", "TMP", "ONEDRIVE", "ONEDRIVECOMMERCIAL", "ONEDRIVECONSUMER", "CSI_MCP_OUTPUT")
_PATH_VAR = re.compile(r"%([^%\\/:]+)%|\$\{([^}]+)\}|\$(\w+)")


def user_path(text: str) -> str:
    """Absolute path from a path parameter: ~ and the folder variables above are expanded, nothing else."""
    def expand(found: re.Match) -> str:
        name = next(g for g in found.groups() if g)
        if name.upper() not in PATH_VARIABLES:
            return found.group(0)      # e.g. a folder called $2026, ETABS' model.$et, or a variable not for paths
        return os.environ.get(name) or os.environ.get(name.upper()) or found.group(0)
    return os.path.abspath(os.path.expanduser(_PATH_VAR.sub(expand, str(text))))


# --------------------------------------------------------------------------- #
# Return-code helpers
# --------------------------------------------------------------------------- #

def split_result(result: Any) -> tuple[Any, list[Any]]:
    """Split a comtypes result into (return_code, [out-parameters...])."""
    if isinstance(result, (list, tuple)):
        if not result:
            return None, []
        return result[-1], list(result[:-1])
    return result, []


def api_call(method: str, func: Callable[..., Any], *args: Any, hint: str = "") -> list[Any]:
    """Call a CSI method that returns an int status (optionally after out-params).

    Returns the list of out-parameters. Raises CSIApiError when ret != 0 or the
    COM layer raises.
    """
    try:
        result = func(*args)
    except (COMError, OSError) as exc:
        raise CSIApiError(method, detail=f"COM error: {exc}", hint=hint) from exc
    ret, outs = split_result(result)
    if type(ret) is not int:
        raise CSIApiError(method, detail="missing or invalid integer return code", hint=hint)
    if ret != 0:
        raise CSIApiError(method, ret=ret, hint=hint)
    return outs


def api_value(method: str, func: Callable[..., T], *args: Any) -> T:
    """Call a CSI method that returns a plain value (Count(), GetModelIsLocked()...)."""
    try:
        return func(*args)
    except (COMError, OSError) as exc:
        raise CSIApiError(method, detail=f"COM error: {exc}") from exc


def as_list(value: Any) -> list[Any]:
    """Convert a COM SAFEARRAY / tuple / None to a Python list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


# --------------------------------------------------------------------------- #
# COM executor: a single STA thread that owns all CSI COM objects
# --------------------------------------------------------------------------- #

class ComExecutor:
    """Runs callables on one dedicated STA thread with COM initialised.

    A hand-rolled daemon thread + queue is used instead of ThreadPoolExecutor
    because Python shuts executors down *before* atexit handlers run, which
    would make a clean CoUninitialize at exit impossible.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=33)
        self._slots = threading.BoundedSemaphore(32)
        self._closed = False
        self._lock = threading.Lock()
        self._ready: Future = Future()
        self._joined = False
        self._thread = threading.Thread(target=self._loop, name="csi-com-sta", daemon=True)
        self._started = False      # the STA thread (and COM) start with the first call, not at import (audit L16):
                                   # --configure-clients, help and the tests then never touch COM

    def _ensure_started(self) -> None:
        with self._lock:
            if not self._started and not self._closed:
                self._started = True
                self._thread.start()

    def _loop(self) -> None:
        try:
            if COM_AVAILABLE:
                comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except Exception as exc:
            self._ready.set_exception(CSIConnectionError(f"COM initialization failed: {exc}"))
            return
        self._ready.set_result(None)
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                if callable(item):  # shutdown: release COM references, then leave the loop
                    try:
                        item()
                    except Exception as exc:  # noqa: BLE001 - shutdown must never raise
                        log.warning("COM release issue: %s", exc)
                    break
                future, fn, args, kwargs = item
                self._slots.release()
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - delivered to the caller
                    future.set_exception(exc)
                else:
                    future.set_result(result)
        finally:
            gc.collect()  # drop COM proxies before uninitialising
            if COM_AVAILABLE:
                comtypes.CoUninitialize()

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future:
        future: Future = Future()
        self._ensure_started()
        if not self._started:
            raise RuntimeError("COM executor is shut down")
        self._ready.result()
        with self._lock:
            if self._closed:
                raise RuntimeError("COM executor is shut down")
            if not self._slots.acquire(blocking=False):
                raise CSIError("CSI worker queue is full. This call did not start.")
            self._queue.put_nowait((future, fn, args, kwargs))
        return future

    def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Blocking call; safe to use re-entrantly from the STA thread itself."""
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        return self.submit(fn, *args, **kwargs).result()

    async def arun(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Awaitable call for async MCP handlers (does not block the event loop)."""
        return await asyncio.wrap_future(self.submit(fn, *args, **kwargs))

    def shutdown(self, before_uninit: Callable[[], None] | None = None, timeout: float = 30.0) -> None:
        """Release COM references, then CoUninitialize on the STA thread."""
        with self._lock:
            if not self._started:          # never used: nothing to release
                self._closed = True
                return
            if not self._closed:
                self._closed = True
                while True:
                    try:
                        pending = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    pending[0].cancel()
                    self._slots.release()
                self._queue.put_nowait(before_uninit)
        if self._joined:      # main() and atexit both call this: wait for a running CSI call once, not twice
            return
        self._joined = True
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            log.warning("CSI call still running at shutdown. Verify the model before retrying.")


# --------------------------------------------------------------------------- #
# Connection manager
# --------------------------------------------------------------------------- #

@dataclass
class Connection:
    software: str
    api_object: Any
    attached_via: str
    launched_by_bridge: bool
    connected_at: float

    @property
    def sap_model(self) -> Any:
        return self.api_object.SapModel


class CSIBridge:
    """Connection, unit, lock and table management for ETABS / SAP2000 / SAFE.

    All public methods must be executed on the ComExecutor's STA thread.
    """

    def __init__(self) -> None:
        self._connections: dict[str, Connection] = {}
        self._pinned: dict[str, Connection] | None = None
        self._editing = False

    @contextlib.contextmanager
    def action_scope(self, editing: bool):
        self._pinned, self._editing = {}, editing
        try:
            yield
        finally:
            self._pinned, self._editing = None, False

    # ----------------------------- connection ------------------------------ #

    @staticmethod
    def spec(software: str) -> SoftwareSpec:
        key = software.strip().upper()
        if key not in SPECS:
            raise CSIValidationError(
                f"Unknown software '{software}'.", hint=f"Use one of: {', '.join(SOFTWARE_NAMES)}."
            )
        return SPECS[key]

    @staticmethod
    def _probe(api_object: Any) -> bool:
        """Cheap liveness check - catches stale ROT entries from crashed instances."""
        try:
            if not api_object:
                return False
            sap = api_object.SapModel
            if not sap:
                return False
            sap.GetPresentUnits()
            return True
        except (COMError, OSError, AttributeError, ValueError):
            return False

    def _not_running_hint(self, spec: SoftwareSpec) -> str:
        return (
            f"Open {spec.name} (with a model) and retry, or call `connect` with "
            f"launch_if_not_running=true. If {spec.name} IS open: (1) run Claude Desktop and "
            f"{spec.name} at the same privilege level (both normal or both 'Run as administrator'); "
            f"(2) register the API by running {spec.register_tool} as administrator from the "
            f"{spec.name} install folder; (3) use 64-bit Python; (4) if several CSI versions "
            f"are installed, the most recently registered one answers COM requests."
        )

    def _helper(self, spec: SoftwareSpec, errors: list[str]) -> Any | None:
        """Create the cHelper object (program-specific library first, cross-product last)."""
        for helper_progid in spec.helper_progids:
            try:
                helper = comtypes.client.CreateObject(helper_progid)
                lib_name = helper_progid.split(".")[0]
                try:
                    lib = importlib.import_module(f"comtypes.gen.{lib_name}")
                    helper = helper.QueryInterface(lib.cHelper)
                except (ImportError, AttributeError, COMError) as exc:
                    log.debug("cHelper QueryInterface via %s skipped: %s", lib_name, exc)
                return helper
            except (OSError, COMError) as exc:
                errors.append(f"CreateObject('{helper_progid}'): {exc}")
        return None

    def _attach(self, spec: SoftwareSpec) -> tuple[Any, str]:
        errors: list[str] = []
        for progid in spec.progids:
            try:
                obj = comtypes.client.GetActiveObject(progid)
                if self._probe(obj):
                    return obj, f"GetActiveObject('{progid}')"
                errors.append(f"GetActiveObject('{progid}'): stale or model not ready")
            except (OSError, COMError) as exc:
                errors.append(f"GetActiveObject('{progid}'): {exc}")

        helper = self._helper(spec, errors)
        if helper is not None:
            for progid in spec.progids:
                try:
                    obj = helper.GetObject(progid)
                    if self._probe(obj):
                        return obj, f"cHelper.GetObject('{progid}')"
                    errors.append(f"cHelper.GetObject('{progid}'): no running instance")
                except (OSError, COMError) as exc:
                    errors.append(f"cHelper.GetObject('{progid}'): {exc}")

        log.info("Attach to %s failed: %s", spec.name, " | ".join(errors))
        raise CSIConnectionError(
            f"Could not attach to a running {spec.name} instance. Tried: " + "; ".join(errors),
            hint=self._not_running_hint(spec),
        )

    def _launch(self, spec: SoftwareSpec, visible: bool, program_path: str | None) -> tuple[Any, str]:
        errors: list[str] = []
        helper = self._helper(spec, errors)
        if helper is None:
            raise CSIConnectionError(
                f"Cannot launch {spec.name}: API helper not registered. " + "; ".join(errors),
                hint=f"Run {spec.register_tool} as administrator from the {spec.name} install folder.",
            )
        if program_path:
            supplied = Path(program_path).resolve()
            configured = os.environ.get(f"CSI_MCP_{spec.name}_EXE")
            vendor = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Computers and Structures"
            if not ((supplied.is_relative_to(vendor.resolve()) and supplied.name.lower() == spec.name.lower() + ".exe")
                    or (configured and supplied == Path(configured).resolve())):
                raise CSIValidationError(f"Custom program_path must match the locally configured CSI_MCP_{spec.name}_EXE.")
            if not os.path.isfile(program_path):
                raise CSIValidationError(f"program_path not found: {program_path}")
            obj = api_value("cHelper.CreateObject", helper.CreateObject, str(supplied))
            via = f"cHelper.CreateObject('{program_path}')"
        else:
            obj = None
            for progid in spec.progids:
                try:
                    obj = helper.CreateObjectProgID(progid)
                    if obj:
                        via = f"cHelper.CreateObjectProgID('{progid}')"
                        break
                except (OSError, COMError) as exc:
                    errors.append(f"CreateObjectProgID('{progid}'): {exc}")
            if not obj:
                raise CSIConnectionError(
                    f"Could not start {spec.name}. " + "; ".join(errors),
                    hint="Pass program_path pointing at the .exe, e.g. "
                    r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe",
                )
        api_call(f"{spec.name}.ApplicationStart", obj.ApplicationStart)
        if not visible:
            with contextlib.suppress(CSIApiError, AttributeError):
                api_call(f"{spec.name}.Hide", obj.Hide)
        return obj, via

    def connect(
        self,
        software: str,
        *,
        launch_if_not_running: bool = False,
        visible: bool = True,
        model_path: str | None = None,
        program_path: str | None = None,
    ) -> Connection:
        """Attach to a running instance (default) or launch a new one.

        If `model_path` is given it is opened after connecting. A freshly launched
        instance without `model_path` gets a blank model in kN_m_C.
        """
        if not COM_AVAILABLE and software.upper() not in self._connections:
            raise CSIConnectionError(
                "comtypes is not available - this server must run on Windows with comtypes installed.",
                hint="pip install comtypes (Windows, 64-bit Python).",
            )
        spec = self.spec(software)
        existing = self._connections.get(spec.name)
        if existing and self._probe(existing.api_object):
            conn = existing
        else:
            self._connections.pop(spec.name, None)
            launched = False
            try:
                obj, via = self._attach(spec)
            except CSIConnectionError:
                if not launch_if_not_running:
                    raise
                obj, via = self._launch(spec, visible, program_path)
                launched = True
            conn = Connection(spec.name, obj, via, launched, time.time())
            self._connections[spec.name] = conn
            if launched and not model_path:
                sap = conn.sap_model
                api_call("InitializeNewModel", sap.InitializeNewModel, UNIT_PRESETS["kN_m_C"])
                api_call("File.NewBlank", sap.File.NewBlank)
        if model_path:
            self.open_model(spec.name, model_path)
        return conn

    def launch_new(self, software: str, *, visible: bool = True, program_path: str | None = None) -> Connection:
        """Always start a NEW instance (never attach) and make it the active connection for `software`.

        Used by the live tests so they work in their own scratch instance and never touch the user's open model.
        """
        if not COM_AVAILABLE:
            raise CSIConnectionError("comtypes is not available - Windows only.")
        spec = self.spec(software)
        obj, via = self._launch(spec, visible, program_path)
        conn = Connection(spec.name, obj, via, True, time.time())
        self._connections[spec.name] = conn
        return conn

    def attach_external(self, software: str, api_object: Any, via: str = "external") -> Connection:
        """Register an already-obtained API object (used by tests and embedding hosts)."""
        spec = self.spec(software)
        conn = Connection(spec.name, api_object, via, False, time.time())
        self._connections[spec.name] = conn
        return conn

    def connection(self, software: str) -> Connection:
        """Return a live connection, auto-attaching to a running instance if needed."""
        spec = self.spec(software)
        if self._pinned is not None and spec.name in self._pinned:
            return self._pinned[spec.name]
        conn = self._connections.get(spec.name)
        if not conn or not self._probe(conn.api_object):
            if conn and (self._editing or conn.launched_by_bridge):
                raise CSIConnectionError("Previous CSI instance is unavailable. Check model status before editing.")
            conn = self.connect(spec.name)
        if self._pinned is not None:
            self._pinned[spec.name] = conn
        return conn

    def sap_model(self, software: str) -> Any:
        return self.connection(software).sap_model

    def cached_filename(self, software: str) -> str | None:
        conn = self._connections.get(software)
        return (self.model_filename(conn.sap_model, software) or None) if conn else None

    def disconnect(self, software: str, close_application: bool = False, save_model: bool = False) -> dict:
        spec = self.spec(software)
        conn = self._connections.get(spec.name)
        if conn is None:
            return {"software": spec.name, "was_connected": False}
        closed = False
        note = None
        if close_application:
            try:
                api_call(f"{spec.name}.ApplicationExit", conn.api_object.ApplicationExit, bool(save_model))
            except CSIApiError:
                # SAFE 23.3 can exit before it answers (RPC 0x800706BE). Closed only if it no longer responds.
                if self._probe(conn.api_object):
                    raise
                note = "The program exited without confirming; it no longer responds."
            closed = True
        del self._connections[spec.name]
        del conn
        gc.collect()
        out = {"software": spec.name, "was_connected": True, "application_closed": closed}
        if note:
            out["note"] = note
        return out

    def release_all(self) -> None:
        self._connections.clear()

    # ------------------------------ model files ---------------------------- #

    def model_filename(self, sap: Any, software: str | None = None) -> str:
        """Full path of the active model ('' if it has never been saved).

        ETABS and SAFE answer '(Untitled)' for an unsaved model and may leave out the folder or the extension,
        so the folder comes from GetModelFilepath and the program's extension is added when missing.
        """
        try:
            result = sap.GetModelFilename(True)
        except TypeError:
            result = sap.GetModelFilename()
        except (COMError, OSError) as exc:
            raise CSIApiError("GetModelFilename", detail=str(exc)) from exc
        if isinstance(result, (list, tuple)):  # defensive: some builds return [name, ret]
            result = result[0] if result else ""
        name = str(result or "").strip()
        if not name or name.lower().startswith("(untitled"):
            return ""
        if not os.path.isabs(name):
            with contextlib.suppress(COMError, OSError, AttributeError, TypeError):
                folder = sap.GetModelFilepath()
                folder = folder[0] if isinstance(folder, (list, tuple)) else folder
                if folder:
                    name = os.path.join(str(folder), name)
        stem, ext = os.path.splitext(name)
        if software and (not ext or ext.startswith(".$")):
            # ETABS reports its working copy (model.$et) while the model is open; the user's file is model.edb.
            name = stem + self.spec(software).model_extensions[0]
        return name

    def open_model(self, software: str, file_path: str) -> str:
        spec = self.spec(software)
        path = user_path(file_path)
        if not os.path.isfile(path):
            raise CSIValidationError(f"Model file not found: {path}")
        ext = os.path.splitext(path)[1].lower()
        if ext not in spec.model_extensions:
            raise CSIValidationError(
                f"{spec.name} expects {', '.join(spec.model_extensions)} files, got '{ext}'.",
                hint="Pick the software that matches the model file type.",
            )
        sap = self.connection(software).sap_model
        api_call("File.OpenFile", sap.File.OpenFile, path)
        return path

    def save_model(self, software: str, file_path: str | None = None) -> str:
        spec = self.spec(software)
        sap = self.sap_model(software)
        target = file_path or self.model_filename(sap, spec.name)
        if not target:
            raise CSIValidationError(
                "The active model has never been saved, so it has no file name.",
                hint=f"Call open_or_save_model with action='save' and a file_path ending in "
                f"{spec.model_extensions[0]}.",
            )
        target = user_path(target)
        if os.path.splitext(target)[1].lower() not in spec.model_extensions:
            raise CSIValidationError(f"{spec.name} models must be saved as {spec.model_extensions[0]}.")
        folder = os.path.dirname(target)
        if folder and not os.path.isdir(folder):
            raise CSIValidationError(f"Folder does not exist: {folder}")
        api_call("File.Save", sap.File.Save, target)
        return target

    # --------------------------------- units ------------------------------- #

    def present_units(self, sap: Any) -> str:
        code = api_value("GetPresentUnits", sap.GetPresentUnits)
        return UNIT_CODES.get(int(code), f"unknown({code})")

    def set_units(self, sap: Any, preset: str) -> str:
        if preset not in UNIT_PRESETS:
            raise CSIValidationError(
                f"Unknown unit preset '{preset}'.", hint=f"Use one of: {', '.join(UNIT_PRESETS)}."
            )
        api_call("SetPresentUnits", sap.SetPresentUnits, UNIT_PRESETS[preset])
        actual = self.present_units(sap)
        if actual != preset:
            raise CSIApiError("SetPresentUnits", detail=f"requested {preset}, model reports {actual}")
        return actual

    @contextlib.contextmanager
    def units_scope(self, sap: Any, preset: str, restore: bool = True) -> Iterator[str]:
        """Set units explicitly for the duration of an operation, then restore."""
        previous = api_value("GetPresentUnits", sap.GetPresentUnits)
        if preset not in UNIT_PRESETS:
            raise CSIValidationError(f"Unknown unit preset '{preset}'.")
        changed = int(previous) != UNIT_PRESETS[preset]
        try:
            if changed:
                self.set_units(sap, preset)
            yield preset
        finally:
            if restore and changed:
                body_failed = sys.exc_info()[0] is not None
                try:
                    api_call("Restore SetPresentUnits", sap.SetPresentUnits, int(previous),
                             hint="The operation may already have taken effect. Inspect the model and display units.")
                    if api_value("GetPresentUnits", sap.GetPresentUnits) != previous:
                        raise CSIApiError("Restore SetPresentUnits",
                                          detail="CSI reports different units after restoration")
                except CSIApiError as exc:
                    if not body_failed:
                        raise
                    log.warning("Could not restore units %s: %s", previous, exc)  # never mask the real error

    # ------------------------------ definitions ---------------------------- #

    def name_list(self, sap: Any, collection: str) -> list[str]:
        """GetNameList() for SapModel.<collection> (e.g. 'PropFrame', 'LoadCases')."""
        obj = getattr(sap, collection)
        outs = api_call(f"{collection}.GetNameList", obj.GetNameList)
        if len(outs) != 2 or type(outs[0]) is not int:
            raise CSIApiError(f"{collection}.GetNameList", detail="unexpected name-list layout")
        names = [str(n) for n in as_list(outs[1]) if n not in (None, "")]
        if len(names) != outs[0]:
            raise CSIApiError(f"{collection}.GetNameList", detail="name count differs from the returned array")
        return names

    def try_name_list(self, sap: Any, collections: Sequence[str]) -> tuple[list[str] | None, str | None]:
        """First collection that answers GetNameList, else (None, error)."""
        last_err = None
        for col in collections:
            try:
                return self.name_list(sap, col), None
            except (CSIApiError, AttributeError) as exc:
                last_err = f"{col}: {exc}"
        return None, last_err

    def count(self, sap: Any, collection: str) -> int | None:
        """Number of items; falls back to the length of GetNameList (SAFE's RespCombo has no Count())."""
        try:
            return int(api_value(f"{collection}.Count", getattr(sap, collection).Count))
        except (CSIApiError, AttributeError, TypeError, ValueError):
            names, _ = self.try_name_list(sap, [collection])
            return len(names) if names is not None else None

    # --------------------------------- lock -------------------------------- #

    def is_locked(self, sap: Any) -> bool | None:
        try:
            value = api_value("GetModelIsLocked", sap.GetModelIsLocked)
            return bool(value) if value in (False, True, -1) else None
        except (CSIApiError, AttributeError):
            return None

    def lock_guard(self, sap: Any, allow_unlock: bool, action: str) -> dict | None:
        """Return a warning payload if the model is locked and unlocking was not permitted.

        If allow_unlock is True the model is unlocked (which deletes analysis results)
        and None is returned. Never unlocks silently.
        """
        locked = self.is_locked(sap)
        if locked is None:
            raise CSIApiError("GetModelIsLocked", detail="cannot verify the lock state before editing")
        if not locked:
            return None
        if not allow_unlock:
            return {
                "ok": False,
                "status": "confirmation_required",
                "model_locked": True,
                "message": (
                    f"The model is locked (it has analysis results). To {action}, the model must be "
                    "unlocked, which permanently DELETES all current analysis results."
                ),
                "hint": "Ask the user to confirm, then send the same action again with allow_unlock: true.",
            }
        api_call("SetModelIsLocked", sap.SetModelIsLocked, False)
        if self.is_locked(sap) is not False:
            raise CSIApiError("SetModelIsLocked", detail="model is still locked after unlock request")
        return {"model_was_unlocked": True}

    # ----------------------------- database tables ------------------------- #

    def available_tables(self, sap: Any) -> list[dict[str, Any]]:
        """DatabaseTables.GetAvailableTables -> [{key, name, import_type}]."""
        outs = api_call("DatabaseTables.GetAvailableTables", sap.DatabaseTables.GetAvailableTables)
        # outs = [NumberTables, TableKey[], TableName[], ImportType[]]
        keys = as_list(outs[1]) if len(outs) > 1 else []
        names = as_list(outs[2]) if len(outs) > 2 else keys
        types = as_list(outs[3]) if len(outs) > 3 else [None] * len(keys)
        return [
            {"key": str(k), "name": str(n), "import_type": (int(t) if t is not None else None)}
            for k, n, t in zip(keys, names, types)
        ]

    def display_selection(self, sap: Any) -> tuple[list[str] | None, list[str] | None]:
        """Current (cases, combos) selected for table display; None when it cannot be read.

        An empty selection comes back as return code 1 with a count of 0 (SAFE 23.3): that is [], not an error.
        """
        out: list[list[str] | None] = []
        for meth in ("GetLoadCasesSelectedForDisplay", "GetLoadCombinationsSelectedForDisplay"):
            try:
                ret, outs = split_result(getattr(sap.DatabaseTables, meth)())
            except (COMError, OSError, AttributeError):
                out.append(None)
                continue
            if ret in (0, 1) and outs and _safe_int(outs[0]) == 0:
                out.append([])
            elif ret == 0 and outs:
                out.append([str(x) for x in as_list(outs[-1]) if x not in (None, "")])
            else:
                out.append(None)
        return out[0], out[1]

    def _set_selected(self, sap: Any, method: str, names: list[str]) -> None:
        func = getattr(sap.DatabaseTables, method)
        try:
            api_call(f"DatabaseTables.{method}", func, list(names))
        except (CSIApiError, TypeError):
            # Some builds expose the (count, list) signature.
            api_call(f"DatabaseTables.{method}", func, len(names), list(names))

    def set_display_selection(self, sap: Any, cases: list[str], combos: list[str]) -> list[str]:
        """Select cases/combos for result tables. Returns warnings (never raises for empty lists)."""
        warnings: list[str] = []
        for method, names in (
            ("SetLoadCasesSelectedForDisplay", cases),
            ("SetLoadCombinationsSelectedForDisplay", combos),
        ):
            try:
                self._set_selected(sap, method, names)
            except (CSIApiError, AttributeError) as exc:
                if names:
                    raise
                warnings.append(f"{method}([]) not accepted ({exc}); rows are filtered in Python instead.")
        return warnings

    def restore_display_selection(self, sap: Any, cases: list[str], combos: list[str]) -> list[str]:
        """Put the user's display selection back and verify it. Returns warnings.

        An originally EMPTY selection may not be re-settable (SAFE 23.3 refuses an empty list): that is reported as a
        warning, not an error, because the table itself was read correctly. A non-empty selection that cannot be put
        back is an error.
        """
        errors: list[str] = []
        for method, names in (("SetLoadCasesSelectedForDisplay", cases),
                              ("SetLoadCombinationsSelectedForDisplay", combos)):
            try:
                self._set_selected(sap, method, names)
            except (CSIApiError, TypeError, AttributeError) as exc:
                errors.append(f"{method}: {exc}")
        actual = self.display_selection(sap)
        warnings: list[str] = []
        for label, got, wanted in (("load cases", actual[0], cases), ("load combinations", actual[1], combos)):
            if got is not None and set(got) == set(wanted):
                continue
            if not wanted:
                warnings.append(f"The display selection of {label} was empty before this read and could not be "
                                "emptied again; it now shows the cases read.")
                continue
            raise CSIApiError("Restore display selection",
                              detail=f"CSI did not confirm the original {label} selection. " + " ".join(errors),
                              hint="Check Display > Show Tables > Select Load Cases in the program.")
        return warnings

    def read_table(self, sap: Any, software: str, table_key: str) -> tuple[list[str], list[list[str]], int | None]:
        """GetTableForDisplayArray -> (field_keys, rows, table_version).

        Tries the field-list/group-name variants that differ between products and
        versions. A non-zero code with column headers returned means an empty table.
        """
        spec = self.spec(software)
        func = sap.DatabaseTables.GetTableForDisplayArray
        attempts: list[str] = []
        for field_list in ([], [""]):
            for group in spec.group_all_candidates:
                try:
                    result = func(table_key, field_list, group, 0, [], 0, [])
                except (COMError, OSError, TypeError) as exc:
                    attempts.append(f"group='{group}', fields={field_list}: {exc}")
                    continue
                ret, outs = split_result(result)
                if len(outs) < 4:
                    attempts.append(f"group='{group}': unexpected result shape {result!r:.120}")
                    continue
                data = as_list(outs[-1])
                n_records = outs[-2]
                raw_fields = as_list(outs[-3])
                fields = [str(f) for f in raw_fields if f not in (None, "")]
                version = outs[-4] if len(outs) >= 4 else None
                # Column keys back = a readable table (a non-zero code with headers = an empty table).
                # Headers with no records = an empty table whatever the code (SAFE returns 1); records with a
                # non-zero code are not trusted.
                empty = _safe_int(n_records) == 0 and not data
                if type(ret) is int and (ret == 0 or empty) and fields and len(fields) == len(raw_fields):
                    if len(set(f.casefold() for f in fields)) != len(fields):
                        raise CSIApiError("GetTableForDisplayArray", detail="duplicate column keys")
                    return fields, _reshape(fields, data, n_records), _safe_int(version)
                attempts.append(f"group='{group}', fields={field_list}: return code {ret}, "
                                f"columns {raw_fields[:5]!r}, records {n_records!r}")
        raise CSIApiError(
            "DatabaseTables.GetTableForDisplayArray",
            detail=f"table '{table_key}' could not be read ({'; '.join(attempts)})",
            hint="Check the exact key with list_available_tables. Result tables exist only after the analysis has "
            "run and the selected cases / combinations have results; an unstable model (see the analysis log) "
            "can leave result tables unreadable.",
        )

    # -------------------------------- analysis ----------------------------- #

    def case_status(self, sap: Any) -> dict[str, str]:
        outs = api_call("Analyze.GetCaseStatus", sap.Analyze.GetCaseStatus)
        names = as_list(outs[1]) if len(outs) > 1 else []
        stats = as_list(outs[2]) if len(outs) > 2 else []
        if len(outs) != 3 or outs[0] != len(names) or len(names) != len(stats):
            raise CSIApiError("Analyze.GetCaseStatus", detail="inconsistent case counts")
        return {str(n): CASE_STATUS.get(s, str(s)) for n, s in zip(names, stats)}


MAX_TABLE_CELLS = 10_000_000


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _reshape(fields: list[str], flat: list[Any], n_records: Any) -> list[list[str]]:
    """CSI returns TableData flattened row-by-row; rebuild rows from the column count."""
    ncol = len(fields)
    if ncol == 0:
        return []
    n = n_records
    if type(n) is not int or n < 0 or n * ncol != len(flat):
        raise CSIApiError("GetTableForDisplayArray", detail="record count, field count and data length disagree")
    if len(flat) > MAX_TABLE_CELLS:
        raise CSIValidationError(f"Table has {len(flat):,} cells (limit {MAX_TABLE_CELLS:,}).",
                                 hint="Select fewer load cases / combinations, or a smaller object group.")
    return [["" if v is None else str(v) for v in flat[i * ncol:(i + 1) * ncol]] for i in range(n)]


# --------------------------------------------------------------------------- #
# Diagnostics:  python csi_bridge.py ETABS
# --------------------------------------------------------------------------- #

def _diagnose(software: str) -> int:
    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    executor, bridge = ComExecutor(), CSIBridge()

    def _job() -> dict:
        conn = bridge.connect(software)
        sap = conn.sap_model
        ver = api_call("GetVersion", sap.GetVersion)
        return {
            "software": conn.software,
            "attached_via": conn.attached_via,
            "version": ver[0] if ver else None,
            "model": bridge.model_filename(sap),
            "units": bridge.present_units(sap),
            "locked": bridge.is_locked(sap),
        }

    try:
        print(executor.run(_job))
        return 0
    except CSIError as exc:
        print(f"FAILED: {exc}\nHINT: {exc.hint}", file=sys.stderr)
        return 1
    finally:
        executor.shutdown(bridge.release_all)


if __name__ == "__main__":
    sys.exit(_diagnose(sys.argv[1] if len(sys.argv) > 1 else "ETABS"))
