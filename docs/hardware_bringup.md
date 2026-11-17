# Hardware Bring-Up — Phase 2A

**Project:** EIE3127 Smart Biohazard & Laser Lab Eye-Shield Monitor
**Phase:** 2A — Hardware Bring-Up (firmware self-test only)
**Host:** MacBook, macOS, Arduino IDE
**Firmware:** `firmware/esp32_eye_shield_monitor/`

---

## 0. Status and scope

**Phase 2A has NOT passed.** This document defines the bring-up procedure and the
tests a human must perform. The firmware compiles and uploads, but **no LED,
buzzer, pin voltage, or current has been observed**. Phase 2A passes only when
the observation table in §9 is filled in from real hardware.

| In scope | Out of scope (later phases) |
|---|---|
| Board identification and IDE configuration | Blynk / Wi-Fi (Phase 5) |
| macOS serial-port identification | YOLO integration (Phase 2B/3) |
| Minimal firmware self-test | JSON serial protocol (Phase 4) |
| Green LED GPIO11 independent test | Latched alarm state machine (Phase 4/5) |
| Red LED GPIO12 independent test | Silence / Acknowledge / Reset (Phase 5) |
| Passive buzzer PWM GPIO14 test | Any 7B model |
| Red + buzzer combined test | |
| Non-blocking `millis()` timing | |
| Active-high / active-low configuration | |

---

## 1. Board identification and Arduino IDE configuration

### 1.1 What the board is

The hardware is an **ESP32-S3 UNO development board** — an UNO-form-factor carrier
for an Espressif **ESP32-S3** SoC. It is *not* an Arduino UNO (ATmega328P) and
must never be configured as one.

### 1.2 There is no "ESP32-S3 UNO" board entry

Verified against the installed core: `arduino-cli board listall` returns no
board named "ESP32-S3 UNO". The correct generic target is:

```
ESP32S3 Dev Module          esp32:esp32:esp32s3
```

This is the entry to select. Vendor UNO-form-factor ESP32-S3 boards do not ship a
separate board definition in the official `esp32` core.

### 1.3 Board manager prerequisites

Verified present on this machine:

| Component | Installed version |
|---|---|
| Arduino IDE | installed (`/Applications/Arduino IDE.app`) |
| `arduino-cli` (bundled with the IDE) | 1.5.1 |
| `esp32` core (`esp32:esp32`) | **3.3.11** |

If the core is missing, install it once:

**Arduino IDE → Settings → Additional boards manager URLs**, add:

```
https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

Then **Tools → Board → Boards Manager**, search `esp32`, install **esp32 by
Espressif Systems**.

### 1.4 Exact IDE settings

| IDE field | Value | Why |
|---|---|---|
| **Board** | `ESP32S3 Dev Module` | Only generic S3 target in the official core |
| **Port** | your board's `/dev/cu.*` device — see §3 | |
| **USB CDC On Boot** | **Enabled** | Exposes `Serial` over native USB so the Serial Monitor works |
| **CPU Frequency** | `240MHz (WiFi)` | Default; irrelevant to bring-up |
| **Flash Mode** | `QIO 80MHz` | Default for most S3 modules |
| **Flash Size** | match the module (commonly `4MB`) | Wrong size can stop the board booting |
| **PSRAM** | `Disabled` unless your module has PSRAM | This sketch needs no PSRAM |
| **Partition Scheme** | `Default 4MB with spiffs` | Any scheme with ≥1.3 MB app space works |
| **Upload Speed** | `921600` | Reduce to `115200` if uploads fail |
| **Upload Mode** | `UART0 / Hardware CDC` | Use `USB-OTG CDC (TinyUSB)` if the UART path is not wired |
| **Core Debug Level** | `None` | |
| **Erase All Flash Before Sketch Upload** | `Disabled` | |

> **`USB CDC On Boot` is the single most common failure.** If it is `Disabled`,
> the sketch runs but the Serial Monitor stays silent and every command appears
> to do nothing. If you see no banner, check this first.

### 1.5 Upload mode and the two USB ports

Many ESP32-S3 UNO boards expose **two** USB-C ports:

| Port | Silkscreen | Chip behind it | Device appears as |
|---|---|---|---|
| Native USB | `USB` / `USB-OTG` / `COM` | ESP32-S3 internal USB (GPIO19/20) | `/dev/cu.usbmodem*` |
| UART bridge | `UART` / `COM` | CH340, CH343, or CP210x | `/dev/cu.wchusbserial*`, `/dev/cu.usbserial-*`, `/dev/cu.SLAB_USBtoUART*` |

Either works. If you use the **native USB** port, set *USB CDC On Boot: Enabled*
(already required above). If you use the **UART bridge** port, `Serial` is still
routed to that port and the setting is harmless.

**Assumption to confirm:** the exact port layout and bridge chip of your specific
board is unknown. §3 gives the command that reveals it.

---

## 2. Wiring

### 2.1 Pin assignment

| Signal | ESP32-S3 GPIO | Direction | Notes |
|---|---|---|---|
| Green traffic-light LED | **GPIO11** | output | via series resistor or driver module |
| Red traffic-light LED | **GPIO12** | output | via series resistor or driver module |
| Passive buzzer signal | **GPIO14** | output (PWM) | square wave, not DC |

These three pins are general-purpose on the ESP32-S3 and are **not** used by the
SPI flash (GPIO26–32) or the native USB PHY (GPIO19/20), so they are safe choices.

### 2.2 Wiring table — bare LEDs (no module board)

Use this if you have loose LEDs. **A series resistor is mandatory**; connecting
an LED directly to a GPIO can destroy both.

| From | To | Component |
|---|---|---|
| ESP32-S3 **GPIO11** | green LED **anode** (long leg) | 220 Ω series resistor |
| green LED **cathode** (short leg) | ESP32-S3 **GND** | — |
| ESP32-S3 **GPIO12** | red LED **anode** | 220 Ω series resistor |
| red LED **cathode** | ESP32-S3 **GND** | — |
| ESP32-S3 **GPIO14** | passive buzzer **+** | — (see §8.3 before wiring) |
| passive buzzer **−** | ESP32-S3 **GND** | — |
| ESP32-S3 **GND** | module GND (if any) | common ground — **required** |

With 220 Ω at 3.3 V: red draws ≈6 mA, green ≈1–4 mA depending on the LED's
forward voltage. Both are within the ESP32-S3 GPIO rating.

### 2.3 Wiring table — traffic-light module board

If your green and red outputs are on a commercial traffic-light module, the
resistors are already fitted and you wire three lines:

| From | To | Notes |
|---|---|---|
| ESP32-S3 **GPIO11** | module **G** input | |
| ESP32-S3 **GPIO12** | module **R** input | |
| ESP32-S3 **GND** | module **GND** | common ground — **required** |
| module **VCC** | ESP32-S3 **5V** or **3V3** — see §8.2 | match the module's rating |
| ESP32-S3 **GPIO14** | passive buzzer signal | |
| buzzer **GND** | ESP32-S3 **GND** | |

**Do not connect the module's VCC to a GPIO.** VCC goes to a supply rail only.

### 2.4 Common ground

All three outputs and the ESP32-S3 must share one ground. Without a common
ground the signals float and the outputs behave randomly — this is the second
most common bring-up failure after `USB CDC On Boot`.

---

## 3. Serial-port identification on macOS

The board must be plugged in. As of this document no ESP32 board was attached to
this Mac — only Bluetooth pseudo-ports existed — so the real device name is
unknown and is **not** guessed here.

### 3.1 Find the port

```bash
ls /dev/cu.*
```

Before plugging the board in you will typically see only:

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
4. Check `sudo dmesg | tail` is not available on macOS — instead re-run
   `system_profiler SPUSBDataType` and look for any new device at all.
5. Install the vendor driver if the board uses a CH34x bridge and nothing appears.

Record the port you found in §9.

---

## 4. Required Arduino libraries

**None.** The sketch uses only what ships with the `esp32` core:

| Library | Provided by | Used for |
|---|---|---|
| `Arduino.h` | `esp32` core | `Serial`, `pinMode`, `digitalWrite`, `millis` |
| LEDC API (`ledcAttach`/`ledcWrite`/`ledcDetach`) | `esp32` core | passive buzzer square wave |

No `Tone.h`, no `ESP32Servo`, no Blynk, no external libraries. If the IDE offers
to install a library for `Arduino.h`, decline — it means the board is not set to
an ESP32 target.

---

## 5. Upload procedure

1. Connect the board with a data USB cable.
2. Arduino IDE → **Tools → Board → esp32 → ESP32S3 Dev Module**.
3. **Tools → USB CDC On Boot → Enabled**.
4. **Tools → Port →** the `/dev/cu.*` device from §3.
5. Open `firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino`.
   The IDE must show `config.example.h` as a second tab; if it does not, the
   folder was opened as a single file instead of as a sketch folder.
6. Click **Upload** (→ arrow).
7. Watch the console. Success ends with something like:

```
Writing at 0x00010000... (100 %)
Wrote 315248 bytes ... Hash of data verified.
Leaving...
Hard resetting via RTS pin...
```

8. Open **Tools → Serial Monitor**, set **115200 baud** and line ending
   **Newline** (not `Newline + CR`, not `No line ending`).
9. Press the board's **RST** button. You should see the banner:

```
==============================================================
 ESP32-S3 eye-shield monitor - PHASE 2A HARDWARE SELF-TEST
 NOT the product firmware: no Blynk, no YOLO, no JSON protocol.
