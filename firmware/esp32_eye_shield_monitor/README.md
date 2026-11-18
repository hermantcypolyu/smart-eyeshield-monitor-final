# ESP32-S3 Eye-Shield Monitor — Firmware

**Status: Phase 2A hardware bring-up. Firmware verified on real hardware
2026-11-18 — green (GPIO11), red (GPIO12) and buzzer (GPIO14) all physically
observed. SELF_TEST and STOP_TEST remain the only unverified Phase 2A items.**

This is the mandatory Arduino sketch required by `plan.md` §"Phase 2A". It is
real firmware, not a placeholder and not a wiring diagram. It drives the three
physical outputs, exposes a line-oriented Serial Monitor command interface, and
runs a non-blocking self-test. It is the same sketch that Phases 4 and 5 will
upgrade in place — there is deliberately no second sketch.

> **Scope of the claim.** Green, red and buzzer were observed working on the
> real board, as were `SAFE`, `ALARM`, `SILENCE` and `ALL_OFF`. `SELF_TEST` and
> `STOP_TEST` have **not** been run on hardware yet, and nothing here asserts
> that they work. See `docs/hardware_bringup.md` §9 for exactly what was and was
> not observed.

---

## Scope

| In Phase 2A (this sketch) | Deliberately out of scope |
|---|---|
| Green output on GPIO11 | Blynk / Wi-Fi / credentials |
| Red output on GPIO12 | YOLO / camera integration |
| Buzzer output on GPIO14 (passive PWM **or** active DC) | Final JSON incident protocol |
| 14-command Serial Monitor interface | Latched alarm state machine |
| Non-blocking `millis()` self-test | Incident IDs, acknowledgement, reset |
| Centralised active-high / active-low configuration | 7B models, cloud inference |

`SAFE`, `ALARM` and `SILENCE` in this sketch are **bring-up presets that drive
the pins**. They are not the Phase 4 latched state machine, and they do not
survive a power cycle.

## Ownership split (Phase 4/5, not Phase 2A)

The ESP32 will own the **latched** alarm state. The laptop proposes; the ESP32
decides and holds. Phase 2A only proves that the outputs can be driven at all.

---

## Files

| File | Committed | Purpose |
|---|---|---|
| `esp32_eye_shield_monitor.ino` | yes | Self-test firmware |
| `config.example.h` | yes | Pin, polarity, buzzer and baud defaults. No secrets. |
| `README.md` | yes | This file |
| `config.h` | **no — git-ignored** | Machine-specific overrides. Never commit. |
| `build/` | **no — git-ignored** | Compiler output |

`config.h` is ignored by `.gitignore` line 20 and `firmware/**/build/` by line
102. `config.h` is the file that will hold Wi-Fi and Blynk credentials in Phase
5, so the ignore rule must stay in place. **During Phase 2A `config.h` must not
contain any credentials.**

The sketch uses `__has_include("config.h")` and falls back to `config.example.h`
when the local file is absent, so a fresh clone always compiles unmodified.

### Why there is no extra header file

The Arduino builder generates a prototype for every sketch function and pastes
them all into one block near the top of the translation unit, *before any
definition*. A function whose signature names a custom struct therefore fails
with `'CommandEntry' does not name a type`, and adding a forward declaration in
the `.ino` does not help because that also lands after the injected block.

The sketch avoids this by having `findCommand()` return a plain `uint8_t` index
into the command table instead of a `const CommandEntry *`. No extra header is
needed, which keeps the firmware to the file set that `plan.md` mandates.

---

## Configuration

All options live in `config.example.h` (or your local `config.h`). Nothing is
hard-coded in the sketch.

| Macro | Verified value | Meaning |
|---|---|---|
| `GREEN_LED_PIN` | `11` | Green traffic-light input pin |
| `RED_LED_PIN` | `12` | Red traffic-light input pin |
| `BUZZER_PIN` | `14` | Buzzer signal pin |
| `GREEN_LED_ACTIVE_HIGH` | `1` — **measured** | `1` = active-high, `0` = active-low |
| `RED_LED_ACTIVE_HIGH` | `1` — **measured** | `1` = active-high, `0` = active-low |
| `BUZZER_ACTIVE_HIGH` | `0` — **measured** | `1` = active-high, `0` = active-low |
| `BUZZER_IS_PASSIVE` | `0` — **measured** | `1` = passive piezo (PWM tone), `0` = active buzzer (DC) |
| `BUZZER_FREQUENCY_HZ` | `2000` | Tone frequency for passive buzzers |
| `BUZZER_PWM_RESOLUTION_BITS` | `10` | LEDC resolution |
| `BUZZER_DUTY_PERCENT` | `50` | Duty cycle, keeps volume reasonable |
| `BUZZER_LEDC_CHANNEL` | `0` | LEDC channel (only used by ESP32 core 2.x) |
| `SERIAL_BAUD_RATE` | `115200` | Serial Monitor baud rate |
| `SELF_TEST_STEP_MS` | `500` | Dwell time per self-test step |

