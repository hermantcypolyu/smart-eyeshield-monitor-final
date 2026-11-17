# ESP32-S3 Eye-Shield Monitor — Firmware

**Status: Phase 2A hardware bring-up. Self-test firmware only.**

This sketch proves that the three physical outputs are wired and driven
correctly. It is **not** the product firmware.

| Implemented in Phase 2A | Deliberately not implemented yet |
|---|---|
| Green LED on GPIO11 | Blynk / Wi-Fi / credentials |
| Red LED on GPIO12 | YOLO or camera integration |
| Passive buzzer PWM on GPIO14 | JSON serial protocol |
| Serial command menu | Incident IDs, latching states |
| Non-blocking `millis()` self-test | `BOOT`/`SAFE`/`ALARM_LATCHED`/… state machine |
| Runtime active-high/active-low | Silence / Acknowledge / Reset commands |

> **No claim of working hardware is made here.** Nothing in this repository
> asserts that any LED or the buzzer actually operates until a human connects the
> board, runs the tests in `docs/hardware_bringup.md`, and reports the observed
> behaviour.

---

## Ownership split (unchanged from the plan)

The ESP32 will own the **latched** alarm state. The laptop proposes; the ESP32
decides and holds. That is Phase 4/5 behaviour and is **not** in this sketch —
Phase 2A only proves the outputs can be driven.

---

## Files

| File | Committed | Purpose |
|---|---|---|
| `esp32_eye_shield_monitor.ino` | yes | Self-test firmware |
| `config.example.h` | yes | Pin, polarity, tone and baud defaults. No secrets. |
| `command_table.h` | yes | `CommandEntry` struct used by the serial dispatcher. |
| `config.h` | **no — git-ignored** | Your machine-specific overrides. Never commit. |
| `README.md` | yes | This file |

`config.h` is git-ignored by `.gitignore` line 20. It is the file that will hold
Wi-Fi and Blynk credentials in Phase 5, so the ignore rule must stay in place.

The sketch uses `__has_include("config.h")` and falls back to
`config.example.h` when the local file is absent, so a fresh clone compiles
unmodified.

### Why `command_table.h` exists

The Arduino builder generates a prototype for every sketch function and pastes
them all into one block near the top of the translation unit, *before* any
sketch definition. `findOutputCommand()` returns a `const CommandEntry *`, so
that generated block needs the type to already be in scope.

Defining the struct in the `.ino` does **not** work — not even with a forward
declaration, because the forward declaration also lands after the injected
block. The build fails with:

```text
error: 'CommandEntry' does not name a type
```

Keeping the struct in a header sidesteps this: the `#include` is processed
before the generated prototypes. Any future type that appears in a sketch
function signature must follow the same rule.

---

## Quick start

1. Wire the board exactly as in `docs/hardware_bringup.md` §2.
2. Open `esp32_eye_shield_monitor.ino` in the Arduino IDE.
3. Select **ESP32S3 Dev Module**, enable **USB CDC On Boot**, pick your port.
4. Upload.
5. Open Serial Monitor at **115200 baud**, line ending **Newline**.
6. Type `SELF_TEST` and watch. Then test each output individually.

Full detail — board settings, port identification, upload, wiring table,
expected behaviour, and the physical test script — is in
[`docs/hardware_bringup.md`](../../docs/hardware_bringup.md).

---

## Serial commands

| Command | Effect | Expected reply |
|---|---|---|
| `GREEN_ON` | Green LED on. Red and buzzer unchanged. | `OK GREEN_ON` |
| `GREEN_OFF` | Green LED off. | `OK GREEN_OFF` |
| `RED_ON` | Red LED on. Green and buzzer unchanged. | `OK RED_ON` |
| `RED_OFF` | Red LED off. | `OK RED_OFF` |
| `BUZZER_ON` | Buzzer tone at `BUZZER_TONE_HZ`. LEDs unchanged. | `OK BUZZER_ON` |
| `BUZZER_OFF` | Buzzer silent. LEDs unchanged. | `OK BUZZER_OFF` |
| `ALL_OFF` | Every output off. | `OK ALL_OFF` |
| `SELF_TEST` | Run the 8-step non-blocking sequence. | `SELF_TEST started.` then `STEP n/8: …` per step |
| `STATUS` | Report pin map, polarity and live state. | `---- STATUS ----` block |
| `HELP` or `?` | Print the command list. | command list |

