# AI usage log

Record of generative-AI assistance, as required for academic integrity and
reviewability. **Every AI-produced change must be reviewed and verified by a team
member before it is relied upon.** Unverified AI output must never become a
reported result.

## Entry template

```text
Date:
Phase:
Tool/agent:
Task requested:
Files affected:
Output accepted as-is / modified / rejected:
How it was verified (command, test, manual check):
Team reviewer:
```

---

## 2026-11-17 — Phase 0: repository and reproducibility

**Tool/agent:** AI coding assistant (Copilot SDK in VS Code), acting as principal
engineer per `plan.md §1`.
**Task requested:** Create the Phase 0 repository structure and baseline files,
install and verify the Python environment, run the Phase 0 checks, and report
files, assumptions, tests, and blockers. Stop before Phase 1.

**Work performed:**
- Created the §4 directory skeleton with `.gitkeep` placeholders.
- Wrote `.gitignore`, `.env.example`, `requirements.txt`, `config/app.example.yaml`,
  `config/classes.yaml`, `README.md`, and the Phase 0 subset of `docs/`.
- Implemented `src/config.py` (typed, bounded configuration loading).
- Implemented `tools/env_check.py` and `tools/camera_check.py`.
- Added `tests/test_config.py` covering the configuration contract.
- Created `.venv` with Python 3.12.4 and installed the pinned dependency set.

**Verification actually performed:**
- `pip install -r requirements.txt` completed with exit code 0, in the working tree
  and again in a fresh `git clone` into a temporary directory.
- `tools/env_check.py` → `Environment check OK: 5 passed, 1 warning(s), 0 failure(s)`.
- `python -m pytest -q` → `108 passed`.
- `tools/camera_check.py` → `Camera check PASSED.`, 29.9–30.1 FPS at 1280×720.
- Ultralytics smoke test: YOLOv8n architecture constructed and ran a forward pass
  on a synthetic 640×640 image, returning 0 detections.
- Two deliberate failure paths: missing `config/app.yaml` (readable `FAIL`, exit 1)
  and camera index that does not exist (readable `ERROR`, exit 1).

**Defects this verification caught (and corrected):**
- `serial.port` was silently coerced instead of validated; a blank or non-string
  value could have triggered unintended autodiscovery (`src/config.py`).
- `tools/env_check.py` printed `python-dotenv==?` while still passing, because that
  package has no `__version__`; it now reads installed metadata.
- `.gitignore` allowed verified-bad re-inclusions and never ignored `*.jpg`;
  model weights were briefly re-included by a negation, contradicting plan §4.
- `tools/camera_check.py` printed a mojibake FOURCC (`'ÿÿÿÿ'`) on macOS.

**Not verified (and therefore not claimed):**
- No model weights exist; no inference accuracy is measured or claimed.
- No ESP32, Blynk, or serial-hardware behaviour has been tested.

**Team action required:** Each team member must read the source files they will
present, re-run the Phase 0 checks on their own machine, and record their own
verification entry below.

---

## Team verification entries

_(add one per member after re-running the checks)_

---

# Phase 1 — Dataset audit: AI usage record

## What the AI did

Wrote `tools/inspect_dataset.py` (read-only auditor) and
`tests/test_inspect_dataset.py` (23 tests on synthetic fixtures), ran the audit
against `data/raw/roboflow_safety_glasses_original.zip`, and wrote
`docs/dataset_audit.md` plus the 13 records in `data/reviewed/audit/`.

## Claims the AI made and how each was checked

| Claim | How it was verified |
|---|---|
| Archive is unmodified | SHA-256 recomputed after the run and compared to the pre-run value; also asserted by a test that checks bytes and mtime |
| 400 images / 400 labels / 770 instances | Cross-checked against an independent extraction of the ZIP; the first run reported 387/755 and that discrepancy exposed a real pairing bug (see below) |
| 2 malformed rows | Independently re-read the two named label files; both really contain 9 fields |
| 59 leakage groups | Broken down by kind and the five source stems (`SG6`, `SG8`, `SG11`, `SG2`, `SG14`) were listed individually and matched against the earlier ad hoc audit |
| Outputs are deterministic | Ran the tool twice and diffed all 13 records; only the timestamps differed |
| 131 tests pass | Ran `pytest -q`; result captured verbatim |
| Phase 0 still works | Re-ran `env_check.py` and `camera_check.py` after the Phase 1 changes |

