# Context Wallet

[![tests](https://github.com/sparkainlp-x/context-wallet/actions/workflows/tests.yml/badge.svg)](https://github.com/sparkainlp-x/context-wallet/actions/workflows/tests.yml)

A compact, offline prototype for preparing a small context packet from a fictional or user-created local profile. A person chooses specific allowlisted fields, names a recipient, states a purpose, sets a short lifetime, and sees the packet before exporting it. The project uses only the Python standard library and makes no network requests.

## Install

Requires Python 3.10 or newer and nothing else. Run the single script from this directory, or install it from a clone to get a `context-wallet` command (which can replace `python3 context_wallet.py` below):

```sh
python3 -m pip install .
context-wallet --version
```

## Quick start

From this directory:

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

`export` prints the packet and asks for `y`; only then does it write exactly the bytes it showed. Any other answer, end of input, or Ctrl-C writes nothing and exits 1. It never replaces an existing file, even one created while the prompt was waiting. Each run makes a new packet ID and timestamps, so the preview printed by `export` itself (not an earlier `preview`) is the final review.

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

The `sample-data/` files are invented. The packet's fixed 2035 dates just keep it an unexpired test fixture.

## What the prototype accepts

Profiles are JSON objects with exactly these top-level keys: `schema_version` (integer `1`), `profile_id` (a short lowercase local label), and `fields` (a non-empty object). A profile may contain a subset of these field names only:

- `preferred_name` (up to 80 characters)
- `role` (up to 120)
- `timezone` (up to 64)
- `communication_style` (up to 240)
- `current_goal` (up to 240)
- `accessibility_preferences` (up to 240)

Each value must be non-blank text. Unknown keys, extra schema properties, duplicate JSON keys, `NaN`/`Infinity` constants, control characters (including tabs and newlines), blank values, and overlong values are rejected. So are invisible Unicode formatting characters (category Cf, e.g. right-to-left overrides and zero-width spaces/joiners, plus line/paragraph separators) in profile values, recipient and purpose, because they can make the preview read differently from the exported text; a side effect is that emoji built with zero-width joiners are not accepted. Selection must be non-empty, unique, allowlisted, and present in the profile. The profile ID and unselected values are never copied into a packet.

A packet contains only `schema_version`, a random packet ID (lowercase canonical UUID), the recipient label (up to 120 characters), stated purpose (up to 400), UTC creation and expiry times (`YYYY-MM-DDTHH:MM:SSZ`), and selected field/value pairs, in the order they were selected. Lifetimes must be from 1 second to 7 days. The preview and file export share one canonical JSON serializer; the export ends with one newline.

## Limits

- **Plaintext, unsigned:** a packet is ordinary JSON. It is not encrypted, authenticated or signed; anyone with a copy can read or edit it (an edited packet still passes `verify` if it stays within the schema). Recipient and purpose are descriptive labels only, not identity checks or proof of delivery.
- **Expiry is advisory:** it is checked only by this program, against the local clock (a packet is expired from its `expires_at` second). It cannot make a recipient delete or stop using a copy. `verify` does not reject a future `created_at`, so it cannot detect a wrong clock.
- **The consume ledger is local:** it refuses to consume the same packet ID twice *from that ledger file*. Deleting or copying the ledger, or using another device, bypasses it, and recipients never see it. On Linux/macOS concurrent `consume` runs are serialized with an advisory lock on `<ledger>.lock`; on Windows no lock is taken.
- **No integrations:** no network client, telemetry, or connection to any AI, app or website. Use fictional values while evaluating, and keep profiles, packets and ledgers only where plaintext is acceptable.

## Suggested pilot measures

If you pilot this with consenting participants, collect only aggregate counts and timings (never profile values or packet contents): time to a reviewed export, fields selected per packet, how often the preview leads to a change, whether the stated purpose justifies each field, and error/cancellation rates. The prototype does not collect any of these itself.

## Related work / when to use something else

- **Verifiable, selectively disclosed claims:** [W3C Verifiable Credentials](https://www.w3.org/TR/vc-data-model-2.0/) with [SD-JWT](https://datatracker.ietf.org/doc/rfc9901/) or BBS+ signatures, when a recipient must be able to check who issued the data and that it was not altered.
- **Confidentiality:** encrypt the packet for the recipient (e.g. [age](https://age-encryption.org/) or OpenPGP) if it must not be readable in transit or at rest.
- **User-controlled data stores:** [Solid](https://solidproject.org/) pods, for access-controlled sharing that can be revoked on the server side.
- **Consent records:** the Kantara Consent Receipt specification and ISO/IEC TS 27560, for standardized records of what was shared, with whom, and why.

Context Wallet is a small, readable demonstration of data minimization and preview-before-share for local files; it is not a substitute for any of the above.

## Citation

See [CITATION.cff](CITATION.cff).

## License

This software is available under the GNU Affero General Public License v3.0 only (AGPL-3.0-only); see [LICENSE](LICENSE).

Organizations that want to use it in proprietary products or services without AGPL obligations can contact the author about a commercial license via https://sparkainlpx.xyz.
