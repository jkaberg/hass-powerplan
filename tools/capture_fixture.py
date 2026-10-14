#!/usr/bin/env python3
"""Capture a device's entities and attributes from a live Home Assistant (D9 §3, §6).

    export POWERPLAN_HA_URL=http://homeassistant.local:8123
    export POWERPLAN_HA_TOKEN=<long-lived access token>
    uv run python tools/capture_fixture.py --device easee

Writes `tests/fixtures/captured/<name>.json`, which is the only place captured
dumps live. Read-only: the tool issues one `GET /api/states` and
nothing else, so it can be pointed at a running house safely.

The REST API exposes states, not the device registry, so `--device` is a
case-insensitive substring matched against each entity's `entity_id` and its
`friendly_name`. That is enough for an entity's attribute *shape* (a Heatit
thermostat's setpoint names, an Easee charger's status strings) - and it keeps
the tool to the standard library: no `aiohttp`, no websocket client, no
`homeassistant` import.

`--storage <config>/.storage` captures what the load flow sees instead (D-0750):
the device named `--device` in `core.device_registry`, and every entity the
entity registry gives it - disabled ones and helpers attached to it included -
with `platform`, `entity_category`, `disabled_by`, `translation_key` and
`original_name`. Not the `unique_id`: its format is the integration's own, and
it carries MAC addresses. States still come from `GET
/api/states`; a disabled entity has none and is written as `unavailable` with
what the registry remembers. The files are only read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURED = REPO_ROOT / "tests" / "fixtures" / "captured"

URL_ENV = "POWERPLAN_HA_URL"
TOKEN_ENV = "POWERPLAN_HA_TOKEN"

# Attribute names whose value never belongs in a committed fixture.
REDACT = frozenset({"access_token", "entity_picture", "latitude", "longitude"})
_SLUG = re.compile(r"[^a-z0-9]+")
#: A LAN address or a hardware address in a value: masked, never committed.
_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")


def slugify(value: str) -> str:
    """Return a file-name-safe form of `value`."""
    return _SLUG.sub("_", value.lower()).strip("_") or "capture"


def fetch_states(url: str, token: str, timeout: float) -> list[dict[str, Any]]:
    """Return every state object from `GET /api/states`."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload: list[dict[str, Any]] = json.load(response)
    return payload


def matches(state: dict[str, Any], needle: str) -> bool:
    """Return whether one state belongs to the device being captured."""
    lowered = needle.lower()
    attributes = state.get("attributes", {})
    friendly = str(attributes.get("friendly_name", ""))
    return lowered in state["entity_id"].lower() or lowered in friendly.lower()


def redact(attributes: dict[str, Any]) -> dict[str, Any]:
    """Replace the value of every attribute that must not be committed, and mask addresses."""
    return {
        key: "**REDACTED**"
        if key in REDACT
        else _ADDRESS.sub("**ADDRESS**", value)
        if isinstance(value, str)
        else value
        for key, value in attributes.items()
    }


def capture(states: list[dict[str, Any]], device: str) -> list[dict[str, Any]]:
    """Return the trimmed, redacted, sorted entity dumps for one device."""
    return sorted(
        (
            {
                "entity_id": state["entity_id"],
                "state": _ADDRESS.sub("**ADDRESS**", str(state["state"])),
                "attributes": redact(state.get("attributes", {})),
                "last_changed": state.get("last_changed"),
                "last_updated": state.get("last_updated"),
            }
            for state in states
            if matches(state, device)
        ),
        key=lambda entry: str(entry["entity_id"]),
    )


def _load_storage(storage: Path, name: str) -> dict[str, Any]:
    """Return a `.storage` document's `data`, read only."""
    document: dict[str, Any] = json.loads((storage / name).read_text("utf-8"))
    data: dict[str, Any] = document["data"]
    return data


