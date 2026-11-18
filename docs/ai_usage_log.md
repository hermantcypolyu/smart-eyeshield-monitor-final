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
                 3.3.11) — pass, 314,825 bytes (24 %) flash / 22,296 bytes (6 %)
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

### Phase 2A follow-up — dispatcher cleanup (same day)

**Task given:** a self-review of the committed firmware, not a new phase.

**What changed:** the serial dispatcher was rebuilt as a table lookup
(`OUTPUT_COMMANDS[]`) with `void` handlers, replacing an if/else chain that
compared `String` against `F("...")`. The `CommandEntry` struct moved to a new
`command_table.h`. Command behaviour, output text, and the self-test are
unchanged; flash use dropped from 315,765 to 314,825 bytes.

**Why:** `WString` in esp32 core 3.3.11 has no `operator==(const
__FlashStringHelper *)`, so each `cmd == F("GREEN_ON")` silently built a
temporary `String`. `PSTR()` is `(s)` on ESP32, so the flash-string wrapper
gained nothing there. `String::equals()` compares in place with no allocation.

**Error made and corrected during this step:** moving the struct into the `.ino`
broke the build with `'CommandEntry' does not name a type`. A forward declaration
did not help. Reading the generated
`sketch/esp32_eye_shield_monitor.ino.cpp` showed the builder pastes every
function prototype into one block at the top of the translation unit, ahead of
all definitions — so the struct had to move to a header the preprocessor expands
first. Recorded as D-017.

**Second error, in the verification itself:** the first `config.h` override test
failed because the throwaway file I generated used invented macro names
(`LED_GREEN_ACTIVE_HIGH`) instead of the real ones (`GREEN_ACTIVE_HIGH`). The
firmware was correct; the test fixture was wrong. Re-run with the real names:
passes.

**Verified:** compiles clean for `esp32:esp32:esp32s3` under `--warnings all`;
compiles clean with a local `config.h` override using inverted polarity and
different pins; the temporary `config.h` was deleted afterwards and remains
git-ignored; `pytest` — 131 passed.

**Still not verified:** every physical output. No board has been attached, and
no claim of hardware success is made here.

---

## 2026-11-17 — Phase 2A rewritten against `plan.md` v2

**Task given:** read the newly replaced `plan.md` (v2, 1029 lines) completely and
begin Phase 2A exactly as it specifies, producing the mandatory working Arduino
firmware, attempting compilation when a toolchain is available, running the
existing regression checks, and then stopping for physical hardware validation.
Explicitly out of scope: Phase 2B, JSON incident integration, Blynk, YOLO.

### What changed and why

The earlier Phase 2A firmware was written to the previous 739-line plan. v2
differs materially, so the two source files were rewritten rather than patched:

| Item | Before (old plan) | After (`plan.md` v2) |
|---|---|---|
| Serial commands | 9 (`GREEN_ON`…`SELF_TEST`) | **14** — adds `SAFE`, `ALARM`, `SILENCE`, `STOP_TEST` |
| Macro names | `GREEN_ACTIVE_HIGH`, `BUZZER_MODE_PASSIVE` | `GREEN_LED_PIN`, `GREEN_LED_ACTIVE_HIGH`, `BUZZER_IS_PASSIVE`, … |
| Buzzer modes | passive only | passive **and** active, selectable |
| Tone frequency | 2700 Hz | **2000 Hz** (v2's mandated initial frequency) |
| Self-test | 8 steps | 8 steps, same content, now with `STOP_TEST` interrupt |
| Helper functions | implicit | v2's exact 10 names, incl. `applyOutputs` + `updateSelfTest(nowMs)` |
| Extra file | `command_table.h` | **deleted** — not in v2's mandatory file list |
| `STATUS` fields | pins, polarity, state | adds self-test step, uptime, active config source |

`config.example.h` and `esp32_eye_shield_monitor.ino` were deleted and recreated
so no superseded macro name or code path survives. `README.md` and
`docs/hardware_bringup.md` were rewritten to match, including v2's exact
14-step physical test order. `docs/decision_log.md` D-017 was rewritten (it
described the now-deleted `command_table.h`) and D-016's "revisit if" clause was
corrected now that an active buzzer is a supported configuration rather than a
replacement trigger.

### Claims made and how each was checked

| Claim | Evidence |
|---|---|
| Compiles for the recorded target | `arduino-cli compile --fqbn esp32:esp32:esp32s3 --warnings all` → 316,337 B (24%) flash, 22,296 B (6%) RAM, **zero warnings** |
| The `config.h` override path compiles | Created a temporary `config.h` with v2 macro names, active-low on all three outputs, `BUZZER_IS_PASSIVE 0`, 9600 baud, 250 ms steps → 308,570 B (23%) flash, zero warnings; file deleted afterwards |
| Both buzzer branches are compiled, not just the default | The active-buzzer build exercised the `#else` branch of `startBuzzerTone`/`stopBuzzerTone` |
| `command_table.h` is gone and nothing references it | `grep` over the repository; only the decision log mentions it, historically |
| Phase 1 unaffected | `.venv/bin/python -m pytest -q` → **131 passed** |
| Serial device path | **Not claimed.** `ls /dev/cu.*` shows only Bluetooth pseudo-ports; no board attached |

### Errors made and corrected during this step

1. **Unused-variable warning in the active-buzzer build.** `g_buzzer_pwm_ready`
   is only meaningful when `BUZZER_IS_PASSIVE` is 1; with an active buzzer the
   compiler warned `defined but not used` under `--warnings all`. Fixed by
   wrapping the declaration in `#if BUZZER_IS_PASSIVE`. Both builds are now
   warning-free.
2. **The first compile result was from a different configuration than reported.**
   The default-config compile (316,337 B) and the override compile (308,570 B)
   were initially run in separate steps, and it was easy to quote one size for
   both. Both were re-run and reported separately above.

### What the AI did NOT do

- Did not upload to hardware — no board is attached.
- Did not claim any LED or buzzer works. Every output remains UNVERIFIED until
  the human completes the §9 observation record.
- Did not invent a macOS serial device path.
- Did not implement Blynk, YOLO, the JSON protocol, or the latched alarm state
  machine.
- Did not add automated tests for the firmware. v2 mandates none, and the Code
  Change Instructions forbid tests for trivial accessors.
- Did not modify any Phase 0 or Phase 1 file. `tools/inspect_dataset.py` and
  `tests/test_inspect_dataset.py` are untouched.

### Residual uncertainty (for the human to resolve)

1. Whether the board's silkscreen actually exposes GPIO11/12/14 under those
   numbers.
2. Whether the LED modules are active-high (the configured default) or
   active-low.
3. Whether the buzzer is passive or active, and whether GPIO14 can source it
   without a transistor driver.
4. Whether the traffic-light module is a 3.3 V or 5 V variant.
5. The macOS serial device path and bridge chip.
6. Whether the USB cable carries data.

None of these can be settled from the repository. They are resolved by the
physical test order in `docs/hardware_bringup.md` §6.

---

## 2026-11-18 — Phase 2A physical validation recorded

**Task given:** update the configuration template, firmware README, bring-up
guide, decision log and this log with the results of the human's physical
hardware test; run regression checks; commit; report the commit hash. Mark Phase
2A complete only if `SELF_TEST` and `STOP_TEST` were also physically verified.

### What the human reported

| Item | Result |
|---|---|
| Board port | `/dev/cu.usbserial-10` |
| Upload speed | 921600 unstable; **115200 works**, flash hash verified |
| Green | GPIO11, **active-HIGH**, works |
| Red | GPIO12, **active-HIGH**, works |
| Buzzer | GPIO14, three-pin **active** module, **active-LOW**, 3V3 |
| `SAFE` / `ALARM` / `SILENCE` / `ALL_OFF` | all four work |
| `SELF_TEST` / `STOP_TEST` | **NOT tested** |
| Not used | Arduino Uno, HC-SR04, servo, level shifter, external 5 V, transistor |

### What changed

- `config.example.h` — polarity and buzzer mode set to the measured values and
  every measured line annotated `MEASURED`, so no reader mistakes a guess for a
  measurement. The passive-buzzer tone block is now labelled dead code rather than
  live configuration.
- `firmware/.../README.md` — status line, configuration table and upload section
  now state verified values instead of defaults.
- `docs/hardware_bringup.md` — §0 status table, §1.4 IDE settings, §1.5 port
  discussion, §2.1/§2.2 wiring, §5.1 compile figures, §7.3 buzzer description and
  §8.2/§8.3/§8.5 risks all rewritten from "unresolved" to resolved where the
  hardware settled them. §9 observation record filled in from the human's report.
- `docs/decision_log.md` — new D-018 records the verified configuration; O-02
  (active-high or active-low) closed.

### The one finding that changed a design assumption

The draft assumed a passive, active-high buzzer. Both halves were wrong: the part
is **active** and **LOW-level trigger**. Two consequences were recorded rather
than papered over:

1. `BUZZER_FREQUENCY_HZ` is inert on this hardware. The 2000 Hz figure that
   `plan.md` v2 mandates as the initial frequency is configured but never reaches
   the buzzer, because an active module generates its own tone. This is stated
   plainly in §7.3 rather than letting a reader assume pitch is controlled.
2. Phase 4/5 alarm patterns cannot vary by pitch with this part. Recorded in D-018
   as a constraint to carry forward, with the passive-piezo swap as the remedy.

Conversely, the two subsystems disagreeing on polarity vindicates the per-output
`*_ACTIVE_HIGH` flags from D-016: a single global polarity macro could not have
described this board.

### Claims made and how each was checked

| Claim | Evidence |
|---|---|
| The verified configuration compiles | `arduino-cli compile --fqbn esp32:esp32:esp32s3 --warnings all` → 308,594 B (23%) flash, 22,176 B (6%) RAM, zero warnings |
| Phase 1 still intact | `.venv/bin/python -m pytest -q` → **131 passed** |
| The passive-buzzer branch was not broken by the config change | Previously compiled at 316,337 B; the branch is unchanged and still selected by `BUZZER_IS_PASSIVE 1` |
| No secrets in the template | `grep` for password/ssid/token/secret — only comments referencing the future Phase 5 use |

### What the AI did NOT do

- Did **not** mark Phase 2A complete. `SELF_TEST` and `STOP_TEST` were not
  physically verified, so they are recorded as the only outstanding items.
- Did **not** claim the buzzer produces a 2000 Hz tone — it cannot, on this part.
- Did **not** fill in multimeter readings, board silkscreen name, bridge chip or
  measured current. Those were never reported, so they are recorded as
  "not recorded" rather than invented.
- Did **not** upgrade any unmeasured value to `MEASURED`.
- Did **not** begin Phase 2B, and did not touch any Phase 0 or Phase 1 file.

### Residual uncertainty

1. `SELF_TEST` behaviour on hardware — untested.
2. `STOP_TEST` mid-sequence cancellation — untested.
3. `STATUS` during a running self-test — untested (depends on the above).
4. Unknown command handling, lowercase input, and green-then-red exclusivity —
   not separately reported.
5. Pin voltages and buzzer current — not measured.

---

## 2026-11-18 — Phase 2A final validation and acceptance

**Task given:** the human reported the final two physical checks (`SELF_TEST`,
`STOP_TEST`) as PASS on every sub-check; mark Phase 2A complete, update
`hardware_bringup.md`, `decision_log.md` and this log, run regression checks,
commit, and report the hash plus remaining blockers and the work needed before
Phase 2B. Explicit instruction: **do not begin Phase 2B.**

### What the human reported

`SELF_TEST` — full eight-step sequence completed; green, red, buzzer and
red + buzzer stages each PASS; finished with all outputs OFF.

`STOP_TEST` — accepted during an active sequence, stopped immediately, green OFF,
red OFF, buzzer OFF, and final `STATUS` reported self test inactive.

### What changed

- `docs/hardware_bringup.md` — §0 status flipped from "NOT complete" to
  "COMPLETE"; §8.5 open-risk list reduced, with the self-test moved to resolved;
  §9.2 supplementary checks closed where now answered; new §9.4 gives the
  per-stage self-test and `STOP_TEST` results; new §9.5 is the final ten-item
  acceptance table mapped to `plan.md` v2's requirements.
- `docs/decision_log.md` — new D-019 records acceptance and explains why
  `STOP_TEST` succeeding is a design confirmation rather than a checkbox.
- `docs/ai_usage_log.md` — this record.
- `firmware/esp32_eye_shield_monitor/README.md` — status line and scope-of-claim
  block corrected. **This file was not in the instructed list but had to change:**
  it still asserted that `SELF_TEST` and `STOP_TEST` had not been run. Leaving it
  would have put a false statement in the repository.

### Honesty note on the earlier record

Commit `d95b667` states Phase 2A is not complete. That was true when committed and
is now superseded by D-019. The earlier text was **not** rewritten to look as
though the claim had always been justified — the superseding entry is additive, so
the history shows the claim was withheld until evidence existed.

### What the AI did NOT do

- Did **not** claim any observation the human did not report. Unknown-command
  handling, lowercase input, and green-then-red exclusivity (§9.2 S2/S3/S5) are
  still "not recorded", and pin voltages and buzzer current are still unmeasured.
  These are optional supplementary checks, not Phase 2A acceptance criteria.
- Did **not** upgrade "not recorded" to PASS by inference. Notably `HELP` is
  listed as unexercised rather than folded into the 14/14 count, even though it is
  non-actuating and would not have changed acceptance.
- Did **not** begin Phase 2B, touch any Phase 0/1 file, or add features or tests.
- Did **not** reopen the firmware. It was verified as-is; no code changed in this
  segment, so the compile result stands from `c503e4c`.

### Claims made and how each was checked

| Claim | Evidence |
|---|---|
| Phase 2A acceptance is recorded, not asserted | Ten requirements in §9.5 each map to a reported observation |
| Firmware still builds | `arduino-cli compile --warnings all` → 308,594 B (23%) / 22,176 B (6%), zero warnings |
| No regressions | `.venv/bin/python -m pytest -q` → **131 passed** |
| No stale "not verified" claims remain in the bring-up docs | `grep -n "OUTSTANDING\|not run — blocked"` → no matches |
| Command-coverage claim is accurate | 13 of 14 exercised; `HELP` recorded as not separately reported rather than counted as PASS |

### Residual risk carried into Phase 2B

1. The buzzer is an active module, so alarm patterns cannot vary by pitch (D-018).
   Phase 4/5 must use on/off duty patterns, or swap to a passive piezo.
2. No pin voltages or buzzer current were measured. Functionally proven; the
   electrical margin is unquantified.
3. The board's silkscreen name and bridge chip remain unidentified. This only
   matters for reproducing the setup on a second unit.
4. The self-test and abort path must be re-exercised after Phase 4/5 adds the
   latched state machine.
