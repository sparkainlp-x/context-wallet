# Context Wallet

[![tests](https://github.com/sparkainlp-x/context-wallet/actions/workflows/tests.yml/badge.svg)](https://github.com/sparkainlp-x/context-wallet/actions/workflows/tests.yml)

A compact, offline prototype for preparing a small context packet from a fictional or user-created local profile. A person chooses specific allowlisted fields, names a recipient, states a purpose, sets a short lifetime, and sees the packet before exporting it. The project uses only the Python standard library and makes no network requests.

## Quick start

Requires Python 3.10 or newer. From this directory:

```sh
python3 context_wallet.py preview \
  --profile sample-data/profile.json \
  --fields preferred_name,timezone,communication_style \
  --recipient "Northstar Demo Coach" \
  --purpose "Prepare one fictional planning note" \
  --ttl-seconds 3600
```

`preview` prints the exact JSON representation and writes no packet file. To export, use the same arguments with `export` and an unused local destination:

```sh
python3 context_wallet.py export \
  --profile sample-data/profile.json \
  --fields preferred_name,timezone,communication_style \
  --recipient "Northstar Demo Coach" \
  --purpose "Prepare one fictional planning note" \
  --ttl-seconds 3600 \
  --out ./northstar-packet.json
```

The `export` command creates one packet, prints its JSON, and asks for a local `y` confirmation. The file is written only after confirmation, using the same serialized bytes shown in that preview. Any other answer, end of input, or Ctrl-C cancels the export, writes nothing, and exits with status 1. `export` never replaces an existing file, including one created at the `--out` path while the confirmation prompt was waiting. The standalone `preview` command is a dry run; a later `export` invocation creates a new packet ID and timestamps, so use the preview printed by `export` itself as the final review.

Validate a packet's schema and expiry, or mark it used in a local ledger:

```sh
python3 context_wallet.py verify --packet sample-data/output_packet.json
python3 context_wallet.py consume \
  --packet sample-data/output_packet.json \
  --ledger .context-wallet-ledger.json
```

Exit status is 0 on success, 1 for a cancelled export, and 2 for errors (unreadable or invalid input, an expired packet, a packet already in the ledger, or an existing export destination); errors are printed to standard error.

Run the test suite and syntax check:

```sh
python3 -m py_compile context_wallet.py tests/test_context_wallet.py
python3 -m unittest discover -s tests -v
```

The `sample-data/` files use invented values only. Their fixed 2035 dates make the packet a stable, currently unexpired fixture; they are illustrative, not a suggested lifetime. The sample profile is not populated from a real person.

## What the prototype accepts

Profiles are JSON objects with exactly these top-level keys: `schema_version` (integer `1`), `profile_id` (a short lowercase local label), and `fields` (a non-empty object). A profile may contain a subset of these field names only:

- `preferred_name` (up to 80 characters)
- `role` (up to 120)
- `timezone` (up to 64)
- `communication_style` (up to 240)
- `current_goal` (up to 240)
- `accessibility_preferences` (up to 240)

Each value must be non-blank text. Unknown keys, extra schema properties, duplicate JSON keys, `NaN`/`Infinity` constants, control characters (including tabs and newlines), blank values, and overlong values are rejected. Selection must be non-empty, unique, allowlisted, and present in the profile. The profile ID and unselected values are never copied into a packet.

A packet contains only `schema_version`, a random packet ID (lowercase canonical UUID), the recipient label (up to 120 characters), stated purpose (up to 400), UTC creation and expiry times (`YYYY-MM-DDTHH:MM:SSZ`), and selected field/value pairs, in the order they were selected. Lifetimes must be from 1 second to 7 days. The preview and file export share one canonical JSON serializer; the export ends with one newline.

## Local-use workflow and limits

The optional consume ledger records packet IDs in a local JSON file and refuses to consume the same ID from that ledger twice. It is a convenience for local workflow state, not a portable or remote one-use guarantee. On POSIX systems, concurrent `consume` runs on the same ledger path take an advisory lock on a sidecar `<ledger>.lock` file so they do not lose each other's updates; on other platforms, racing separate processes can still bypass the ledger. Removing or editing the ledger, or using another device or ledger copy, bypasses it everywhere. A recipient does not consult this ledger.

Expiry is checked against this computer's clock when this program verifies or locally consumes a packet; a packet is expired from its `expires_at` second onward. `verify` does not reject a `created_at` in the future (the 2035 sample depends on this), so it cannot detect a wrong clock or back-dated labels. It is not a remote deletion mechanism: expiry does not force a recipient to forget, delete, or stop using a copy. The packet is ordinary plaintext JSON. This prototype is not encryption, authentication, a digital signature, tamper-proof storage, identity verification, or proof that a named recipient received the packet. Anyone with a copy can read or alter it, and the recipient/purpose labels are descriptive only. It has no integration with any AI, website, app, or recipient system.

Keep profiles, drafts, exports, and ledgers only where you are comfortable storing plaintext files. Use fictional or synthetic values while evaluating this prototype; never put real personal data in the sample files. The code includes no telemetry, background process, browser access, network client, secrets, or external dependencies.

## Suggested pilot measures

If piloting with consenting participants, collect only aggregate counts and timings, and do not retain profile values or packet contents for measurement. Useful signals include:

- **Preparation time:** median time from starting field selection to completing a reviewed export.
- **Packet minimization:** median number of fields selected per packet, plus the share of packets with one or two fields.
- **Preview value:** how often a participant removes or changes a field after seeing the preview.
- **Purpose clarity:** recipient/participant rating of whether the stated purpose explains why each included field is needed.
- **Workflow friction:** rates of invalid-field errors, cancelled exports, and repeated attempts.

These are proposed manual pilot measures only; the prototype does not collect or calculate them.

## Citation

See [CITATION.cff](CITATION.cff).

## License

This software is available under the GNU Affero General Public License v3.0 only (AGPL-3.0-only); see [LICENSE](LICENSE).

Organizations that want to use it in proprietary products or services without AGPL obligations can contact the author about a commercial license via https://sparkainlpx.xyz.