## Two errors the AI made and corrected

Both were caught by executing code, not by re-reading it. Recorded here because
the corrected behaviour is the load-bearing part, not the original mistake.

1. **`zipfile.ZipFile(path, "rb")`.** The auditor crashed on the very first run
   with `ValueError: ZipFile requires mode 'r', 'w', 'x', or 'a'`. Fixed to
   `"r"`. The exception handler was widened to cover `ValueError` so a bad
   archive surfaces as a readable error rather than a traceback.

2. **Pairing images to labels on a normalised stem.** The first successful run
   reported 387 label files and 755 instances against 400 images. Investigation
   showed two images sharing a normalised stem (`SG10_jpg.rf.<hashA>` and
   `SG10_jpg.rf.<hashB>`) were collapsed into one pairing key, so one of each
   pair looked unlabelled and its instances were dropped. Pairing now uses the
   exact stem; normalisation is retained only for leakage grouping. Corrected
   counts: 400 labels, 770 instances.

A third, smaller error: three of the first tests asserted a `run_audit` keyword
(`contact_sheets`) that does not exist — the real parameter is
`make_contact_sheets`, and the return value is a wrapper dict whose `summary`
key holds the findings. The tests were corrected to match the real signature.
That mismatch also exposed a genuine gap: `run_audit` hashed the archive before
checking it existed, so a missing path leaked a raw `FileNotFoundError`. An
explicit existence check was added.

## What the AI did NOT do

- Did not train, fine-tune, or evaluate any model.
- Did not create `models/best.pt`.
- Did not rename `glasses` to `goggles`, remap any class, or infer `no_goggles`
  from the absence of a box.
- Did not modify, move, rename, or re-zip the source archive.
- Did not rewrite splits or delete any duplicate.
- Did not commit extracted images, the archive, contact sheets, or weights.
- Did not claim any accuracy, mAP, precision, recall, or latency figure. The
  audit produces no performance metric, because no model was run.

## Residual uncertainty

The near-duplicate threshold (Hamming <= 10 on a 64-bit dHash) is a judgement
call, not a calibrated value. It is exposed as `--near-dup-bits` so a human can
re-run with a different bound. The 377 figure should be read as "many frames are
near-identical, consistent with video sampling", not as a defect count.

---

# Phase 2A — Hardware bring-up: AI usage record

## Entry

```text
Date:            2026-11-17
Phase:           2A — Hardware bring-up (firmware self-test only)
Tool/agent:      GitHub Copilot CLI (agent mode)
Task requested:  Minimal ESP32-S3 firmware self-test driving green LED GPIO11,
                 red LED GPIO12 and passive buzzer GPIO14, with a serial command
                 menu, non-blocking millis() timing, and configurable
                 active-high/active-low polarity. No Blynk, no YOLO, no JSON
                 protocol, no 7B model, no trivial-code tests.
Files affected:  firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino (new)
                 firmware/esp32_eye_shield_monitor/config.example.h (new)
                 firmware/esp32_eye_shield_monitor/README.md (rewritten)
                 docs/hardware_bringup.md (new)
                 docs/decision_log.md (D-014, D-015, D-016; O-02 phase updated)
                 docs/ai_usage_log.md (this entry)
                 docs/dataset_audit.md, data/README.md, README.md,
                 docs/annotation_guideline.md (Phase 1 provenance correction only)
Output accepted as-is / modified / rejected: accepted after two AI self-corrections
                 described below; no human hardware verification yet.
How it was verified: arduino-cli compile for esp32:esp32:esp32s3 (esp32 core
                 3.3.11) — pass, 315,765 bytes (24 %) flash / 22,296 bytes (6 %)
                 RAM; re-compiled with --warnings all — no warnings; re-compiled
                 with a local config.h override — pass; git check-ignore confirmed
                 config.h is ignored and config.example.h is trackable.
                 NO physical output has been observed.
Team reviewer:   PENDING — requires the human hardware test in
                 docs/hardware_bringup.md §6 and the filled-in §9 record.
```

