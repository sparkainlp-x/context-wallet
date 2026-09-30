#!/usr/bin/env python3
# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Offline, local-first Context Wallet prototype.

This module creates deliberately small JSON context packets. It does not encrypt,
authenticate, sign, or transmit them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # POSIX only; used to serialize concurrent consume runs on one ledger path.
    import fcntl
except ImportError:  # pragma: no cover - e.g. Windows
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = 1
MAX_LIFETIME_SECONDS = 7 * 24 * 60 * 60

# This is the complete allowlist. Adding a new context type requires an explicit
# code change; profile and packet inputs cannot introduce new field names.
FIELD_LIMITS: dict[str, int] = {
    "preferred_name": 80,
    "role": 120,
    "timezone": 64,
    "communication_style": 240,
    "current_goal": 240,
    "accessibility_preferences": 240,
}

_PROFILE_KEYS = {"schema_version", "profile_id", "fields"}
_PACKET_KEYS = {
    "schema_version",
    "packet_id",
    "recipient",
    "purpose",
    "created_at",
    "expires_at",
    "fields",
}
_LEDGER_KEYS = {"schema_version", "consumed_packet_ids"}
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
# ASCII digits only: in Python str patterns, \d also matches non-ASCII digits.
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class WalletError(ValueError):
    """Raised when a profile, packet, or ledger violates the local schema."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WalletError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise WalletError(f"invalid JSON constant: {value}")


def _read_json(path: str | Path) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
        return json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except WalletError:
        raise
    except RecursionError as exc:
        raise WalletError(f"cannot read JSON from {path}: nesting is too deep") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        # ValueError covers JSONDecodeError and, on Python 3.11+, integers that
        # exceed the interpreter's int-to-string digit limit.
        raise WalletError(f"cannot read JSON from {path}: {exc}") from exc


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown: " + ", ".join(sorted(map(str, extra))))
        raise WalletError(f"{label} has invalid keys ({'; '.join(details)})")


def _require_text(value: Any, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise WalletError(f"{label} must be a string")
    if not value.strip():
        raise WalletError(f"{label} must not be blank")
    if len(value) > max_length:
        raise WalletError(f"{label} must be at most {max_length} characters")
    # Category Cc covers C0 controls, DEL, and C1 controls (U+0080-U+009F).
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise WalletError(f"{label} must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WalletError(f"{label} must be valid UTF-8 text") from exc
    return value


def _validate_fields(fields: Any, label: str) -> dict[str, str]:
    if not isinstance(fields, dict):
        raise WalletError(f"{label} must be a JSON object")
    if not fields:
        raise WalletError(f"{label} must contain at least one field")
    checked: dict[str, str] = {}
    for name, value in fields.items():
        if not isinstance(name, str) or name not in FIELD_LIMITS:
            raise WalletError(f"unknown context field: {name}")
        checked[name] = _require_text(value, f"{label}.{name}", FIELD_LIMITS[name])
    return checked


def validate_profile(profile: Any) -> dict[str, Any]:
    """Validate and return a strict profile object.

    A profile may contain any non-empty subset of the allowlisted fields, but
    cannot contain extra top-level or field keys.
    """
    if not isinstance(profile, dict):
        raise WalletError("profile must be a JSON object")
    _require_exact_keys(profile, _PROFILE_KEYS, "profile")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != SCHEMA_VERSION:
        raise WalletError(f"profile schema_version must be {SCHEMA_VERSION}")
    profile_id = profile["profile_id"]
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise WalletError("profile_id must match [a-z0-9][a-z0-9_-]{0,79}")
    fields = _validate_fields(profile["fields"], "profile.fields")
    return {"schema_version": SCHEMA_VERSION, "profile_id": profile_id, "fields": fields}


def load_profile(path: str | Path) -> dict[str, Any]:
    """Load and validate a profile JSON file."""
    return validate_profile(_read_json(path))


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise WalletError(f"{label} must be a UTC timestamp like 2030-01-02T03:04:05Z")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise WalletError(f"{label} is not a valid timestamp") from exc


def _format_timestamp(value: datetime) -> str:
    # Explicit zero-padding: strftime("%Y") does not pad years below 1000 on
    # every platform.
    value = value.astimezone(timezone.utc)
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}Z"
    )


def _validate_packet_id(value: Any) -> str:
    if not isinstance(value, str):
        raise WalletError("packet_id must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WalletError("packet_id must be a canonical UUID") from exc
    if str(parsed) != value:
        raise WalletError("packet_id must be a lowercase canonical UUID")
    return value


def validate_packet(packet: Any) -> dict[str, Any]:
    """Validate packet shape, selected values, and its maximum lifetime."""
    if not isinstance(packet, dict):
        raise WalletError("packet must be a JSON object")
    _require_exact_keys(packet, _PACKET_KEYS, "packet")
    if type(packet["schema_version"]) is not int or packet["schema_version"] != SCHEMA_VERSION:
        raise WalletError(f"packet schema_version must be {SCHEMA_VERSION}")

    packet_id = _validate_packet_id(packet["packet_id"])
    recipient = _require_text(packet["recipient"], "recipient", 120)
    purpose = _require_text(packet["purpose"], "purpose", 400)
    created = _parse_timestamp(packet["created_at"], "created_at")
    expires = _parse_timestamp(packet["expires_at"], "expires_at")
    lifetime = (expires - created).total_seconds()
    if lifetime <= 0:
        raise WalletError("expires_at must be after created_at")
    if lifetime > MAX_LIFETIME_SECONDS:
        raise WalletError("packet lifetime must not exceed 7 days")
    fields = _validate_fields(packet["fields"], "packet.fields")

    return {
        "schema_version": SCHEMA_VERSION,
        "packet_id": packet_id,
        "recipient": recipient,
        "purpose": purpose,
        "created_at": _format_timestamp(created),
        "expires_at": _format_timestamp(expires),
        "fields": fields,
    }


def create_packet(
    profile: Mapping[str, Any],
    selected_fields: Iterable[str],
    recipient: str,
    purpose: str,
    lifetime_seconds: int = 3600,
    *,
    now: datetime | None = None,
    packet_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal packet from explicitly selected profile fields.

    ``now`` and ``packet_id`` are injectable for deterministic local tests and
    fixtures. The returned packet contains no profile ID or unselected values.
    """
    checked_profile = validate_profile(dict(profile) if isinstance(profile, Mapping) else profile)
    _require_text(recipient, "recipient", 120)
    _require_text(purpose, "purpose", 400)
    if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= MAX_LIFETIME_SECONDS:
        raise WalletError("lifetime_seconds must be an integer from 1 through 604800")
    if isinstance(selected_fields, (str, bytes)):
        raise WalletError("selected_fields must be a collection of field names, not a string")
    try:
        requested = list(selected_fields)
    except TypeError as exc:
        raise WalletError("selected_fields must be an iterable of field names") from exc
    if not requested:
        raise WalletError("select at least one context field")
    if any(not isinstance(name, str) for name in requested):
        raise WalletError("selected_fields must contain only field-name strings")
    if len(set(requested)) != len(requested):
        raise WalletError("selected_fields must not contain duplicates")

    selected: dict[str, str] = {}
    available = checked_profile["fields"]
    for name in requested:
        if not isinstance(name, str) or name not in FIELD_LIMITS:
            raise WalletError(f"unknown context field: {name}")
        if name not in available:
            raise WalletError(f"selected field is not present in profile: {name}")
        selected[name] = available[name]

    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise WalletError("now must be a timezone-aware datetime")
    try:
        created = now.astimezone(timezone.utc).replace(microsecond=0)
        expires = created + timedelta(seconds=lifetime_seconds)
    except OverflowError as exc:
        raise WalletError("packet times are outside the supported date range") from exc
    packet = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": packet_id or str(uuid.uuid4()),
        "recipient": recipient,
        "purpose": purpose,
        "created_at": _format_timestamp(created),
        "expires_at": _format_timestamp(expires),
        "fields": selected,
    }
    return validate_packet(packet)