def find_device(devices: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    """Return the one device whose name is `needle`, or whose name contains it."""
    lowered = needle.lower()

    def names(device: dict[str, Any]) -> list[str]:
        return [str(n).lower() for n in (device.get("name_by_user"), device.get("name")) if n]

    exact = [d for d in devices if lowered in names(d)]
    found = exact or [d for d in devices if any(lowered in n for n in names(d))]
    if len(found) != 1:
        listed = ", ".join(sorted(names(d)[0] for d in found if names(d))) or "none"
        raise LookupError(f"{len(found)} devices match {needle!r}: {listed}")
    return found[0]


def capture_device(
    states: list[dict[str, Any]], storage: Path, needle: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the device and its registry entities with their states (D-0750)."""
    device = find_device(_load_storage(storage, "core.device_registry")["devices"], needle)
    by_id = {state["entity_id"]: state for state in states}
    entities = []
    for entry in _load_storage(storage, "core.entity_registry")["entities"]:
        if entry.get("device_id") != device["id"]:
            continue
        state = by_id.get(entry["entity_id"])
        attributes = (
            dict(state.get("attributes", {}))
            if state is not None
            else {
                **(entry.get("capabilities") or {}),
                **({"device_class": dc} if (dc := entry.get("original_device_class")) else {}),
                **({"unit_of_measurement": u} if (u := entry.get("unit_of_measurement")) else {}),
                **({"friendly_name": n} if (n := entry.get("original_name")) else {}),
            }
        )
        entities.append(
            {
                "entity_id": entry["entity_id"],
                "state": "unavailable"
                if state is None
                else _ADDRESS.sub("**ADDRESS**", str(state["state"])),
                "attributes": redact(attributes),
                "last_changed": None if state is None else state.get("last_changed"),
                "last_updated": None if state is None else state.get("last_updated"),
                "platform": entry.get("platform"),
                "entity_category": entry.get("entity_category"),
                "disabled_by": entry.get("disabled_by"),
                "translation_key": entry.get("translation_key"),
                "original_name": entry.get("original_name"),
            }
        )
    return device, sorted(entities, key=lambda entity: str(entity["entity_id"]))


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser (flags per D9 §6)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url",
        default=os.environ.get(URL_ENV),
        help=f"Home Assistant base URL (default: ${URL_ENV})",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get(TOKEN_ENV),
        help=f"long-lived access token (default: ${TOKEN_ENV})",
    )
    parser.add_argument(
        "--device",
        required=True,
        help="substring of the entity_id or friendly name to capture",
    )
    parser.add_argument("--name", help="fixture name (default: a slug of --device)")
    parser.add_argument(
        "--out",
        type=Path,
        help=f"output file (default: {CAPTURED.relative_to(REPO_ROOT)}/<name>.json)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout, s")
    parser.add_argument(
        "--storage",
        type=Path,
        help="Home Assistant's .storage directory: capture the device from the registries",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Capture one device into a fixture file."""
    args = build_parser().parse_args(argv)

    if not args.url or not args.token:
        print(
            f"error: set --url/--token or ${URL_ENV}/${TOKEN_ENV}",
            file=sys.stderr,
        )
        return 2

    name = args.name or slugify(args.device)
    out: Path = args.out or CAPTURED / f"{name}.json"

    try:
        states = fetch_states(args.url, args.token, args.timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        print(f"error: cannot read {args.url}: {err}", file=sys.stderr)
        return 1

    device: dict[str, Any] = {}
    if args.storage is not None:
        try:
            device, entities = capture_device(states, args.storage, args.device)
        except (LookupError, OSError, KeyError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
    else:
        entities = capture(states, args.device)
    if not entities:
        print(f"error: no entity matches {args.device!r}", file=sys.stderr)
        return 1

    document: dict[str, Any] = {
        "name": name,
        "device": args.device,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "entity_count": len(entities),
        "entities": entities,
    }
    if device:
        document |= {
            "device_id": device["id"],
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "registry": True,
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")

    print(f"{len(entities)} entities → {out}")
    for entity in entities:
        print(f"  {entity['entity_id']} = {entity['state']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
