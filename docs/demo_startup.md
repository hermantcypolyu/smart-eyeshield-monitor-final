# Demo startup and shutdown

How to take the board from whatever state the last run left it in to a clean
start for the next demonstration, without opening a serial monitor and typing
the lifecycle handshake by hand.

The helper this document describes is
[`tools/pre_demo_reset.py`](../tools/pre_demo_reset.py). It uses **only** the
commands in [protocol.md](./protocol.md) — no pin presets, no `ALL_OFF`, no GPIO
access, and no way to clear a latch the firmware would refuse to clear.

## Why a helper is needed

The live demo does not clear a latched incident when it exits. That is
deliberate: an open incident is closed only by the
`ACKNOWLEDGE` → `RESET_READY` → `RESET` handshake, and a process exiting is not
evidence that the hazard went away. So a demo that ends during a violation
leaves the board latched, red, and (if not yet acknowledged) buzzing — which is
the correct resting state for a safety device, but an awkward starting state for
the next demo.

## Startup workflow

### 1. Connect the ESP32

Plug the board in and confirm the port exists:

```bash
ls /dev/cu.usbserial-*
```

The port used throughout this project is `/dev/cu.usbserial-10`. `-110` is the
wrong device and is what the Arduino IDE sometimes auto-selects.

Make sure nothing else holds the port — the demo and the Arduino Serial Monitor
both do, and the error is `Resource busy`:

```bash
lsof /dev/cu.usbserial-10
```

### 2. Run the pre-demo reset

```bash
.venv/bin/python tools/pre_demo_reset.py --port /dev/cu.usbserial-10
```

The tool reads the board's own `STATUS` line first and only then decides what,
if anything, to do:

- **No open incident** → prints `Board already clean` and exits `0`. A board
  that reports no incident but is not idle (for example `FAULT`, or a stale red
  lamp) is returned to `SAFE`, which is legal whenever no incident is open.
- **Open incident** → shows the incident id and current outputs, asks for
  confirmation, then performs the handshake. `--auto` skips the prompt.
- **Nothing is sent that the current state does not permit.** `ACKNOWLEDGE` is
  skipped when the board already reports `ACK=1`, and a `HEARTBEAT` is sent
  immediately before `RESET` because the firmware refuses a reset whose link is
  not fresh.

### 3. Verify `READY FOR NEW DEMO`

The tool prints its command trace and the board's final state, and only then:

```text
Final board state
  lifecycle    : SAFE
  incident id  : 0 (none)
  green LED    : ON
  red LED      : off
  buzzer       : off

READY FOR NEW DEMO
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Board is clean — either it already was, or recovery succeeded |
| `1` | Recovery failed; the firmware's refusal is printed verbatim |
| `2` | Could not talk to the board (port missing, busy, or no `STATUS` line) |
| `3` | Operator declined, or interrupted |

Do not launch the demo on a non-zero exit. Fix the reason first.

### 4. Launch the live demo

```bash
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

These six values are the demo preset from
[FINAL_WORKING_VERSION.md](../FINAL_WORKING_VERSION.md). The acceptance preset
in [v2_live_acceptance.md](./v2_live_acceptance.md) is different — never mix the
two, or a run will be compared against thresholds it was not recorded under.

The demo opens the serial port itself, so `pre_demo_reset.py` must have finished
before it starts. Run them in sequence, never at the same time.

### 5. Perform the demonstration

| Operator action | Expected board behaviour |
|---|---|
| Wear the goggles | Green LED, `SAFE` |
| Remove the goggles for the confirmation window | Red LED, buzzer, `ALARM_LATCHED` |
| Press `A` | Buzzer stops, red stays on, `ACKNOWLEDGED` |
| Put the goggles back on, hold for the clearance window | `SAFE_RECOVERED` |
| Press `R` | Green LED, `RESET` accepted, back to `SAFE` |

Per-state detail, including what each lamp and the buzzer do in every state, is
in [SYSTEM_STATE_REFERENCE.md](../SYSTEM_STATE_REFERENCE.md).

## Shutdown workflow

### 1. Close the live demo

Press `Q`, or `Ctrl-C`. Either way the demo closes the serial port; if it did
not, the next launch fails with `Resource busy`.

### 2. Observe the board

Look at the board before packing up:

- **Green, quiet** — the demo ended in `SAFE`. Nothing to do.
- **Red, buzzing, or red and quiet** — an incident is still open. The board is
  latched, which is correct, and it will still be latched at the next demo.

### 3. Reset before the next demo if an incident remains

```bash
.venv/bin/python tools/pre_demo_reset.py --port /dev/cu.usbserial-10
```

Waiting until just before the next demo is fine. A latched board holds its
incident across power cycles only as long as it stays powered — the latch is in
RAM, so a board that is unplugged and replugged comes up in `SAFE` with no
incident. Do not rely on that as the normal shutdown procedure: unplugging a
safety device to clear an alarm is exactly the habit this design is meant to
prevent, and `pre_demo_reset.py` makes the correct route just as convenient.

## What the tool sends, and why that order

```text
STATUS              read the board's own view; never guess
HEARTBEAT           RESET is refused unless the link is fresh
ACKNOWLEDGE <id>    skipped when the board already reports ACK=1
RESET_READY <id>    arm the gate; refused unless acknowledged
HEARTBEAT           refresh the link before the gated command
RESET               only now legal, and only for this incident
STATUS              verify the board really returned to SAFE
```

Every command is id-carrying, and every step waits for the exact reply code the
firmware documents for it. A reply with the wrong code is reported and ignored
rather than treated as success — the same discipline
`tools/live_eye_shield_demo.py` uses, and the reason `OK HEARTBEAT` can never
confirm an acknowledgement.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `cannot open ... Resource busy` | Demo or Serial Monitor holds the port | `lsof /dev/cu.usbserial-10`, close the owner |
| `cannot open ... No such file` | Wrong port name | `ls /dev/cu.usbserial-*` and use the real one |
| `no STATUS line from the board` | Firmware build without the machine-readable line, or wrong port | Confirm `STATUS` in a serial monitor returns `STATE=...` |
| `ERR RESET_REJECTED LINK_OR_HEALTH_INVALID` | Heartbeat window (3000 ms) lapsed | Re-run; the tool heartbeats immediately before `RESET` |
| `ERR RESET_READY_REJECTED NOT_ACKNOWLEDGED` | Board lost the acknowledge | Re-run; the tool skips `ACKNOWLEDGE` only when `ACK=1` |
| `ERR SAFE_REJECTED INCIDENT_ACTIVE` | Tried to force `SAFE` with an incident open | Expected; use the handshake, which is what the tool does |
| `exit 3` | You answered `n`, or pressed `Ctrl-C` | Re-run and confirm |

Broader hardware and upload problems are in
[troubleshooting.md](./troubleshooting.md) and
[RECOVERY_GUIDE.md](../RECOVERY_GUIDE.md).