def render_packet(packet: Any) -> str:
    """Return the canonical human-previewable JSON for a validated packet."""
    checked = validate_packet(packet)
    return json.dumps(checked, ensure_ascii=False, indent=2, separators=(",", ": "))


def _fsync_directory(directory: Path) -> None:
    """Best-effort flush of a directory entry change (not supported everywhere)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_text(path: str | Path, text: str, *, overwrite: bool = True) -> None:
    """Write ``text`` via a temporary file in the same directory.

    With ``overwrite=False`` the final step is a hard link, which fails if the
    destination already exists (including if it appeared after an earlier check),
    so an existing file is never replaced. If the file system does not support
    hard links, an exclusive-create write is used instead.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp_name, destination)
            temp_name = None
        else:
            try:
                os.link(temp_name, destination)
            except FileExistsError:
                raise WalletError(
                    f"destination already exists; choose a new output path: {destination}"
                ) from None
            except OSError:
                # No hard-link support: fall back to exclusive creation.
                try:
                    with open(destination, "x", encoding="utf-8", newline="\n") as out:
                        out.write(text)
                        out.flush()
                        os.fsync(out.fileno())
                except FileExistsError:
                    raise WalletError(
                        f"destination already exists; choose a new output path: {destination}"
                    ) from None
        _fsync_directory(destination.parent)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def write_packet(path: str | Path, packet: Any) -> str:
    """Export the exact canonical preview bytes (plus one final newline).

    Refuses to replace an existing file at ``path``.
    """
    text = render_packet(packet) + "\n"
    _atomic_write_text(path, text, overwrite=False)
    return text


def check_expiry(packet: Any, *, now: datetime | None = None) -> None:
    """Raise WalletError if a valid packet is expired at ``now``."""
    checked = validate_packet(packet)
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise WalletError("now must be a timezone-aware datetime")
    current = now.astimezone(timezone.utc)
    expires = _parse_timestamp(checked["expires_at"], "expires_at")
    if current >= expires:
        raise WalletError("packet has expired")


