# Hardware Bring-Up — Phase 2A

**Project:** EIE3127 Smart Biohazard & Laser Lab Eye-Shield Monitor
**Phase:** 2A — Mandatory ESP32-S3 Firmware and Hardware Bring-Up
**Host:** MacBook, macOS, Arduino IDE
**Firmware:** `firmware/esp32_eye_shield_monitor/`

---

## 0. Status — read this first

**Phase 2A has NOT passed.**

| Evidence | Status |
|---|---|
| Firmware written | **yes** — real, compiling Arduino sketch |
| Compile attempted | **yes** — Arduino CLI 1.5.1, `esp32:esp32:esp32s3`, core 3.3.11 |
| Compile result | **success, zero warnings** — 316,337 B (24%) flash, 22,296 B (6%) RAM |
| Upload attempted | **no** — no ESP32 board is attached to this Mac |
| Serial commands exercised on hardware | **no** |
| GPIO11 green physically observed | **no** |
| GPIO12 red physically observed | **no** |
| GPIO14 buzzer physically observed | **no** |

Nothing below claims that any output works. Phase 2A passes only when §9 is
filled in from real hardware observations you supply.

| In scope | Out of scope (later phases) |
|---|---|
| Board identification and IDE configuration | Blynk / Wi-Fi / credentials (Phase 5) |
| macOS serial-port identification | YOLO / camera integration (Phase 2B–3) |
| Working firmware with 14 serial commands | Final JSON incident protocol (Phase 4) |
| Green output GPIO11 independent test | Latched alarm state machine (Phase 4/5) |
| Red output GPIO12 independent test | Incident IDs, acknowledge, reset |
| Buzzer GPIO14 passive-PWM **and** active-DC test | Any 7B model |
| Red + buzzer combined test | |
| Non-blocking `millis()` timing | |
| Configurable active-high / active-low outputs | |

---

## 1. Board identification and Arduino IDE configuration

### 1.1 What the board is

The hardware is an **ESP32-S3 UNO development board** — an UNO-form-factor
carrier for an Espressif **ESP32-S3** SoC. It is *not* an Arduino UNO (ATmega328P)
and must never be configured as one.

### 1.2 There is no "ESP32-S3 UNO" board entry

Verified against the installed core: `arduino-cli board listall` returns no board
named "ESP32-S3 UNO". The correct generic target, and the one this firmware was
compiled for, is:

```
ESP32S3 Dev Module          esp32:esp32:esp32s3
```

UNO-form-factor ESP32-S3 carriers do not ship a separate board definition in the
official `esp32` core. If your board's vendor supplies its own board package with a
more precise profile, prefer theirs and record it in §9.

### 1.3 Board manager prerequisites

Verified present on this machine:

| Component | Installed version |
|---|---|
| Arduino IDE | `/Applications/Arduino IDE.app` |
| `arduino-cli` (bundled with the IDE) | **1.5.1** |
| `esp32` core (`esp32:esp32`) | **3.3.11** |
| Arduino-ESP32 core path | `~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.11` |

If the core is missing, install it once: **Arduino IDE → Settings → Additional
boards manager URLs**, add

