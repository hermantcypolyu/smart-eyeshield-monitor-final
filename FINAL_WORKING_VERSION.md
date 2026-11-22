# FINAL WORKING VERSION — Smart Eye-Shield Monitor

Release-freeze snapshot of the first physically working end-to-end build.
**This document records state only. It does not describe fixes.**

- **Snapshot commit:** `ec5241cb22c0de8da39fabe6795842f1c1d8b946`
  `fix(demo): define mark_onboard_verified and require it for reset`
  — this is the code state that froze the working build. The release branch and
  tag were created from it; the tag additionally carries the four release
  documents (commit `a561ab8ab186f937a7c760ab204405782dda2a45`,
  documentation only).
- **Date frozen:** 2026-11-22
- **Release branch:** `release/phase4-working`
- **Release tag:** `v0.9-working-demo`
- **Parent branch:** `master`

---

## Project status summary

Phase 4 hardware integration is functionally complete and demonstrable.
The laptop runs YOLOv8n inference, a 2-second temporal filter and a latched
incident lifecycle; the ESP32-S3 renders the decision as a green LED, a red
LED and a buzzer over a line-based USB serial protocol at 115200 baud.

One defect remains open: the board and the laptop can disagree about the
acknowledge/reset handshake (see **Outstanding bugs**). It is a
state-synchronisation defect, **not** a loss of function — the demo still
runs end to end.

---

## Verified features

Independently observed on the physical rig before this freeze:

- YOLOv8n V2 weights (`models/best_v2.pt`) load successfully.
- Real safety goggles produce `SAFE`.
- Real `no_goggles` produces a violation.
- The 2-second temporal filter suppresses single-frame noise in both directions.
- The latched incident lifecycle transitions correctly
  (`VIOLATION_PENDING` → `ALARM_LATCHED` → … → `ACKNOWLEDGED` → `RESET`).
- The `ACKNOWLEDGED` state is reachable and visible in the UI.
- The web dashboard renders live state, FPS and hardware indicators.
- The ESP32 serial link answers commands and carries the 1.0 s heartbeat.
- Green LED (GPIO11) works.
- Red LED (GPIO12) works.
- Buzzer (GPIO14) works.

## Known limitations

- Single camera viewpoint; the demo must be run from the laptop, never the board.
- The ESP32 has no camera interface and roughly 520 KB of RAM, so it can only
  render decisions. It cannot run the detector.
- No HC-SR04 proximity sensor and no Blynk cloud telemetry — both are out of
  scope for this freeze.
- The V2 model is **not promoted**. `models/best.pt` (V1) keeps its role as the
  reference weights; V2 remains an evaluation candidate.
- The 45-trial live acceptance matrix is incomplete, so the summariser reports
  `V2 NOT APPROVED`.
- Serial link reliability depends on the exact USB port; see
  [RECOVERY_GUIDE.md](./RECOVERY_GUIDE.md).

---

## Current hardware configuration

| Item | Value |
|---|---|
| Board | ESP32-S3 (QFN56, rev v0.2), 8 MB PSRAM, 240 MHz |
| Module markings | "ESP32 UNO purple" — no dedicated BOOT button on the shield |
| USB port (macOS) | `/dev/cu.usbserial-10` |
| MAC address | `98:a3:16:ec:56:74` |
| Serial baud | `115200` |
| FQBN | `esp32:esp32:esp32s3` |
| Green LED | GPIO11 |
| Red LED | GPIO12 |
| Buzzer | GPIO14, **active LOW** |
| Boot mode | Auto-reset via RTS/DTR; no manual BOOT press needed |

## Current model information

| Property | `models/best.pt` (V1) | `models/best_v2.pt` (V2) |
|---|---|---|
| Purpose | Reference weights, frozen | Evaluation candidate, in use for the demo |
| Task | YOLOv8n detection | YOLOv8n detection |
| Class 0 | `goggles` (compliant) | `goggles` (compliant) |
| Class 1 | `no_goggles` (violation) | `no_goggles` (violation) |
| Size | 6,508,202 bytes | 6,501,866 bytes |
| SHA-256 | `9366b73b35d4f820af401808aa4e0e58cb29a7b557991b123e5cd6a813fa3591` | `fec02c88f4236d85d4e913df57638a96cf0a35498f6a65c413b7fe14077f22fc` |
| Git | Ignored (`models/*.pt`) | Ignored (`models/*.pt`) |

Inference preset used by the demo: candidate confidence `0.40`,
goggles confidence `0.60`, no-goggles confidence `0.55`, NMS IoU `0.30`.

