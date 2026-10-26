"""Contract library: YAML-based contracts with device-specific implementations.

Each contract is defined in contracts/{name}.yaml with:
  - description, parameters, precondition, postcondition
  - implementations: per-device (or per-family with inherits:) step lists

Public API
----------
load(name)                         Load definition from YAML.
has_impl(defn, device_id)          True if device has a direct or inherited implementation.
steps_for(defn, device_id, params) Resolve implementation and substitute {param} placeholders.
placeholders_for(defn, device_id)  List placeholder names still in steps (need LLM resolution).
save_impl(name, device_id, keys, args)
                                   Write a discovered key sequence back into the YAML file.
                                   LINE keys are generalised to {linekey} when semantic args
                                   were given (so the same contract works for any contact).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CONTRACTS_DIR = Path("contracts")


# ── Loader ────────────────────────────────────────────────────────────────────


def load(name: str) -> dict:
    """Load contract definition from contracts/{name}.yaml."""
    path = CONTRACTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No contract definition for '{name}' at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ── Implementation resolver ───────────────────────────────────────────────────


def _find_impl(impls: dict, device_id: str) -> dict | None:
    """Return implementation for *device_id*, following a single inherits: chain.

    Returns a new dict (never mutates the source).  Returns None if the device
    has no entry and no applicable parent.
    """
    if device_id not in impls:
        return None
    raw = impls[device_id]
    if not isinstance(raw, dict):
        return None
    impl = dict(raw)
    parent_id = impl.pop("inherits", None)
    if parent_id:
        parent = _find_impl(impls, parent_id)
        if parent:
            merged = dict(parent)
            merged.update(impl)
            return merged
    return impl


def has_impl(defn: dict, device_id: str) -> bool:
    """Return True if *defn* contains a (direct or inherited) implementation for *device_id*."""
    return _find_impl(defn.get("implementations", {}), device_id) is not None


def steps_for(defn: dict, device_id: str, resolved_params: dict[str, str]) -> list[str]:
    """Resolve implementation for *device_id*, substitute ``{param}`` placeholders.

    Returns a list of physical key names ready to press.
    Raises ValueError if no implementation exists.
    """
    impl = _find_impl(defn.get("implementations", {}), device_id)
    if impl is None:
        name = defn.get("contract", "?")
        raise ValueError(
            f"Contract '{name}' has no implementation for device '{device_id}'"
        )
    keys: list[str] = []
    for step in impl.get("steps", []):
        key = step.get("keypress", "")
        for placeholder, value in resolved_params.items():
            key = key.replace(f"{{{placeholder}}}", value)
        keys.append(key)
    return keys


def placeholders_for(defn: dict, device_id: str) -> list[str]:
    """Return placeholder names still present in the resolved steps for *device_id*.

    A non-empty return means the LLM must resolve those parameters before replay.
    """
    impl = _find_impl(defn.get("implementations", {}), device_id)
    if impl is None:
        return []
    placeholders: list[str] = []
    for step in impl.get("steps", []):
        placeholders.extend(re.findall(r"\{(\w+)\}", step.get("keypress", "")))
    return placeholders


# ── Serialiser ────────────────────────────────────────────────────────────────


def save_impl(
    name: str,
    device_id: str,
    keys: list[str],
    args: list[str],
) -> None:
    """Persist a discovered key sequence as a device implementation.

    When semantic *args* were given (e.g. a contact name) and a LINE key was
    discovered, the key is stored as ``{linekey}`` so any future call can have
    its contact resolved by the LLM dynamically — the contract itself stays
    device-specific but contact-agnostic.

    If the YAML file already exists it is updated in-place; otherwise a minimal
    stub is created.
    """
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = CONTRACTS_DIR / f"{name}.yaml"

    if path.exists():
        defn = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        defn = {
            "contract": name,
            "description": f"Auto-generated contract '{name}'.",
            "parameters": [],
            "implementations": {},
        }

    def _generalise(key: str) -> str:
        """Replace a specific LINE key with {linekey} when args suggest parameterisation."""
        if args and re.match(r"LINE\d+$", key):
            return "{linekey}"
        return key

    defn.setdefault("implementations", {})[device_id] = {
        "steps": [{"keypress": _generalise(k)} for k in keys]
    }

    path.write_text(
        yaml.dump(defn, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"[contract] Saved '{name}' for '{device_id}': {keys}")