```
https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

then **Tools → Board → Boards Manager**, search `esp32`, install **esp32 by
Espressif Systems**.

### 1.4 Exact IDE settings

| IDE field | Value | Why |
|---|---|---|
| **Board** | `ESP32S3 Dev Module` | Only generic S3 target in the official core |
| **Port** | your board's `/dev/cu.*` device — see §3 | |
| **USB CDC On Boot** | **Enabled** | Exposes `Serial` over native USB so Serial Monitor works |
| **CPU Frequency** | `240MHz (WiFi)` | Default; irrelevant to bring-up |
| **Flash Mode** | `QIO 80MHz` | Default for most S3 modules |
| **Flash Size** | match the module (commonly `4MB`) | Wrong size can stop the board booting |
| **PSRAM** | `Disabled` unless your module has PSRAM | This sketch needs no PSRAM |
| **Partition Scheme** | `Default 4MB with spiffs` | Any scheme with ≥1.3 MB app space works |
| **Upload Speed** | `921600` | Drop to `115200` if uploads fail |
| **Upload Mode** | `UART0 / Hardware CDC` | Use `USB-OTG CDC (TinyUSB)` if the UART path is not wired |
| **Core Debug Level** | `None` | |
| **Erase All Flash Before Sketch Upload** | `Disabled` | |
| **USB Mode** | `Hardware CDC and JTAG` | Default for the generic S3 target |

Serial baud rate in both firmware and Serial Monitor: **115200** (`SERIAL_BAUD_RATE`).

> **`USB CDC On Boot` is the single most common failure.** If it is `Disabled`,
> the sketch runs but the Serial Monitor stays silent and every command appears to
> do nothing. If you see no banner, check this first.

### 1.5 Upload mode and the two USB ports

Many ESP32-S3 UNO boards expose **two** USB-C ports:

| Port | Silkscreen | Chip behind it | Device appears as |
|---|---|---|---|
| Native USB | `USB` / `USB-OTG` / `COM` | ESP32-S3 internal USB (GPIO19/20) | `/dev/cu.usbmodem*` |
| UART bridge | `UART` / `COM` | CH340, CH343, or CP210x | `/dev/cu.wchusbserial*`, `/dev/cu.usbserial-*`, `/dev/cu.SLAB_USBtoUART*` |

Either works. With the **native USB** port, *USB CDC On Boot: Enabled* is
mandatory. With the **UART bridge** port, `Serial` is still routed there and the
setting is harmless.

**Assumption to confirm:** the port layout and bridge chip of your specific board
is unknown. §3 gives the command that reveals it.

---

## 2. Wiring

### 2.1 Pin assignment (from `plan.md` v2, fixed)

| Function | ESP32-S3 pin |
|---|---:|
| Green traffic-light input | **GPIO11** |
| Red traffic-light input | **GPIO12** |
| Passive/active buzzer signal | **GPIO14** |
| Module ground | **GND** |
| Buzzer module supply | **3V3 only after confirming 3.3 V support** |
| Yellow traffic-light input | **Not connected** |

These three pins are general-purpose on the ESP32-S3 and are **not** used by the
SPI flash (GPIO26–32) or the native USB PHY (GPIO19/20), so they are safe choices.

### 2.2 Wiring table — traffic-light module board (assumed hardware)

The traffic-light wiring assumes a **common-cathode / common-GND** module. Check
the real PCB labels before power-on.

| From | To | Notes |
|---|---|---|
| ESP32-S3 **GPIO11** | module **G** (green input) | |
| ESP32-S3 **GPIO12** | module **R** (red input) | |
| ESP32-S3 **GND** | module **GND** | common ground — **required** |
| module **VCC** | ESP32-S3 **3V3** *only after confirming 3.3 V support* | see §8.2 |
| ESP32-S3 **GPIO14** | buzzer **signal / +** | see §8.3 first |
| buzzer **GND / −** | ESP32-S3 **GND** | |
| — | module **Y** (yellow input) | **leave unconnected** |

**Do not connect module VCC to a GPIO.** VCC goes to a supply rail only.

### 2.3 Wiring table — bare LEDs (if you are not using a module)

**A series resistor is mandatory**; connecting an LED directly to a GPIO can
destroy both.

| From | To | Component |
|---|---|---|
| ESP32-S3 **GPIO11** | green LED **anode** (long leg) | 220 Ω series resistor |
| green LED **cathode** (short leg) | ESP32-S3 **GND** | — |
| ESP32-S3 **GPIO12** | red LED **anode** | 220 Ω series resistor |
| red LED **cathode** | ESP32-S3 **GND** | — |
| ESP32-S3 **GPIO14** | passive piezo **+** | — (see §8.3) |
| passive piezo **−** | ESP32-S3 **GND** | — |

With 220 Ω at 3.3 V: red draws ≈6 mA, green ≈1–4 mA depending on forward voltage.
Both are within the ESP32-S3 GPIO rating.

### 2.4 Common ground

All outputs and the ESP32-S3 must share one ground. Without it the signals float
and the outputs behave randomly — the second most common bring-up failure after
`USB CDC On Boot`.

---

## 3. Serial-port identification on macOS

**No ESP32 board was attached to this Mac while this document was written.** Only
Bluetooth pseudo-ports were present, so the real device path is unknown and is
**not guessed** here. You must record it in §9.

### 3.1 Find the port

```bash
ls /dev/cu.*
```

With no board attached you will typically see only:

```
/dev/cu.Bluetooth-Incoming-Port
/dev/cu.debug-console
```

Plug the board in with a **data** USB cable (many cables are charge-only) and run
it again. A new entry appears:

| Pattern | Meaning |
|---|---|
| `/dev/cu.usbmodem1101` (or `…14101`, `…201`) | native USB CDC |
| `/dev/cu.wchusbserial110` | CH340/CH343 bridge |
| `/dev/cu.usbserial-0001` | CP210x bridge |
| `/dev/cu.SLAB_USBtoUART` | CP210x, Silicon Labs driver |

### 3.2 Confirm it is the board, not something else

```bash
system_profiler SPUSBDataType | grep -iA 8 -e "esp32" -e "usbmodem" -e "ch34" -e "cp210"
```

A match names the vendor/product. If nothing matches, the cable is charge-only or
the board has no power.

### 3.3 Port does not appear — checklist

1. Swap the USB cable (charge-only cables are extremely common).
2. Try the board's **other** USB port.
3. Hold **BOOT**, tap **RST**, release **BOOT** to force download mode.
4. Re-run `system_profiler SPUSBDataType` and look for any new device at all.
5. Install the vendor driver if the board uses a CH34x bridge and nothing appears.

Record the port you found in §9.

---

## 4. Required Arduino libraries

**None.** The sketch uses only what ships with the `esp32` core.

| Library | Provided by | Used for |
|---|---|---|
| `Arduino.h` | `esp32` core | `Serial`, `pinMode`, `digitalWrite`, `millis` |
| LEDC API (`ledcAttach`/`ledcWrite`/`ledcDetach`) | `esp32` core | passive buzzer square wave |

No `Tone.h`, no `ESP32Servo`, no Blynk, no external library. If the IDE offers to
install a library for `Arduino.h`, decline — it means the board is not set to an
ESP32 target.

---

## 5. Upload procedure

1. Connect the board with a **data** USB cable.
2. Arduino IDE → **Tools → Board → esp32 → ESP32S3 Dev Module**.
3. **Tools → USB CDC On Boot → Enabled**.
4. **Tools → Port →** the `/dev/cu.*` device from §3.
5. Open `firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino`.
   The IDE must show `config.example.h` as a second tab; if it does not, you
   opened a single file instead of the sketch folder.
6. Click **Upload** (→ arrow).
7. Watch the console. Success ends with something like:

```
Writing at 0x00010000... (100 %)
Wrote 316337 bytes ... Hash of data verified.
Leaving...
Hard resetting via RTS pin...
```

8. Open **Tools → Serial Monitor**, set **115200 baud** and line ending
   **Newline** (or **Both NL & CR** — the firmware accepts LF and CRLF, and a
   CRLF does not fire a command twice).
9. Press the board's **RST** button. You should see:

```
==============================================================
 ESP32-S3 EYE-SHIELD MONITOR - PHASE 2A HARDWARE BRING-UP
 Working self-test firmware. Not the final product firmware:
 no Blynk, no Wi-Fi, no YOLO, no JSON protocol yet.