==============================================================
config.h        : defaults from config.example.h
green LED       : GPIO11
red LED         : GPIO12
passive buzzer  : GPIO14
serial baud     : 115200
All outputs parked OFF.
...
Ready. Type a command and press Enter.
```

**No banner?** In order: (a) `USB CDC On Boot` not Enabled, (b) wrong port
selected, (c) Serial Monitor baud not 115200, (d) Serial Monitor line ending not
`Newline`, (e) board not actually reset after upload.

### 5.1 Optional: compile-only check without the IDE

Verified working on this machine:

```bash
"/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli" \
  compile --fqbn esp32:esp32:esp32s3 \
  firmware/esp32_eye_shield_monitor
```

Expected tail:

```
Sketch uses 315765 bytes (24%) of program storage space. Maximum is 1310720 bytes.
Global variables use 22296 bytes (6%) of dynamic memory, leaving 305384 bytes for local variables. Maximum is 327680 bytes.
```

---

## 6. Physical tests you must perform

Run these in order. Record every result in §9. Stop and report if any step fails.

| # | Action | Pass condition |
|---|---|---|
| **T0** | Plug in, upload, open Serial Monitor at 115200, press RST | Banner appears; `All outputs parked OFF.`; all lamps dark, buzzer silent |
| **T1** | Type `STATUS` | Block prints; every `state` reads `OFF`; `SELF_TEST : idle` |
| **T2** | Type `GREEN_ON` | Green lamp **on**, red lamp **off**, buzzer **silent**; reply `OK GREEN_ON` |
| **T3** | Type `GREEN_OFF` | Green lamp **off**; reply `OK GREEN_OFF` |
| **T4** | Type `RED_ON` | Red lamp **on**, green lamp **off**, buzzer **silent**; reply `OK RED_ON` |
| **T5** | Type `RED_OFF` | Red lamp **off**; reply `OK RED_OFF` |
| **T6** | Type `BUZZER_ON` | **Tone** audible, both lamps **off**; reply `OK BUZZER_ON` |
| **T7** | Type `BUZZER_OFF` | Silence; reply `OK BUZZER_OFF` |
| **T8** | Type `ALL_OFF` | Everything off; reply `OK ALL_OFF` |
| **T9** | Type `SELF_TEST` | 8 steps in order, ≈0.5 s each, both LEDs and buzzer each exercised; ends `SELF_TEST complete. All outputs OFF.` |
| **T10** | During `SELF_TEST`, type `ALL_OFF` mid-sequence | `SELF_TEST aborted (manual command). All outputs OFF.` and all outputs go dark immediately |
| **T11** | Type `GREEN_ON` then `RED_ON` then `STATUS` | Both lamps lit; `STATUS` reports both `state ON` |
| **T12** | Type `BOGUS` | `ERROR unknown command: BOGUS`; no output changes |
| **T13** | Type `green_on` (lowercase) | Same as `GREEN_ON`; reply `OK GREEN_ON` |
| **T14** | Red + buzzer together: `RED_ON` then `BUZZER_ON` | Red lamp lit **and** tone audible simultaneously |
| **T15** | Measure GPIO11, GPIO12, GPIO14 with a multimeter to GND while each is on | ≈3.3 V when on, ≈0 V when off (inverts if you set active-low) |

### 6.1 If an output is inverted

Symptom: `GREEN_ON` is acknowledged but the green lamp goes **dark**, or the lamp
is lit while `STATUS` says `OFF`.

That output's module is active-low. Fix:

```bash
cp config.example.h config.h     # in firmware/esp32_eye_shield_monitor/
```

In `config.h` set the matching flag to `0`:

```c
#define GREEN_ACTIVE_HIGH 0
```

Re-upload and repeat T2–T5. `config.h` is git-ignored — do not commit it.

---

## 7. Expected LED and buzzer behaviour

### 7.1 Per command

| Command | Green lamp | Red lamp | Buzzer |
|---|---|---|---|
| `GREEN_ON` | **ON** | unchanged | unchanged |
| `GREEN_OFF` | **OFF** | unchanged | unchanged |
| `RED_ON` | unchanged | **ON** | unchanged |
| `RED_OFF` | unchanged | **OFF** | unchanged |
| `BUZZER_ON` | unchanged | unchanged | **tone** |
| `BUZZER_OFF` | unchanged | unchanged | **silent** |
| `ALL_OFF` | **OFF** | **OFF** | **silent** |

### 7.2 `SELF_TEST` sequence

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

Total duration ≈ 8 × 500 ms = **4 seconds**. No step should take noticeably
longer, and the Serial Monitor must stay responsive throughout — that is the
observable proof that the loop is non-blocking.

### 7.3 What the buzzer should sound like

A steady, continuous, high-pitched tone at **2700 Hz**, not a click and not a
buzz at mains hum. A passive piezo driven with DC only clicks once; a sustained
tone proves the PWM path is working.

---

## 8. Electrical risks and unresolved assumptions

### 8.1 GPIO current limits

The ESP32-S3 GPIO is **3.3 V logic**. Recommended source current is ~20 mA per
pin; ~40 mA is the absolute maximum. **Never connect an LED without a series
resistor**, and never drive a magnetic buzzer or a lamp directly from a pin.

### 8.2 Module supply voltage — unresolved

Traffic-light modules are sold in **3.3 V and 5 V** variants and the silkscreen is
often the only difference. **Check your module before connecting VCC.**

- If the module is rated 5 V, power it from the board's `5V`/`VBUS` pin, not
  `3V3`, or it will be dim or dead.
- A 5 V module's **inputs** must still tolerate 3.3 V logic. Most do. If the
  module has series resistors to the LED cathodes in a common-anode arrangement,
  driving its input from 3.3 V is fine.
- **Never** feed 5 V into a GPIO.

If the module's VCC is 5 V and its inputs are pulled to 5 V, a level shifter is
required. This is unverified for your specific module.

### 8.3 Buzzer type — unresolved

- A **bare passive piezo** draws ~1–5 mA and can be driven from GPIO14 directly.
  This is the assumed case.
- A **magnetic passive buzzer** can draw 20–30 mA. At the top of the GPIO rating
  this is marginal. Use a transistor driver module instead of a direct
  connection.
- An **active** buzzer (with internal oscillator) would sound continuously the
  moment its pin is driven high and would ignore the PWM frequency. If you hear a
  constant tone regardless of `BUZZER_ON`/`BUZZER_OFF`, you have an active buzzer
  and it must be replaced with a passive one, because the product needs tone
  control.

If a tone is faint, that is normal for a bare piezo at 3.3 V. It is sufficient
for a prototype; a driver stage can be added later.

### 8.4 Other risks

| Risk | Mitigation |
|---|---|
| Wrong `Flash Size` → board will not boot | Set it to match the module before uploading |
| Upload fails at 921600 | Drop Upload Speed to 115200 |
| Board not detected | Hold BOOT, tap RST, release BOOT |
| Missing common ground → erratic outputs | Verify with a continuity check |
| 5 V into a GPIO | Verify module VCC rating first (§8.2) |
| ESP32-S3 pin reuse | GPIO11/12/14 are free; do **not** move outputs onto GPIO26–32 (SPI flash) or GPIO19/20 (native USB) |
| `config.h` accidentally committed | It is git-ignored; verify with `git check-ignore -v` before `git add` |

### 8.5 Assumptions to confirm against your hardware

1. The board exposes GPIO11, GPIO12 and GPIO14 on its headers under those exact
   numbers. UNO-form-factor carriers sometimes relabel pins; check the board's
   pinout silkscreen.
2. The LED modules are **active-high**. Default in `config.example.h`; flip to
   `0` if inverted (§6.1).
3. The buzzer is **passive** (§8.3).
4. Module VCC is 3.3 V-compatible or is powered from the 5 V rail (§8.2).
5. The USB cable is a **data** cable.

---

## 9. Observation record — to be completed by the human

Fill this in and return it. Phase 2A cannot be marked as passed until it is
complete.

| Test | Observed result | Pass? |
|---|---|---|
| T0 banner + all outputs parked off | | |
| T1 `STATUS` all `OFF` | | |
| T2 `GREEN_ON` | | |
| T3 `GREEN_OFF` | | |
| T4 `RED_ON` | | |
| T5 `RED_OFF` | | |
| T6 `BUZZER_ON` | | |
| T7 `BUZZER_OFF` | | |
| T8 `ALL_OFF` | | |
| T9 `SELF_TEST` 8 steps | | |
| T10 abort mid self-test | | |
| T11 both lamps on + `STATUS` | | |
| T12 unknown command | | |
| T13 lowercase command | | |
| T14 red + buzzer together | | |
| T15 multimeter voltages | | |

Hardware facts to record:

| Item | Your value |
|---|---|
| Exact board silkscreen name | |
| USB port used (`USB` / `UART`) | |
| macOS device path (`/dev/cu.…`) | |
| Bridge chip seen in `system_profiler` | |
| ESP32 core version | |
| Traffic-light module rated voltage | |
| LED module polarity (active-high/low) | |
| Buzzer type (passive piezo / magnetic / active) | |
| Any output that needed `*_ACTIVE_HIGH 0` | |
| Multimeter reading GPIO11 on/off | |
| Multimeter reading GPIO12 on/off | |
| Multimeter reading GPIO14 on/off | |

---

## 10. What Phase 2A does not cover

Deliberately absent, to keep this phase minimal:

- no Blynk, no Wi-Fi, no credentials in firmware;
- no YOLO, no camera, no laptop integration;
- no JSON serial protocol and no incident IDs;
- no `BOOT`/`SAFE`/`ALARM_LATCHED`/`SILENCED`/`ACKNOWLEDGED`/`READY_TO_RESET`/`FAULT`
  state machine — that is Phase 4/5;
- no Silence/Acknowledge/Reset commands;
- no automated tests for this firmware.

The latching alarm behaviour the product needs is **not** implemented here. This
sketch only proves the three outputs can be driven independently and together.
