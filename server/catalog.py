"""The wording Claude sees: tool descriptions and `help`, rendered from actions.ACTIONS.

Each action is defined once in actions.py (summary + Pydantic parameter model), so the text here cannot drift from
the code. docs/ACTIONS.md is generated from this file (tools/gen_docs.py).
"""
from __future__ import annotations

import re
from typing import Any

import edition
import programs
from actions import ACTIONS, Action

SERVER_VERSION = "0.22.2"

UNITS_NOTE = (
    "UNITS: every action that reads or writes numbers takes params.units (default kN_m_C = kN, m, C; also Ton_m_C, "
    "N_mm_C, kN_mm_C, kip_ft_F ... 16 CSI presets). The action sets those units, works, then puts the user's display "
    "units back. Always state the units in answers."
)
NO_DESIGN_NOTE = (
    "DESIGN: Claude never chooses design codes, combinations, preferences, overwrites, sections, loads or limits; "
    "they come from the designer or the model. Design runs the program's own engine with the code set in the model "
    "or named by the designer (get_design_settings first, and state the code, combinations and units in answers). "
    "If something is missing, ask the user."
)
LOCK_NOTE = (
    "LOCKED MODELS: a model with analysis results is locked. Changing it needs allow_unlock: true, which DELETES the "
    "results. Never send allow_unlock: true unless the user agreed in this conversation; a locked model answers "
    "'confirmation_required' instead."
)
def software_note() -> str:
    one = edition.current()
    if one:
        return (f"This server works with {one} only (params.software is filled in; ETABS, SAP2000 and SAFE each have "
                f"their own server). Reads attach to the running {one} by themselves. "
                + programs.MODULES[one].USAGE)
    on = programs.enabled()
    limited = "" if on == programs.ALL else f" (this server is limited to {', '.join(on)})"
    return (f"Every action except help and list_programs takes params.software: {' | '.join(on)}{limited}. Reads "
            "attach to the running program by themselves. Actions marked [ETABS, SAFE] etc. exist only in those "
            "programs' API.")



SAFETY_NOTE = (
    "SAFETY AND OUTPUT: every edit action takes dry_run: true (checks everything, reports what it would do, changes "
    "nothing) - use it to show the user the plan before a change they have not seen. Modelling edits and run_analysis "
    "take snapshot: true (copy of the model file as last saved, first). Every edit that runs is logged in "
    "Documents\\CsiMcp\\change_log.jsonl. Big tables go to a CSV in Documents\\CsiMcp\\exports (export_path); "
    "reports go to Documents\\CsiMcp\\reports unless output_path is given."
)

GROUP_ORDER = ("Session", "Model", "Model reading", "Definitions", "Objects", "Modelling", "Geometry", "Bulk editing",
               "Analysis", "Results", "Design", "Output")


def _required(model: type, field: str) -> bool:
    return model.model_fields[field].is_required()


_MARK = re.compile(r"\[\[([ESF]+): ?(.*?)\]\]", re.S)
LETTER = {v: k for k, v in programs.LETTER.items()}    # "ETABS" -> "E"


def render(text: str) -> str:
    """[[EF: text]] -> text in the ETABS and SAFE editions, nothing in SAP2000; the development server (all
    programs) keeps it, followed by the programs in brackets."""
    one = edition.current()

    def sub(m: re.Match) -> str:
        src = m.string
        before = src[m.start() - 1] if m.start() else " "
        after = src[m.end()] if m.end() < len(src) else " "
        if one:
            kept = m.group(2) if LETTER[one] in m.group(1) else ""
        else:
            kept = f"{m.group(2)} [{', '.join(programs.expand(m.group(1)))}]"
        if not kept:     # a dropped piece between two words leaves one space ("blank[[EF: or grid-only]] model")
            return " " if (before.isalnum() or before in ".)") and after.isalnum() else ""
        if (before.isalnum() or before in ".)") and kept[0].isalnum():
            kept = " " + kept    # "read back.[[E:ETABS takes ...]]" -> "read back. ETABS takes ..."
        if after.isalnum() and (kept[-1].isalnum() or kept[-1] in ".)"):
            kept += " "
        return kept
    return re.sub(r"  +", " ", _MARK.sub(sub, text or ""))


def _shown(letters: str) -> bool:
    """Is something marked for these programs shown by this server (any enabled program has it)?"""
    return any(p in programs.expand(letters) for p in programs.enabled())


def _fields(a: Action) -> list[str]:
    """Parameters shown to Claude: an edition hides software (it fills it in) and other programs' parameters."""
    hide = {"software"} if edition.current() else set()
    hide |= {f for f, letters in a.fields.items() if not _shown(letters)}
    return [f for f in a.params.model_fields if f not in hide]


def check_program_options(a: Action, validated: Any, software: str) -> None:
    """Refuse a parameter or an option value that the program's API does not have."""
    from csi_bridge import CSIValidationError
    letter = LETTER[software]
    alt = programs.MODULES[software].ALTERNATIVES
    for f, letters in a.fields.items():
        if letter not in letters and f in validated.model_fields_set and getattr(validated, f) is not None:
            raise CSIValidationError(f"{a.name}: {f} does not apply in {software}.",
                                     hint=alt.get(f) or f"Leave it out (used in {', '.join(programs.expand(letters))}).")
    for f, values in a.options.items():
        got = getattr(validated, f, None)
        for v in (got if isinstance(got, (list, tuple)) else [got]):
            if v in values and letter not in values[v]:
                raise CSIValidationError(f"{a.name}: {f} '{v}' is not available in {software}.",
                                         hint=alt.get(v) or f"Available in {', '.join(programs.expand(values[v]))}.")