==============================================================
config          : defaults from config.example.h
pin mapping     :
  green LED     : GPIO11
  red LED       : GPIO12
  buzzer        : GPIO14
polarity        : green active-HIGH, red active-HIGH, buzzer active-HIGH
buzzer mode     : PASSIVE (PWM tone) @ 2000 Hz
serial baud     : 115200
--------------------------------------------------------------
!! UNVERIFIED HARDWARE !!
...
Ready. Type a command and press Enter.
```

**No banner?** In order: (a) `USB CDC On Boot` not Enabled, (b) wrong port,
(c) Serial Monitor baud not 115200, (d) board not reset after upload,
(e) wrong line ending so commands never terminate.

### 5.1 Compile-only check without the IDE (verified working)

```bash
"/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli" \
  compile --fqbn esp32:esp32:esp32s3 --warnings all \
  firmware/esp32_eye_shield_monitor --output-dir /tmp/build
```

Verified output:

```
Sketch uses 316337 bytes (24%) of program storage space. Maximum is 1310720 bytes.
Global variables use 22296 bytes (6%) of dynamic memory, leaving 305384 bytes for local variables. Maximum is 327680 bytes.
```

No warnings at `--warnings all`. The alternative configuration path (active-low,
active buzzer, `config.h` override) was also compiled: 308,570 B (23%) flash,
22,176 B (6%) RAM, also warning-free.

---

## 6. Required physical test order

**Follow this exact order from `plan.md` v2.** Each step is deliberately
incremental so a wiring fault cannot damage something already proven.

| # | Action | Pass condition |
|---|---|---|
| **1** | Upload the firmware with **no external modules connected** | Upload succeeds; banner prints |
| **2** | Confirm the boot banner and send `STATUS` | Banner lists GPIO11/12/14, polarity, buzzer mode; `STATUS` shows all three outputs `OFF`, `self test: inactive` |
| **3** | **Disconnect USB power** | Board is fully unpowered before touching wiring |
| **4** | Connect **only** traffic-light **GND** and **green input** to GPIO11 | Nothing else connected |
| **5** | Reconnect USB and test `GREEN_ON` then `GREEN_OFF` | Green lamp lights on, goes dark off; red stays dark; buzzer silent; `OK GREEN_ON` / `OK GREEN_OFF` |
| **6** | **Disconnect USB power** | Unpowered before adding wiring |
| **7** | Add the **red input** to GPIO12, reconnect, test `RED_ON` then `RED_OFF` | Red lamp lights on, goes dark off; green stays dark; buzzer silent |
| **8** | Test `SAFE` and `ALARM` **without the buzzer connected** | `SAFE` → green on only. `ALARM` → red on only (no buzzer reply yet). `SILENCE` → red on only. `ALL_OFF` → all dark |
| **9** | **Disconnect USB power** | Unpowered before touching the buzzer |
| **10** | Confirm the buzzer's **PCB labels**, **active/passive type**, **voltage** and **current** | You have read the part number or measured the module. Do not proceed on assumption |
| **11** | Connect the **three-pin buzzer module** only if **3.3 V operation is supported** | If 5 V-only, stop and report — do not connect to 3V3 |
| **12** | Test `BUZZER_ON` **briefly**, then `BUZZER_OFF`, then `ALL_OFF` | Tone on, tone off, everything off |
| **13** | Test `SELF_TEST`, and interrupt it once with `STOP_TEST` mid-sequence | 8 steps run in order; `STOP_TEST` aborts immediately and silences everything |
| **14** | **Return the observed behaviour** (§9) | Phase 2A can then be judged |

### 6.1 Supplementary checks (do these once the ordered tests pass)

| # | Action | Pass condition |
|---|---|---|
| S1 | `SELF_TEST` and let it finish | 8 steps, ≈0.5 s each, ends `SELF_TEST complete. All outputs OFF.` |
| S2 | Type `BOGUS` | `ERROR unknown command: BOGUS`, outputs unchanged |
| S3 | Type `green_on` in lowercase | Treated as `GREEN_ON`; reply `OK GREEN_ON` |
| S4 | During `SELF_TEST`, send `STATUS` | Status block prints with `self test: ACTIVE at step n/8`; sequence keeps running |
| S5 | `GREEN_ON` then `RED_ON` | Green goes dark as red lights (mutually exclusive); `STATUS` shows red ON, green OFF |
| S6 | Multimeter: GPIO11, GPIO12, GPIO14 to GND, on and off | ≈3.3 V on, ≈0 V off (inverted if you set `*_ACTIVE_HIGH 0`) |

### 6.2 If an output is inverted

Symptom: `GREEN_ON` is acknowledged but the green lamp goes **dark**, or a lamp is
lit while `STATUS` says `OFF`.

That output is active-low. Fix in configuration — **do not rewire to compensate**:

```bash
cd firmware/esp32_eye_shield_monitor
cp config.example.h config.h
```

In `config.h` set the matching flag to `0`:

```c
#define GREEN_LED_ACTIVE_HIGH 0
```

Re-upload and repeat step 5 (or 7). `config.h` is git-ignored — never commit it,
and during Phase 2A it must contain **no credentials**.

### 6.3 If you have an active buzzer instead of a passive one

A passive piezo needs a square wave; an active buzzer has its own oscillator and
needs only a DC level. If your buzzer sounds a steady tone that does **not**
change with `BUZZER_ON`/`BUZZER_OFF`, or sounds wrong at 2000 Hz, set in
`config.h`:

```c
#define BUZZER_IS_PASSIVE 0
```

`BUZZER_FREQUENCY_HZ` is then ignored. Both branches are compiled and verified to
build; neither has been exercised on hardware.

---

## 7. Expected LED and buzzer behaviour

### 7.1 Per command

| Command | Green | Red | Buzzer | Serial reply |
|---|---|---|---|---|
| `GREEN_ON` | **ON** | off | unchanged | `OK GREEN_ON` |
| `GREEN_OFF` | **OFF** | unchanged | unchanged | `OK GREEN_OFF` |
| `RED_ON` | off | **ON** | unchanged | `OK RED_ON` |
| `RED_OFF` | unchanged | **OFF** | unchanged | `OK RED_OFF` |
| `BUZZER_ON` | unchanged | unchanged | **tone** | `OK BUZZER_ON` |
| `BUZZER_OFF` | unchanged | unchanged | **silent** | `OK BUZZER_OFF` |
| `ALL_OFF` | **OFF** | **OFF** | **silent** | `OK ALL_OFF` |
| `SAFE` | **ON** | off | **silent** | `OK SAFE` |
| `ALARM` | off | **ON** | **tone** | `OK ALARM` |
| `SILENCE` | off | **ON** | **silent** | `OK SILENCE` |
| `STOP_TEST` | **OFF** | **OFF** | **silent** | `OK STOP_TEST` |
| `STATUS` | — | — | — | status block |
| `HELP` | — | — | — | command list |
| `SELF_TEST` | sequenced | sequenced | sequenced | `SELF_TEST started…` |

Green and red are **mutually exclusive**: `GREEN_ON` forces red off and `RED_ON`
forces green off. `applyOutputs()` is the single enforcement point and prints
`FAULT: green and red requested together; red forced OFF.` if a future edit ever
asks for both.

### 7.2 `SELF_TEST` sequence (8 steps, non-blocking)

| Step | Green | Red | Buzzer | What you should see and hear |
|---|---|---|---|---|
| 1/8 | ON | off | off | Green lamp only |
| 2/8 | off | off | off | Everything dark and silent |
| 3/8 | off | ON | off | Red lamp only |
| 4/8 | off | off | off | Everything dark and silent |
| 5/8 | off | off | **on** | Tone only, both lamps dark |
| 6/8 | off | off | off | Silence, both lamps dark |
| 7/8 | off | ON | **on** | Red lamp lit **and** tone together |
| 8/8 | off | off | off | Everything off — sequence complete |

Total ≈ 8 × 500 ms = **4 seconds**. No step should take noticeably longer, and the
Serial Monitor must stay responsive throughout — that is the observable proof that
`loop()` is non-blocking.

### 7.3 What the buzzer should sound like

A steady, continuous tone at **2000 Hz** — high-pitched but not a click, and not a
low buzz. A passive piezo driven with DC alone clicks once; a sustained tone proves
the PWM path works. A faint tone is normal for a bare piezo at 3.3 V and is
sufficient for a prototype.

---

## 8. Electrical risks and unresolved assumptions

### 8.1 GPIO current limits

ESP32-S3 GPIO is **3.3 V logic**. Recommended source current is ~20 mA per pin;
~40 mA is the absolute maximum. **Never connect an LED without a series resistor**,
and never drive a magnetic buzzer or lamp directly from a pin.

### 8.2 Module supply voltage — UNRESOLVED, must be checked

Traffic-light modules are sold in **3.3 V and 5 V** variants and the silkscreen is
often the only difference. **Check your module before connecting VCC.**

- `plan.md` v2 requires: buzzer module supply **3V3 only after confirming 3.3 V
  support**.
- If the module is rated 5 V, power it from the board's `5V`/`VBUS` pin, not
  `3V3`, or it will be dim or dead.
- A 5 V module's **inputs** must still tolerate 3.3 V logic. Most do.
- **Never** feed 5 V into a GPIO.

If the module's VCC is 5 V and its inputs are pulled to 5 V, a level shifter is
required. This is unverified for your specific module.

### 8.3 Buzzer type and driver — UNRESOLVED, must be checked

`plan.md` v2 states: a bare two-pin buzzer must not be treated as a three-pin
module and **may require a transistor driver**.

- A **bare passive piezo** draws ~1–5 mA and can be driven from GPIO14 directly.
  This is the assumed case (`BUZZER_IS_PASSIVE 1`).
- A **magnetic passive buzzer** can draw 20–30 mA — at the top of the GPIO rating
  and marginal. Use a transistor driver module.
- An **active** buzzer has an internal oscillator: set `BUZZER_IS_PASSIVE 0`.
  It cannot be frequency-controlled, so it is less suitable for the product's
  distinct alarm patterns, but it is acceptable for Phase 2A bring-up.
- A **three-pin module** usually has VCC / GND / SIG or I/O. Confirm the label
  meanings before wiring; a two-pin bare buzzer must not be wired as if the third
  pin existed.

### 8.4 Other risks

| Risk | Mitigation |
|---|---|
| Wrong `Flash Size` → board will not boot | Set it to match the module before uploading |
| Upload fails at 921600 | Drop Upload Speed to 115200 |
| Board not detected | Hold BOOT, tap RST, release BOOT |
| Missing common ground → erratic outputs | Verify with a continuity check |
| 5 V into a GPIO | Verify module VCC rating first (§8.2) |
| ESP32-S3 pin reuse | GPIO11/12/14 are free; do **not** move outputs onto GPIO26–32 (SPI flash) or GPIO19/20 (native USB) |
| `config.h` accidentally committed | Git-ignored; verify with `git check-ignore -v` before `git add` |

### 8.5 Assumptions to confirm against your hardware

1. The board exposes GPIO11, GPIO12 and GPIO14 on its headers under those exact
   numbers. UNO-form-factor carriers sometimes relabel pins — check the pinout
   silkscreen.
2. The modules are **active-high**. This is the `config.example.h` default; flip
   the matching `*_ACTIVE_HIGH` to `0` if inverted (§6.2).
3. The buzzer is **passive** and 3.3 V-compatible (§8.3).
4. Module VCC is 3.3 V-compatible, or is powered from the 5 V rail (§8.2).
5. The USB cable is a **data** cable.
6. The traffic light is a **common-cathode / common-GND** module.
7. GPIO14 can source the buzzer current without a driver (§8.3).

---

## 9. Observation record — to be completed by the human

Fill this in and return it. **Phase 2A cannot be marked passed until it is
complete.** Do not report "it works" without the specific per-step observations.

### 9.1 Ordered tests (§6)

| # | Test | Observed result | Pass? |
|---|---|---|---|
| 1 | Upload with nothing connected | | |
| 2 | Banner + `STATUS` all `OFF` | | |
| 3 | USB disconnected before wiring | | |
| 4 | Green input only connected | | |
| 5 | `GREEN_ON` / `GREEN_OFF` | | |
| 6 | USB disconnected before wiring | | |
| 7 | `RED_ON` / `RED_OFF` | | |
| 8 | `SAFE` / `ALARM` / `SILENCE` / `ALL_OFF` (no buzzer) | | |
| 9 | USB disconnected before buzzer | | |
| 10 | Buzzer labels/type/voltage/current confirmed | | |
| 11 | Three-pin module connected at 3.3 V only if supported | | |
| 12 | `BUZZER_ON` / `BUZZER_OFF` / `ALL_OFF` | | |
| 13 | `SELF_TEST` + `STOP_TEST` interrupt | | |
| 14 | Observations returned | | |

### 9.2 Supplementary checks (§6.1)

| # | Test | Observed result | Pass? |
|---|---|---|---|
| S1 | `SELF_TEST` completes 8 steps | | |
| S2 | Unknown command `BOGUS` | | |
| S3 | Lowercase `green_on` | | |
| S4 | `STATUS` during self-test | | |
| S5 | `GREEN_ON` then `RED_ON` | | |
| S6 | Multimeter GPIO11 / GPIO12 / GPIO14 | | |

### 9.3 Hardware facts to record

| Item | Your value |
|---|---|
| Exact board silkscreen name | |
| USB port used (`USB` / `UART`) | |
| macOS device path (`/dev/cu.…`) | |
| Bridge chip seen in `system_profiler` | |
| ESP32 core version | |
| Traffic-light module rated voltage | |
| LED module polarity observed (active-high/low) | |
| Buzzer type (passive piezo / magnetic / active) | |
| Buzzer rated voltage and measured current | |
| Any output that needed `*_ACTIVE_HIGH 0` | |
| `BUZZER_IS_PASSIVE` value that worked | |
| Multimeter GPIO11 on / off | |
| Multimeter GPIO12 on / off | |
| Multimeter GPIO14 on / off | |

---

## 10. What Phase 2A does not cover

Deliberately absent, to keep this phase minimal and unblocked:

- no Blynk, no Wi-Fi, no credentials in firmware;
- no YOLO, no camera, no laptop integration;
- no JSON serial protocol, no incident IDs;
- no `BOOT`/`SAFE`/`ALARM_LATCHED`/`SILENCED`/`ACKNOWLEDGED`/`READY_TO_RESET`/`FAULT`
  latched state machine — that is Phase 4/5;
- no Acknowledge/Reset commands (`SILENCE` here is a pin preset, not a latched state);
- no automated tests for this firmware.

The latching behaviour the product needs is **not** implemented here. This sketch
only proves the three outputs can be driven independently and together.