> Do not mix this preset with the acceptance preset
> (`0.50 / 0.65 / 0.60 / 0.30`). They are recorded separately and must never be
> combined in one measurement.

## Current serial protocol

Line-oriented ASCII, `\n` terminated, 115200 8N1.

**Laptop → board (production path only):**

```
INCIDENT <id>        latch the alarm
ACKNOWLEDGE <id>     operator claims they saw the alarm
RESET_READY <id>     recovery proven for that incident
RESET                clear the latch (requires a healthy link)
NO_INCIDENT <id>     clear verdict (never clears red on its own)
HEARTBEAT            proves the link is alive, every 1.0 s
STATUS               request the machine-readable status line
```

**Board → laptop (selected):**

```
OK INCIDENT <id>     alarm latched (red on, buzzer on)
OK ACKNOWLEDGED <id> buzzer off, red stays on
OK RESET             latch cleared, back to green
OK HEARTBEAT
STATUS <k=v ...>     board's own authoritative view
ERR <reason>         rejected command
```

Bare pin presets (`GREEN_ON`, `RED_ON`, `BUZZER_OFF`, `ALL_OFF`, …) exist for
bring-up and self-test only and are **never** used by the production path.
`SAFE` / `ALARM` / `SILENCE` are accepted by the current firmware but print
`WARN PRESET_OVERRIDE` whenever an incident is open; the laptop must not send
them.

## Current run commands

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject

.venv/bin/python tools/live_eye_shield_demo.py \
  --source 0 \
  --weights models/best_v2.pt \
  --candidate-confidence 0.40 \
  --goggles-confidence 0.60 \
  --no-goggles-confidence 0.55 \
  --iou 0.30 \
  --device auto \
  --serial-port /dev/cu.usbserial-10
```

Every model and threshold value is passed on the command line. `config/app.yaml`
still points at `models/best.pt` with `0.55 / 0.45`, and the demo does **not**
read it for these six values — so do not expect `app.yaml` edits to change the
demo's behaviour. Edit the command line instead.

Operator keys during the demo:

| Key | Meaning |
|---|---|
| `A` | acknowledge the latched alarm (buzzer stops, red stays) |
| `R` | reset once recovery is verified (green returns) |
| `Q` | quit |

Dashboard:

```bash
bash dashboard/start.sh
```

Link probe (no camera, no model):

```bash
.venv/bin/python tools/test_esp32_quick.py
```

## Current firmware upload command

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject

arduino-cli compile \
  --fqbn esp32:esp32:esp32s3 \
  firmware/esp32_eye_shield_monitor

arduino-cli upload \
  -p /dev/cu.usbserial-10 \
  --fqbn esp32:esp32:esp32s3 \
  firmware/esp32_eye_shield_monitor
```

If `arduino-cli upload` reports
`Unable to verify flash chip connection` at 921600 baud, see the
**Upload troubleshooting** section of [RECOVERY_GUIDE.md](./RECOVERY_GUIDE.md).

## Outstanding bugs

**B-01 — Hardware/laptop acknowledge desynchronisation (open).**
Under investigation. After `ALARM_LATCHED` the board could return to green while
the laptop still displayed `ACKNOWLEDGED: YES`, and an `OK HEARTBEAT` reply could
be mistaken for an acknowledge confirmation.

The working tree contains the in-progress correction to the acknowledge handshake:
the laptop now *requests* an acknowledge and only enters `ACKNOWLEDGED` after the
board answers `OK ACKNOWLEDGED` for the matching incident id. That change is
**unverified on hardware** — the corrected firmware could not be flashed before
this freeze (see `BACKUP_INVENTORY.md`). Treat the physical handshake as
unproven until it is re-tested.

**B-02 — Firmware upload instability (open).**
`arduino-cli` occasionally fails chip verification at 921600 baud, and the
Arduino IDE auto-selects `/dev/cu.usbserial-110` (the wrong port) when several
USB serial devices are present.

## Recovery instructions

If any later change breaks this build, return to the frozen snapshot:

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject
git checkout release/phase4-working
# or, for a detached, read-only look:
git checkout --detach v0.9-working-demo
```

Full restore, clean-build and reflash procedures live in
[RECOVERY_GUIDE.md](./RECOVERY_GUIDE.md).
Intended lifecycle semantics live in
[SYSTEM_STATE_REFERENCE.md](./SYSTEM_STATE_REFERENCE.md).
Repository contents and sizes live in
[BACKUP_INVENTORY.md](./BACKUP_INVENTORY.md).