def _empty_ledger() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "consumed_packet_ids": []}


def _validate_ledger(ledger: Any) -> dict[str, Any]:
    if not isinstance(ledger, dict):
        raise WalletError("ledger must be a JSON object")
    _require_exact_keys(ledger, _LEDGER_KEYS, "ledger")
    if type(ledger["schema_version"]) is not int or ledger["schema_version"] != SCHEMA_VERSION:
        raise WalletError(f"ledger schema_version must be {SCHEMA_VERSION}")
    ids = ledger["consumed_packet_ids"]
    if not isinstance(ids, list):
        raise WalletError("ledger.consumed_packet_ids must be an array")
    checked_ids = [_validate_packet_id(item) for item in ids]
    if len(set(checked_ids)) != len(checked_ids):
        raise WalletError("ledger must not contain duplicate packet IDs")
    return {"schema_version": SCHEMA_VERSION, "consumed_packet_ids": checked_ids}


def load_ledger(path: str | Path) -> dict[str, Any]:
    """Load a local one-use ledger, treating a missing file as an empty ledger."""
    if not Path(path).exists():
        return _empty_ledger()
    return _validate_ledger(_read_json(path))


def consume_packet(packet: Any, ledger_path: str | Path, *, now: datetime | None = None) -> None:
    """Mark one packet consumed in a local JSON ledger, once per ledger file.

    On POSIX systems the read-check-write sequence holds an advisory lock on a
    sidecar ``<ledger>.lock`` file, so concurrent consume runs on the same
    ledger path do not lose updates. This is local workflow state only.
    """
    checked = validate_packet(packet)
    check_expiry(checked, now=now)
    ledger_file = Path(ledger_path)
    lock_handle = None
    if fcntl is not None:
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(ledger_file.with_name(ledger_file.name + ".lock"), "a")
    try:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        ledger = load_ledger(ledger_file)
        if checked["packet_id"] in ledger["consumed_packet_ids"]:
            raise WalletError("packet has already been consumed in this local ledger")
        ledger["consumed_packet_ids"].append(checked["packet_id"])
        _atomic_write_text(
            ledger_file,
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        )
    finally:
        if lock_handle is not None:
            lock_handle.close()  # closing releases the lock


def _add_packet_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="local profile JSON file")
    parser.add_argument("--fields", required=True, help="comma-separated allowlisted field names")
    parser.add_argument("--recipient", required=True, help="named recipient label")
    parser.add_argument("--purpose", required=True, help="specific purpose for this packet")
    parser.add_argument(
        "--ttl-seconds", type=int, default=3600,
        help="packet lifetime in seconds (1 to 604800; default: 3600)",
    )


def _selected_from_argument(value: str) -> list[str]:
    selected = [part.strip() for part in value.split(",")]
    if any(not part for part in selected):
        raise WalletError("--fields must be a comma-separated list without empty entries")
    return selected


def _make_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return create_packet(
        load_profile(args.profile),
        _selected_from_argument(args.fields),
        args.recipient,
        args.purpose,
        args.ttl_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify small, local-only context packets. No network is used."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="print the exact packet JSON; write nothing")
    _add_packet_arguments(preview)

    export = subparsers.add_parser(
        "export", help="preview a packet, ask for confirmation, then export those exact bytes"
    )
    _add_packet_arguments(export)
    export.add_argument("--out", required=True, help="new local packet file path")

    verify = subparsers.add_parser("verify", help="validate packet schema and check expiry")
    verify.add_argument("--packet", required=True, help="local packet JSON file")

    consume = subparsers.add_parser(
        "consume", help="mark a valid packet used once in a local ledger"
    )
    consume.add_argument("--packet", required=True, help="local packet JSON file")
    consume.add_argument("--ledger", required=True, help="local ledger JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preview":
            packet = _make_from_args(args)
            sys.stdout.write(render_packet(packet) + "\n")
            return 0
        if args.command == "export":
            destination = Path(args.out)
            if destination.exists():
                raise WalletError("destination already exists; choose a new output path")
            packet = _make_from_args(args)
            preview = render_packet(packet) + "\n"
            sys.stdout.write(preview)
            try:
                answer = input(f"Export this exact packet to {destination}? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer != "y":
                print("\nExport cancelled; nothing was written.", file=sys.stderr)
                return 1
            # Write the exact text that was just shown; never replace an existing file.
            _atomic_write_text(destination, preview, overwrite=False)
            print(f"Exported: {destination}")
            return 0
        if args.command == "verify":
            packet = validate_packet(_read_json(args.packet))
            check_expiry(packet)
            print(
                f"Valid and unexpired: {packet['packet_id']} for {packet['recipient']} "
                f"(expires {packet['expires_at']})."
            )
            return 0
        if args.command == "consume":
            packet = validate_packet(_read_json(args.packet))
            consume_packet(packet, args.ledger)
            print("Marked consumed in the specified local ledger only.")
            return 0
    except (WalletError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
