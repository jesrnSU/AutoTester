"""Helpers for saving and loading device configurations to/from JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from auto_tester.devices.base import BaseDevice
from auto_tester.devices.polycom import PolycomDevice
from auto_tester.devices.yealink import YealinkDevice
from auto_tester.net.http import HttpClient

SAVED_DEVICES_DIR = Path("auto_tester/devices/saved_configs")


def _make_auth(auth_type: str, username: str, password: str):
    if auth_type == "digest":
        return HTTPDigestAuth(username, password)
    return HTTPBasicAuth(username, password)


def save_device(
    device_id: str,
    ip: str,
    username: str,
    password: str,
    vendor: str,
    auth_type: str = "digest",
    left_linekeys: int = 0,
    right_linekeys: int = 0,
    softkeys: int = 0,
    touchscreen: bool = False,
) -> None:
    SAVED_DEVICES_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "device_id": device_id,
        "ip": ip,
        "username": username,
        "password": password,
        "vendor": vendor,
        "auth_type": auth_type,
        "capabilities": {
            "left_linekeys": left_linekeys,
            "right_linekeys": right_linekeys,
            "softkeys": softkeys,
            "touchscreen": touchscreen,
        },
    }
    (SAVED_DEVICES_DIR / f"{device_id}.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


def load_device(path: Path) -> BaseDevice:
    config = json.loads(path.read_text(encoding="utf-8"))
    auth_type = config.get("auth_type", "digest")
    auth = _make_auth(auth_type, config["username"], config["password"])
    http = HttpClient(base_url=f"https://{config['ip']}", verify=False, auth=auth)
    cls = PolycomDevice if config["vendor"] == "Polycom" else YealinkDevice
    caps = config["capabilities"]
    return cls(
        device_id=config["device_id"],
        ip=config["ip"],
        username=config["username"],
        password=config["password"],
        http_client=http,
        touchEnabled=caps["touchscreen"],
        nr_left_linekeys=caps["left_linekeys"],
        nr_right_linekeys=caps["right_linekeys"],
        nr_softkeys=caps["softkeys"],
    )


def list_saved_devices() -> list[Path]:
    if not SAVED_DEVICES_DIR.exists():
        return []
    return sorted(SAVED_DEVICES_DIR.glob("*.json"))


def make_device(device_id: str) -> BaseDevice:
    """Load device from saved_configs by device_id."""
    config_path = SAVED_DEVICES_DIR / f"{device_id}.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"No saved config for '{device_id}' in {SAVED_DEVICES_DIR}"
        )
    return load_device(config_path)


def load_config(device_id: str) -> dict:
    """Load a device config from saved_configs/<device_id>.json."""
    return json.loads(
        (SAVED_DEVICES_DIR / f"{device_id}.json").read_text(encoding="utf-8")
    )


def save_config(config: dict) -> None:
    """Persist a device config dict back to its JSON file."""
    path = SAVED_DEVICES_DIR / f"{config['device_id']}.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def config_context(config: dict, alias: str) -> str:
    """Build a compact, human-readable context block for one device."""
    caps = config["capabilities"]
    total_linekeys = caps["left_linekeys"] + caps["right_linekeys"]
    parts = [f"{alias} ({config['vendor']} {config['device_id']})"]
    parts.append(
        f"  Hardware: {total_linekeys} linekeys "
        f"({caps['left_linekeys']} left + {caps['right_linekeys']} right), "
        f"{caps['softkeys']} softkeys, "
        f"touchscreen={caps['touchscreen']}"
    )
    usage = config.get("usage", {})
    if usage:
        line_keys = usage.get("line_keys", {})
        softkeys = usage.get("softkeys", {})
        if line_keys:
            if "layout" in line_keys:
                parts.append(f"  Line key layout: {line_keys['layout']}")
            for k, v in line_keys.items():
                if k != "layout":
                    parts.append(f"  {k}: {v}")
        if softkeys:
            if "layout" in softkeys:
                parts.append(f"  Softkey layout: {softkeys['layout']}")
            note = softkeys.get("note")
            if note:
                parts.append(f"  Softkeys note: {note}")
        navigation = usage.get("navigation", {})
        if navigation:
            if "layout" in navigation:
                parts.append(f"  Navigation: {navigation['layout']}")
    behaviors = config.get("behaviors", {})
    if behaviors:
        parts.append("  Learned behaviors (state-scoped):")
        for state, keys in behaviors.items():
            if isinstance(keys, dict):
                parts.append(f"    [{state}]")
                for key, desc in keys.items():
                    parts.append(f"      {key}: {desc}")
            else:
                # Legacy flat entry
                parts.append(f"    {state}: {keys}")
    states = config.get("states", {})
    if states:
        parts.append("  Known screen states:")
        for name, desc in states.items():
            parts.append(f"    {name}: {desc}")
    return "\n".join(parts)