def available(a: Action) -> tuple[str, ...]:
    """Programs (of those enabled) whose API has this action."""
    return tuple(p for p in programs.expand(a.programs) if p in programs.enabled())


def signature(a: Action) -> str:
    names = [f if _required(a.params, f) else f + "?" for f in _fields(a)]
    progs = available(a)
    tag = f" [{', '.join(progs)}]" if progs != programs.enabled() else ""
    summary = render(a.summary)
    return f"- {a.name}({', '.join(names)}){tag}: {summary}" if names else f"- {a.name}{tag}: {summary}"


def param_help(a: Action) -> dict[str, Any]:
    schema = a.params.model_json_schema()
    out: dict[str, Any] = {}
    for name in _fields(a):
        field = a.params.model_fields[name]
        entry: dict[str, Any] = {"required": field.is_required(), "description": render(field.description or "")}
        if not field.is_required():     # a default_factory field has no plain default (PydanticUndefined)
            entry["default"] = field.get_default(call_default_factory=True)
        prop = schema.get("properties", {}).get(name, {})
        enum = prop.get("enum") or next((x.get("enum") for x in prop.get("anyOf", []) if "enum" in x), None)
        if not enum:
            arr = prop if prop.get("type") == "array" else next(
                (x for x in prop.get("anyOf", []) if x.get("type") == "array"), {})
            items = arr.get("items", {})
            enum = items.get("enum") or next((x.get("enum") for x in items.get("anyOf", []) if "enum" in x), None)
        if enum:
            only = a.options.get(name, {})
            entry["one_of"] = [v for v in enum if v not in only or _shown(only[v])]
        out[name] = entry
    return out


def _actions(kind: str | None) -> list[Action]:
    acts = [a for a in ACTIONS.values() if kind in (None, a.kind) and available(a)]
    return sorted(acts, key=lambda a: (GROUP_ORDER.index(a.group) if a.group in GROUP_ORDER else 99))


def _listing(kind: str | None) -> list[str]:
    out: list[str] = []
    group = None
    for a in _actions(kind):
        if a.group != group:
            group = a.group
            out.append(f"[{group}]")
        out.append(signature(a))
    return out


def tool_description(mode: str = "all") -> str:
    """Text Claude sees for the MCP tool. mode: 'all', 'read' or 'edit'."""
    one = edition.current()
    what = f"CSI {one}" if one else "CSI ETABS, SAP2000 and SAFE"
    out = [f"Work with {what} running on this computer (CSI Open API). Call with action=<name> and "
           "params={...}. Parameters ending in ? are optional. action='help' (params.topic = an action) returns every "
           "parameter's description, default and allowed values.",
           software_note(), UNITS_NOTE, NO_DESIGN_NOTE]
    if mode in ("all", "edit"):
        out.append(LOCK_NOTE)
    out.append(SAFETY_NOTE)
    if mode in ("all", "read"):
        out.append("\nREAD ACTIONS (never change the model; Output actions write files only)")
        out.append("- help(topic?): parameter details of an action; without topic the list of actions.")
        out += _listing("read")
    if mode in ("all", "edit"):
        out.append("\nEDIT ACTIONS (change the model, its files or the program)")
        out += _listing("edit")
    out.append("\nTables: find exact keys with list_available_tables before query_database_table; keep max_rows small "
               "and prefer get_analysis_summary for headline results.")
    return "\n".join(out)


def help_index() -> dict[str, Any]:
    """`help` without a topic: the actions and what each does. The parameter details of all of them together are
    about 200 000 characters, so they are given per action (topic) or only when asked for (topic 'all')."""
    return {"version": SERVER_VERSION, "server": edition.server_name(), "programs": list(programs.enabled()),
            "readActions": {a.name: render(a.summary) for a in _actions("read")},
            "editActions": {a.name: render(a.summary) for a in _actions("edit")},
            "hint": "params.topic = an action name returns its parameters (description, default, allowed values). "
                    "topic 'all' returns every action's parameters at once (very large; avoid it)."}


def help_data(topic: str | None = None) -> dict[str, Any]:
    if topic is None:
        return {"version": SERVER_VERSION, "server": edition.server_name(), "programs": list(programs.enabled()),
                "usage": software_note(), "units": UNITS_NOTE, "noDesign": NO_DESIGN_NOTE, "safety": SAFETY_NOTE, "locked": LOCK_NOTE,
                "readActions": {a.name: {"summary": render(a.summary), "params": param_help(a)} for a in _actions("read")},
                "editActions": {a.name: {"summary": render(a.summary), "params": param_help(a)} for a in _actions("edit")}}
    a = ACTIONS.get(topic)
    if a is None or not available(a):
        raise ValueError(f"Unknown help topic '{topic}'. Topics: {', '.join(x.name for x in _actions(None))}.")
    return {topic: {"summary": render(a.summary), "kind": a.kind, "group": a.group, "programs": list(available(a)),
                    "params": param_help(a)}}
