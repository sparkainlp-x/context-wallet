# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
import ast
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import context_wallet  # noqa: E402
from context_wallet import (  # noqa: E402
    WalletError,
    check_expiry,
    consume_packet,
    create_packet,
    load_ledger,
    load_profile,
    main,
    render_packet,
    validate_packet,
    validate_profile,
    write_packet,
)


NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
PACKET_ID = "11111111-1111-4111-8111-111111111111"
PROFILE = {
    "schema_version": 1,
    "profile_id": "test-fictional-profile",
    "fields": {
        "preferred_name": "Riley Example",
        "role": "Fictional planner",
        "timezone": "UTC",
        "communication_style": "Concise and friendly.",
        "current_goal": "Plan a fictional event.",
    },
}


def make_packet(fields=("preferred_name", "timezone"), lifetime_seconds=3600):
    return create_packet(
        PROFILE,
        fields,
        "Northstar Demo Coach",
        "Prepare one fictional planning note",
        lifetime_seconds,
        now=NOW,
        packet_id=PACKET_ID,
    )


class ContextWalletTests(unittest.TestCase):
    def test_packet_contains_only_selected_allowlisted_values(self):
        packet = make_packet(("preferred_name", "timezone"))
        self.assertEqual(packet["fields"], {
            "preferred_name": "Riley Example",
            "timezone": "UTC",
        })
        self.assertNotIn("role", packet["fields"])
        self.assertNotIn("profile_id", packet)
        self.assertEqual(
            set(packet),
            {"schema_version", "packet_id", "recipient", "purpose", "created_at", "expires_at", "fields"},
        )

    def test_preview_and_export_have_exact_same_json_content(self):
        packet = make_packet()
        expected = '''{
  "schema_version": 1,
  "packet_id": "11111111-1111-4111-8111-111111111111",
  "recipient": "Northstar Demo Coach",
  "purpose": "Prepare one fictional planning note",
  "created_at": "2030-01-01T12:00:00Z",
  "expires_at": "2030-01-01T13:00:00Z",
  "fields": {
    "preferred_name": "Riley Example",
    "timezone": "UTC"
  }
}'''
        preview = render_packet(packet)
        self.assertEqual(preview, expected)
        with tempfile.TemporaryDirectory() as directory:
            exported = Path(directory) / "packet.json"
            returned = write_packet(exported, packet)
            self.assertEqual(returned, expected + "\n")
            self.assertEqual(exported.read_text(encoding="utf-8"), expected + "\n")
            self.assertEqual(json.loads(exported.read_text(encoding="utf-8")), packet)

    def test_confirmed_cli_export_saves_the_previewed_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            output_path = Path(directory) / "packet.json"
            profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            output = StringIO()
            arguments = [
                "export",
                "--profile", str(profile_path),
                "--fields", "preferred_name,timezone",
                "--recipient", "Northstar Demo Coach",
                "--purpose", "Prepare one fictional planning note",
                "--ttl-seconds", "3600",
                "--out", str(output_path),
            ]
            with patch("builtins.input", return_value="y"), redirect_stdout(output):
                result = main(arguments)
            self.assertEqual(result, 0)
            emitted = output.getvalue()
            displayed_preview = emitted.rsplit("Exported: ", 1)[0]
            self.assertEqual(output_path.read_text(encoding="utf-8"), displayed_preview)

    def test_unknown_or_unavailable_fields_are_rejected(self):
        with self.assertRaisesRegex(WalletError, "unknown context field"):
            make_packet(("email",))
        with self.assertRaisesRegex(WalletError, "not present in profile"):
            make_packet(("accessibility_preferences",))
        bad_profile = {**PROFILE, "fields": {**PROFILE["fields"], "email": "fictional@example.invalid"}}
        with self.assertRaisesRegex(WalletError, "unknown context field"):
            validate_profile(bad_profile)

    def test_strict_schema_rejects_extra_keys_and_duplicate_json_keys(self):
        packet = make_packet()
        with self.assertRaisesRegex(WalletError, "unknown: unexpected"):
            validate_packet({**packet, "unexpected": "value"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(WalletError, "duplicate JSON key"):
                load_profile(path)

    def test_expiry_is_valid_before_deadline_and_rejected_at_deadline(self):
        packet = make_packet()
        check_expiry(packet, now=NOW + timedelta(seconds=3599))
        with self.assertRaisesRegex(WalletError, "expired"):
            check_expiry(packet, now=NOW + timedelta(seconds=3600))

    def test_lifetime_must_be_positive_and_at_most_seven_days(self):
        with self.assertRaisesRegex(WalletError, "lifetime_seconds"):
            make_packet(lifetime_seconds=0)
        with self.assertRaisesRegex(WalletError, "lifetime_seconds"):
            make_packet(lifetime_seconds=7 * 24 * 60 * 60 + 1)

    def test_local_ledger_allows_one_consume_per_packet_id(self):
        packet = make_packet()
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            consume_packet(packet, ledger_path, now=NOW + timedelta(seconds=1))
            self.assertEqual(load_ledger(ledger_path)["consumed_packet_ids"], [PACKET_ID])
            with self.assertRaisesRegex(WalletError, "already been consumed"):
                consume_packet(packet, ledger_path, now=NOW + timedelta(seconds=2))

    def test_expired_packet_does_not_change_local_ledger(self):
        packet = make_packet()
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            with self.assertRaisesRegex(WalletError, "expired"):
                consume_packet(packet, ledger_path, now=NOW + timedelta(seconds=3600))
            self.assertFalse(ledger_path.exists())


    # --- Added review tests -------------------------------------------------

    def _export_args(self, directory, output_path):
        profile_path = Path(directory) / "profile.json"
        profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
        return [
            "export",
            "--profile", str(profile_path),
            "--fields", "preferred_name,timezone",
            "--recipient", "Northstar Demo Coach",
            "--purpose", "Prepare one fictional planning note",
            "--out", str(output_path),
        ]

    def _run(self, arguments, answer=None, side_effect=None):
        out, err = StringIO(), StringIO()
        kwargs = {"side_effect": side_effect} if side_effect else {"return_value": answer}
        with patch("builtins.input", **kwargs), redirect_stdout(out), redirect_stderr(err):
            code = main(arguments)
        return code, out.getvalue(), err.getvalue()

    def test_declined_or_interrupted_export_writes_nothing_and_exits_nonzero(self):
        for kwargs in ({"answer": "n"}, {"answer": ""}, {"answer": "yes please"},
                       {"side_effect": EOFError}, {"side_effect": KeyboardInterrupt}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                with tempfile.TemporaryDirectory() as directory:
                    output_path = Path(directory) / "packet.json"
                    code, _, err = self._run(self._export_args(directory, output_path), **kwargs)
                    self.assertEqual(code, 1)
                    self.assertIn("cancelled", err)
                    self.assertFalse(output_path.exists())
                    self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["profile.json"])

    def test_export_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "packet.json"
            output_path.write_text("keep me", encoding="utf-8")
            code, out, err = self._run(self._export_args(directory, output_path), answer="y")
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("already exists", err)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "keep me")

    def test_export_does_not_clobber_file_created_while_waiting_for_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "packet.json"

            def confirm_after_race(prompt):
                output_path.write_text("created meanwhile", encoding="utf-8")
                return "y"

            code, _, err = self._run(self._export_args(directory, output_path),
                                     side_effect=confirm_after_race)
            self.assertEqual(code, 2)
            self.assertIn("already exists", err)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "created meanwhile")
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                             ["packet.json", "profile.json"])

    def test_write_packet_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packet.json"
            write_packet(path, make_packet())
            with self.assertRaisesRegex(WalletError, "already exists"):
                write_packet(path, make_packet(("preferred_name",)))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), make_packet())

    def test_failed_atomic_write_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            with patch("context_wallet.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    context_wallet._atomic_write_text(path, "{}\n")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_export_falls_back_to_exclusive_create_without_hard_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packet.json"
            with patch("context_wallet.os.link", side_effect=PermissionError("no links")):
                text = write_packet(path, make_packet())
                self.assertEqual(path.read_text(encoding="utf-8"), text)
                with self.assertRaisesRegex(WalletError, "already exists"):
                    write_packet(path, make_packet())
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["packet.json"])

    def test_control_characters_including_c1_are_rejected(self):
        for bad in ("a\nb", "a\tb", "a\x7fb", "a\x85b", "a\x9bb"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaisesRegex(WalletError, "control characters"):
                    validate_packet({**make_packet(), "recipient": bad})
        validate_packet({**make_packet(), "recipient": "Café Nord \u2014 démo"})

    def test_invisible_format_characters_are_rejected(self):
        bad_values = {
            "right-to-left override": "Coach\u202eevil",
            "left-to-right isolate": "a\u2066b",
            "zero-width space": "Coa\u200bch",
            "zero-width joiner": "a\u200db",
            "byte-order mark": "\ufeffCoach",
            "soft hyphen": "Co\u00adach",
            "line separator": "a\u2028b",
            "paragraph separator": "a\u2029b",
        }
        for name, bad in bad_values.items():
            with self.subTest(name):
                with self.assertRaisesRegex(WalletError, "invisible formatting"):
                    validate_packet({**make_packet(), "recipient": bad})
                with self.assertRaisesRegex(WalletError, "invisible formatting"):
                    validate_profile({**PROFILE, "fields": {"preferred_name": bad}})
                with self.assertRaisesRegex(WalletError, "invisible formatting"):
                    create_packet(PROFILE, ["timezone"], "R", bad, 60, now=NOW)
        # Visible non-ASCII text, including emoji without joiners, is still accepted.
        validate_packet({**make_packet(), "purpose": "Plan \u00e9t\u00e9 \U0001F331 \u05e9\u05dc\u05d5\u05dd"})

    def test_cli_preview_rejects_bidi_override_in_purpose(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text(json.dumps(PROFILE), encoding="utf-8")
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["preview", "--profile", str(profile), "--fields", "timezone",
                             "--recipient", "R", "--purpose", "a\u202eb"])
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertIn("invisible formatting", err.getvalue())

    def test_blank_overlong_and_lone_surrogate_values_are_rejected(self):
        with self.assertRaisesRegex(WalletError, "blank"):
            validate_packet({**make_packet(), "purpose": "   "})
        with self.assertRaisesRegex(WalletError, "at most 80"):
            validate_profile({**PROFILE, "fields": {"preferred_name": "x" * 81}})
        validate_profile({**PROFILE, "fields": {"preferred_name": "x" * 80}})
        with self.assertRaisesRegex(WalletError, "UTF-8"):
            validate_packet({**make_packet(), "recipient": "a\ud800b"})

    def test_timestamps_require_ascii_digits_and_real_dates(self):
        packet = make_packet()
        for bad in ("\u0662\u0660\u0663\u0660-01-01T12:00:00Z", "2030-01-01T12:00:00+00:00",
                    "2030-02-30T12:00:00Z", "2030-01-01T12:00:60Z", "2030-01-01 12:00:00Z",
                    "2030-01-01T12:00:00.5Z", "2030-01-01T12:00:00z", 20300101):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(WalletError):
                    validate_packet({**packet, "created_at": bad})

    def test_packet_lifetime_rules_are_enforced_on_validation(self):
        packet = make_packet()
        with self.assertRaisesRegex(WalletError, "after created_at"):
            validate_packet({**packet, "expires_at": packet["created_at"]})
        with self.assertRaisesRegex(WalletError, "7 days"):
            validate_packet({**packet, "expires_at": "2030-01-08T12:00:01Z"})
        validate_packet({**packet, "expires_at": "2030-01-08T12:00:00Z"})
        make_packet(lifetime_seconds=7 * 24 * 60 * 60)
        make_packet(lifetime_seconds=1)
        for bad in (True, 3600.0, "3600", None):
            with self.subTest(bad=repr(bad)):
                with self.assertRaisesRegex(WalletError, "lifetime_seconds"):
                    make_packet(lifetime_seconds=bad)

    def test_now_must_be_aware_and_is_normalized_to_utc(self):
        with self.assertRaisesRegex(WalletError, "timezone-aware"):
            create_packet(PROFILE, ["timezone"], "R", "P", 60, now=datetime(2030, 1, 1))
        toronto_like = timezone(timedelta(hours=-4))
        packet = create_packet(PROFILE, ["timezone"], "R", "P", 60,
                               now=datetime(2030, 1, 1, 8, 0, 0, 999999, tzinfo=toronto_like))
        self.assertEqual(packet["created_at"], "2030-01-01T12:00:00Z")
        self.assertEqual(packet["expires_at"], "2030-01-01T12:01:00Z")
        check_expiry(packet, now=datetime(2030, 1, 1, 8, 0, 59, tzinfo=toronto_like))
        with self.assertRaisesRegex(WalletError, "expired"):
            check_expiry(packet, now=datetime(2030, 1, 1, 8, 1, 0, tzinfo=toronto_like))
        with self.assertRaisesRegex(WalletError, "timezone-aware"):
            check_expiry(packet, now=datetime(2030, 1, 1))

    def test_out_of_range_dates_raise_wallet_error(self):
        early = datetime(999, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(context_wallet._format_timestamp(early), "0999-01-01T00:00:00Z")
        with self.assertRaisesRegex(WalletError, "date range"):
            create_packet(PROFILE, ["timezone"], "R", "P", 604800,
                          now=datetime(9999, 12, 31, tzinfo=timezone.utc))

    def test_explicit_packet_id_is_validated_not_silently_replaced(self):
        for bad in ("", "not-a-uuid", "AAAAAAAA-1111-4111-8111-111111111111"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(WalletError, "packet_id"):
                    create_packet(PROFILE, ["timezone"], "R", "P", 60, now=NOW, packet_id=bad)
        generated = create_packet(PROFILE, ["timezone"], "R", "P", 60, now=NOW)["packet_id"]
        self.assertNotEqual(generated, PACKET_ID)
        validate_packet({**make_packet(), "packet_id": generated})

    def test_selection_validation(self):
        with self.assertRaisesRegex(WalletError, "duplicates"):
            make_packet(("timezone", "timezone"))
        with self.assertRaisesRegex(WalletError, "at least one"):
            make_packet(())
        with self.assertRaisesRegex(WalletError, "not a string"):
            make_packet("timezone")
        with self.assertRaises(WalletError):
            create_packet(None, ["timezone"], "R", "P", 60)
        packet = make_packet(("timezone", "preferred_name"))
        self.assertEqual(list(packet["fields"]), ["timezone", "preferred_name"])

    def test_profile_and_packet_schema_details(self):
        with self.assertRaisesRegex(WalletError, "schema_version"):
            validate_profile({**PROFILE, "schema_version": True})
        with self.assertRaisesRegex(WalletError, "profile_id"):
            validate_profile({**PROFILE, "profile_id": "Upper-Case"})
        with self.assertRaisesRegex(WalletError, "at least one field"):
            validate_profile({**PROFILE, "fields": {}})
        with self.assertRaisesRegex(WalletError, "missing: fields"):
            validate_packet({k: v for k, v in make_packet().items() if k != "fields"})
        for bad in ("11111111-1111-4111-8111-11111111111A",
                    "{11111111-1111-4111-8111-111111111111}", "not-a-uuid", 1):
            with self.subTest(bad=repr(bad)):
                with self.assertRaisesRegex(WalletError, "packet_id"):
                    validate_packet({**make_packet(), "packet_id": bad})

    def test_malformed_json_inputs_raise_wallet_error(self):
        cases = {
            "nan.json": '{"schema_version": NaN}',
            "bom.json": "\ufeff{}",
            "broken.json": '{"schema_version": 1',
            "huge_int.json": '{"schema_version": ' + "1" * 5000 + "}",
            "deep.json": "[" * 100000 + "]" * 100000,
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, text in cases.items():
                with self.subTest(name=name):
                    path = Path(directory) / name
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(WalletError):
                        load_profile(path)
            latin1 = Path(directory) / "latin1.json"
            latin1.write_bytes(b'{"x": "\xe9"}')
            with self.assertRaises(WalletError):
                load_profile(latin1)
            with self.assertRaises(WalletError):
                load_profile(Path(directory) / "missing.json")

    def test_cli_errors_exit_2_with_message(self):
        with tempfile.TemporaryDirectory() as directory:
            deep = Path(directory) / "deep.json"
            deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
            for arguments in (["verify", "--packet", str(deep)],
                              ["verify", "--packet", str(Path(directory) / "missing.json")],
                              ["consume", "--packet", str(deep), "--ledger",
                               str(Path(directory) / "ledger.json")]):
                with self.subTest(arguments=arguments[0]):
                    code, out, err = self._run(arguments)
                    self.assertEqual(code, 2)
                    self.assertEqual(out, "")
                    self.assertTrue(err.startswith("Error: "))
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
                main(["preview"])
            self.assertEqual(raised.exception.code, 2)

    def test_cli_verify_rejects_expired_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packet.json"
            past = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            write_packet(path, create_packet(PROFILE, ["timezone"], "R", "P", 3600, now=past))
            code, _, err = self._run(["verify", "--packet", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("expired", err)

    def test_preview_command_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._export_args(directory, Path(directory) / "unused.json")
            arguments = ["preview"] + arguments[1:-2]
            code, out, _ = self._run(arguments)
            self.assertEqual(code, 0)
            self.assertEqual(out, render_packet(validate_packet(json.loads(out))) + "\n")
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["profile.json"])

    def test_ledger_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertEqual(load_ledger(ledger_path)["consumed_packet_ids"], [])
            ledger_path.write_text(json.dumps(
                {"schema_version": 1, "consumed_packet_ids": [PACKET_ID, PACKET_ID]}),
                encoding="utf-8")
            with self.assertRaisesRegex(WalletError, "duplicate"):
                load_ledger(ledger_path)
            ledger_path.write_text('{"schema_version": 1, "consumed_packet_ids": {}}',
                                   encoding="utf-8")
            with self.assertRaisesRegex(WalletError, "array"):
                consume_packet(make_packet(), ledger_path, now=NOW)

    @unittest.skipIf(context_wallet.fcntl is None, "advisory ledger lock is POSIX-only")
    def test_concurrent_consumes_on_one_ledger_do_not_lose_updates(self):
        ids = [f"22222222-2222-4222-8222-{index:012d}" for index in range(12)]
        results = []
        barrier = threading.Barrier(len(ids) + 4)
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"

            def worker(packet_id):
                packet = {**make_packet(), "packet_id": packet_id}
                barrier.wait()
                try:
                    consume_packet(packet, ledger_path, now=NOW)
                    results.append(("ok", packet_id))
                except WalletError:
                    results.append(("refused", packet_id))

            # 12 distinct IDs plus 4 extra attempts to consume the first ID again.
            threads = [threading.Thread(target=worker, args=(pid,)) for pid in ids + [ids[0]] * 4]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            recorded = load_ledger(ledger_path)["consumed_packet_ids"]
            self.assertEqual(sorted(recorded), sorted(ids))
            self.assertEqual(sum(1 for status, _ in results if status == "ok"), len(ids))
            self.assertEqual(sum(1 for status, _ in results if status == "refused"), 4)

    def test_cli_consume_marks_once_then_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "sub" / "ledger.json"
            arguments = ["consume", "--packet", str(ROOT / "sample-data" / "output_packet.json"),
                         "--ledger", str(ledger_path)]
            self.assertEqual(self._run(arguments)[0], 0)
            code, _, err = self._run(arguments)
            self.assertEqual(code, 2)
            self.assertIn("already been consumed", err)
            self.assertEqual(load_ledger(ledger_path)["consumed_packet_ids"], [PACKET_ID])

    def test_sample_packet_verifies_and_is_canonical(self):
        path = ROOT / "sample-data" / "output_packet.json"
        text = path.read_text(encoding="utf-8")
        packet = validate_packet(json.loads(text))
        self.assertEqual(text, render_packet(packet) + "\n")
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))
        code, out, _ = self._run(["verify", "--packet", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("Valid and unexpired", out)

    def test_sample_packet_is_reproducible_from_sample_profile(self):
        profile = load_profile(ROOT / "sample-data" / "profile.json")
        self.assertEqual(profile["profile_id"], "sample-fictional-profile")
        packet = create_packet(
            profile,
            ["preferred_name", "communication_style", "current_goal"],
            "Northstar Demo Coach",
            "Draft a one-time fictional neighborhood clean-up planning note.",
            24 * 60 * 60,
            now=datetime(2035, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            packet_id=PACKET_ID,
        )
        expected = (ROOT / "sample-data" / "output_packet.json").read_text(encoding="utf-8")
        self.assertEqual(render_packet(packet) + "\n", expected)

    def test_module_imports_only_offline_standard_library_modules(self):
        tree = ast.parse((ROOT / "context_wallet.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        allowed = {"__future__", "argparse", "datetime", "fcntl", "json", "os", "pathlib",
                   "re", "sys", "tempfile", "typing", "unicodedata", "uuid"}
        self.assertLessEqual(imported, allowed)


if __name__ == "__main__":
    unittest.main()