Notes:

- Commands are case-insensitive; `green_on` works.
- Any manual output command aborts a running `SELF_TEST` and reports
  `SELF_TEST aborted (manual command). All outputs OFF.`
- Unknown input returns `ERROR unknown command: <TEXT>` and does not change any
  output.

### `STATUS` reply format

```text
---------------- STATUS ----------------
board firmware  : Phase 2A bring-up (self-test only)
config.h        : defaults from config.example.h
PIN_LED_GREEN   : GPIO11  polarity active-HIGH  state OFF
PIN_LED_RED     : GPIO12  polarity active-HIGH  state OFF
PIN_BUZZER      : GPIO14  polarity active-HIGH  tone 2700 Hz @ 50%  state OFF
SELF_TEST       : idle
uptime_ms       : 12345
----------------------------------------
```

`state` is the **logical** state (energised or not), not the raw pin voltage.
This is deliberate: if a module is inverted, the command reports `state ON`
while the lamp stays dark, which localises the fault immediately.

---

## Polarity

Every output has its own compile-time polarity switch in `config.example.h`:

```c
#define GREEN_ACTIVE_HIGH 1   /* 1 = HIGH turns it ON, 0 = LOW turns it ON */
#define RED_ACTIVE_HIGH 1
#define BUZZER_ACTIVE_HIGH 1
```

Defaults are active-HIGH. If `SELF_TEST` shows an output on when it should be
off (and off when it should be on), set that output's flag to `0` and re-upload.
The inversion is applied in exactly one place — `writeActiveLevel()` — so no
other code changes.

---

## Non-blocking design

`loop()` never blocks:

- serial input is polled with `Serial.available()` and accumulated into a line
  buffer;
- the self-test advances on `millis()` deltas, one step per
  `SELF_TEST_STEP_MS`;
- there is **no `delay()` in the runtime path**. The only bounded wait is the
  ≤2 s USB-CDC enumeration spin in `setup()`, which cannot be avoided on native
  USB boards and runs once at boot.

Elapsed-time comparisons use unsigned subtraction (`millis() - mark`) so they
stay correct across the 49-day `millis()` rollover.

---

## Passive buzzer drive

A passive piezo has no internal oscillator, so it needs a **square wave**, not a
DC level. The sketch drives it with LEDC PWM at `BUZZER_TONE_HZ` and
`BUZZER_DUTY_PERCENT`.

The Arduino-ESP32 core changed the LEDC API in 3.x, so both are supported behind
an `ESP_ARDUINO_VERSION_MAJOR` check:

- core 3.x — `ledcAttach(pin, freq, res)` / `ledcWrite(pin, duty)` / `ledcDetach(pin)`;
- core 2.x — `ledcSetup(ch, freq, res)` / `ledcAttachPin(pin, ch)` /
  `ledcWrite(ch, duty)` / `ledcDetachPin(pin)`.

Silencing detaches the pin from the LEDC peripheral **before** parking it at its
inactive GPIO level. Writing `digitalWrite()` to a pin still owned by the LEDC
mux does not reliably change the level.

---

## Verified so far

| Check | Result |
|---|---|
| Compiles for `esp32:esp32:esp32s3`, ESP32 core 3.3.11 | **pass** — 314,825 bytes (24%) flash, 22,296 bytes (6%) RAM |
| Compiles with `--warnings all` | **pass** — no warnings |
| Compiles with a local `config.h` override | **pass** |
| `config.h` is git-ignored | **pass** — `git check-ignore` resolves to `.gitignore:20` |
| Any physical output verified | **NO — awaiting user hardware test** |

Compilation is the only evidence recorded here. No LED, buzzer, pin voltage, or
current measurement has been observed.