## Phase 1 documentation correction (the only Phase 1 change made)

Two corrections were requested and applied; no audit feature, test, CSV,
duplicate analysis, or contact sheet was added, and `tools/inspect_dataset.py`
and `tests/test_inspect_dataset.py` were not touched.

1. **Provenance is recorded, not blocking.** `docs/dataset_audit.md` §1 now
   states: original source `workspace-3vzwb/safety-glasses-dataset`, fork/export
   workspace `eie3360-fwbnu`, licence CC BY 4.0, and that this is **not a
   blocker** requiring no re-download or new fork. §15/§16, `data/README.md`,
   `README.md` §9 and `docs/annotation_guideline.md` §6 were aligned, and
   decision **D-014** records the reasoning.
2. **No cut-out claim from geometry alone.** `docs/dataset_audit.md` §7 now
   says the small eyewear-level boxes are **incompatible with the proposed
   head/face-region compliance labels** and that the semantic content **requires
   human visual review**. The previous "consistent with tight product cut-outs"
   inference was removed, since bounding-box statistics cannot establish what an
   image depicts.

## Two errors the AI made and corrected before reporting

**1. Unreachable self-test states.** The first sequencer used a nested switch over
an enum with eight states plus `ST_GREEN_OFF`, `ST_RED_OFF`, `ST_BUZZER_OFF` and
`ST_COMBINED_OFF` entries that no transition ever entered, and several cases
advanced to a state whose label did not match the outputs it had just set. The
sequence was rewritten as a flat table where each entry states the **complete**
desired output combination. That removes the class of bug entirely: a step can no
longer leave a stale output energised, and reordering is a one-line change.

**2. `digitalWrite()` on an LEDC-owned pin.** The first buzzer silence routine
called `ledcWrite(channel, 0)` and then `digitalWrite(pin, inactive)`. The second
call does not reliably change the level, because the LEDC peripheral mux still
owns the pin. Fixed by detaching from LEDC (`ledcDetach` / `ledcDetachPin`)
**before** parking the pin. Both core 2.x and 3.x APIs are handled behind an
`ESP_ARDUINO_VERSION_MAJOR` branch.

## Claims the AI made and how each was checked

| Claim | Check |
|---|---|
| The sketch compiles for the ESP32-S3 | `arduino-cli compile --fqbn esp32:esp32:esp32s3` — pass, exit 0 |
| No compiler warnings | same compile with `--warnings all` — clean |
| A local `config.h` override still compiles | compiled with a generated `config.h` altering a pin and a polarity — pass |
| `config.h` cannot be committed by accident | `git check-ignore -v` resolves it to `.gitignore:20`; `git status` shows it untracked-and-ignored |
| No board entry named "ESP32-S3 UNO" exists | `arduino-cli board listall` piped through grep — only generic `esp32s3` targets present |
| esp32 core version is 3.3.11 | listed `~/Library/Arduino15/packages/esp32/hardware/esp32/` |

## What the AI did NOT do

- Did **not** claim that any LED, the buzzer, or any pin works. No physical
  observation exists.
- Did **not** invent a macOS serial device path. No ESP32 board was attached to
  this Mac during the work (`/dev/cu.*` held only Bluetooth pseudo-ports), so
  §3 of `docs/hardware_bringup.md` documents the *procedure* and leaves the value
  for the human to record.
- Did **not** implement Blynk, Wi-Fi, YOLO, the JSON serial protocol, incident
  IDs, the alarm state machine, or Silence/Acknowledge/Reset.
- Did **not** use or test any 7B model.
- Did **not** add automated tests for this firmware.
- Did **not** commit `config.h`, any credential, or any machine-specific value.
- Did **not** restart or expand Phase 1.

## Residual uncertainty (for the human to resolve)

1. The board's actual pin silkscreen — GPIO11/12/14 are assumed exposed.
2. LED module polarity — defaulted active-high; observable in one `SELF_TEST` run.
3. Buzzer type — a passive piezo is assumed. An active buzzer would sound
   continuously and ignore the PWM frequency.
4. Module supply voltage — 3.3 V vs 5 V variants exist and are not
   interchangeable.
5. The macOS serial device path and bridge chip — unknown until the board is
   plugged in.