**These values are measured, not assumed.** The traffic light and the buzzer
differ in polarity on this hardware: the two lamps are active-HIGH, while the
buzzer module is LOW-level trigger (`BUZZER_ACTIVE_HIGH 0`). That is why each
output has its own flag. The buzzer is an **active** type, so
`BUZZER_FREQUENCY_HZ` is ignored and the tone settings are dead code kept only
for a future passive-piezo swap.

If you replace a module and an output behaves inverted, change the matching
`*_ACTIVE_HIGH` in `config.h` — do not rewire the board to compensate.

---

## Serial Monitor commands

Open Serial Monitor at **115200 baud**, line ending **Newline** or **CRLF**
(both are accepted; CRLF does not fire a command twice).

| Command | Green | Red | Buzzer | Notes |
|---|---:|---:|---:|---|
| `SAFE` | On | Off | Off | Normal compliant state |
| `ALARM` | Off | On | On | Hardware alarm test only |
| `SILENCE` | Off | On | Off | Alarm stays visually active |
| `GREEN_ON` | On | Off | unchanged | Green/red are mutually exclusive |
| `GREEN_OFF` | Off | unchanged | unchanged | |
| `RED_ON` | Off | On | unchanged | Green/red are mutually exclusive |
| `RED_OFF` | unchanged | Off | unchanged | |
| `BUZZER_ON` | unchanged | unchanged | On | |
| `BUZZER_OFF` | unchanged | unchanged | Off | Stops tone immediately |
| `ALL_OFF` | Off | Off | Off | Bring-up safety state |
| `SELF_TEST` | sequenced | sequenced | sequenced | Non-blocking, 8 steps |
| `STOP_TEST` | Off | Off | Off | Immediate cancellation |
| `STATUS` | — | — | — | Full state report, read-only |
| `HELP` (`?`) | — | — | — | Command list, read-only |

Every accepted command replies with `OK <COMMAND>`. Unknown input replies with
`ERROR unknown command: <text>` followed by a `HELP` hint, and **leaves the
outputs unchanged**.

`STATUS` and `HELP` never disturb a running self-test. Any command that changes
an output cancels a running self-test first, so the sequence can never fight a
human command.

### `STATUS` reports

- green, red and buzzer state;
- configured active polarity for each output;
- buzzer mode (passive PWM / active DC) and frequency;
- self-test active/inactive (with the current step number);
- uptime in milliseconds;
- which config file is in effect.

### `SELF_TEST` sequence

Eight steps, `SELF_TEST_STEP_MS` apart, driven by `millis()`:

1. Green ON
2. Green OFF
3. Red ON
4. Red OFF
5. Buzzer ON
6. Buzzer OFF
7. Red + buzzer ON
8. All OFF — sequence ends here

Each step prints `STEP n/8: <what to expect>`. `STOP_TEST` interrupts at any
point and forces all outputs off.

---

## Timing discipline

`loop()` never blocks. Serial input is polled, the self-test advances on
`millis()`, and there is **no `delay()` in the runtime path**. The only wait is a
bounded 2000 ms startup wait for native USB CDC enumeration in `setup()`, which
prevents the boot banner being lost. Short self-test dwell times exist solely so
the human watching the LEDs can see each step.

## Buzzer handling

- **Passive piezo** (`BUZZER_IS_PASSIVE 1`) — driven by an LEDC square wave at
  `BUZZER_FREQUENCY_HZ`. Silent by writing duty 0.
- **Active buzzer** (`BUZZER_IS_PASSIVE 0`) — driven by a static DC level. The
  frequency macro is ignored.

The pin is detached from the LEDC peripheral before being parked at its inactive
level. `digitalWrite()` on a pin still routed to LEDC does not do what it looks
like, because the peripheral owns the pin.

Both Arduino-ESP32 core 2.x and 3.x LEDC APIs are supported behind
`ESP_ARDUINO_VERSION_MAJOR` (3.x attaches by pin, 2.x attaches by channel).

---

## Compile

Verified locally with Arduino CLI 1.5.1 and Arduino-ESP32 core **3.3.11**:

```
"/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli" \
  compile --fqbn esp32:esp32:esp32s3 --warnings all \
  firmware/esp32_eye_shield_monitor --output-dir /tmp/build
```

Result with the verified configuration (`BUZZER_IS_PASSIVE 0`, buzzer active-low):
**compiles clean, no warnings**, 308,594 bytes (23%) of program storage,
22,176 bytes (6%) of dynamic memory.

## Upload

**Upload speed 115200.** 921600 was unstable on this board and failed; 115200
uploaded successfully and passed flash hash verification. Set it in
**Tools → Upload Speed**.

Also: **Tools → USB CDC On Boot → Enabled**, and Serial Monitor at 115200 with
line ending Newline or CRLF.

The board appeared as `/dev/cu.usbserial-10` — a UART-bridge device path, not a
native-USB `usbmodem` path.

See `docs/hardware_bringup.md` for the wiring table, the full IDE settings, the
upload procedure, and the mandatory 14-step physical test order.
