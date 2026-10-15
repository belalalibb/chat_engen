"""Re-derive upstream contract shapes from the INSTALLED upstream package.

Faithful re-implementation of the introspection half of upstream's
``engineering/verification/contract_freeze_derive.py`` (R189). Kept here, not
imported: that script is outside the installed packages and D0 forbids
``apps.*``. The served-route half is NOT re-derived (needs ``apps.api``); the
committed route list is data checked by docs/COMPATIBILITY_MAP.md.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import json
from typing import Any, get_type_hints

#: Frozen modules that live in the upstream app layer — not importable under D0.
APP_LAYER_MODULES: frozenset[str] = frozenset({"apps.api.provider_onboarding"})

FROZEN_CONSTANTS: dict[str, tuple[str, ...]] = {
    "core.learning.gates": ("TRAINING_ELIGIBILITY_CONDITIONS", "PROMOTION_CONDITIONS"),
    "core.routing.capacity": ("DEFAULT_COOLDOWN_MS", "DEFAULT_UNAVAILABLE_MS", "RPM_WINDOW_SECONDS"),
    "core.contracts.execution_strategy": ("MAX_STAGES", "MAX_PARALLEL"),
    "core.contracts.evaluation": ("VERIFICATION_LEVEL_ORDER",),
}


def _annotation(obj: Any) -> str:
    if isinstance(obj, type):
        return obj.__qualname__
    text = obj if isinstance(obj, str) else repr(obj)
    return text.replace("typing.", "")


def _jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    try:
        return json.loads(json.dumps(value))
    except TypeError:
        return repr(value)


def _pydantic_model(cls: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, info in cls.model_fields.items():
        if info.is_required():
            default: Any = "<required>"
        elif info.default_factory is not None:
            default = f"<factory:{getattr(info.default_factory, '__name__', 'factory')}>"
        else:
            default = _jsonable(info.default)
        fields[name] = {
            "annotation": _annotation(info.annotation),
            "required": info.is_required(),
            "default": default,
            "alias": info.alias,
        }
    config = getattr(cls, "model_config", {}) or {}
    return {
        "kind": "pydantic_model",
        "fields": fields,
        "extra": config.get("extra"),
        "frozen": config.get("frozen"),
    }


def _enum(cls: type[enum.Enum]) -> dict[str, Any]:
    return {"kind": "enum", "members": {m.name: m.value for m in cls}}


def _dataclass(cls: type) -> dict[str, Any]:
    hints = get_type_hints(cls)
    fields: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            default: Any = _jsonable(f.default)
        elif f.default_factory is not dataclasses.MISSING:
            default = "<factory>"
        else:
            default = "<required>"
        fields[f.name] = {"annotation": _annotation(hints.get(f.name, f.type)), "default": default}
    params = getattr(cls, "__dataclass_params__", None)
    return {"kind": "dataclass", "fields": fields, "frozen": bool(params and params.frozen)}


def _module_unions(mod: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in vars(mod).items():
        if name.startswith("_") or isinstance(value, type):
            continue
        text = repr(value)
        if "Annotated[" in text and "discriminator" in text:
            out[name] = {"kind": "discriminated_union", "annotation": _annotation(value)}
    return out


def module_contracts(module_name: str) -> dict[str, Any]:
    """Introspect one frozen module exactly like upstream's derive script."""
    mod = importlib.import_module(module_name)
    entries: dict[str, Any] = {}
    for name, obj in sorted(vars(mod).items()):
        if not isinstance(obj, type) or obj.__module__ != module_name or name.startswith("_"):
            continue
        if issubclass(obj, enum.Enum):
            entries[name] = _enum(obj)
        elif hasattr(obj, "model_fields"):
            entries[name] = _pydantic_model(obj)
        elif dataclasses.is_dataclass(obj):
            entries[name] = _dataclass(obj)
        elif issubclass(obj, Exception):
            entries[name] = {"kind": "exception", "bases": [b.__name__ for b in obj.__bases__]}
    entries.update(_module_unions(mod))
    constants: dict[str, Any] = {}
    for const in FROZEN_CONSTANTS.get(module_name, ()):
        value = getattr(mod, const)
        if isinstance(value, tuple | list | frozenset | set):
            value = [_jsonable(v) for v in value]
        constants[const] = value
    return {"contracts": entries, "constants": constants}


def derive_core_modules(frozen_modules: list[str]) -> dict[str, Any]:
    """Derive every frozen module importable under D0 (core.* only)."""
    return {n: module_contracts(n) for n in frozen_modules if n not in APP_LAYER_MODULES}
