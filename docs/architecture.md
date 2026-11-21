# Architecture

Phase 0 establishes the boundaries below. Only the configuration layer and the
environment/camera check tools exist so far; the modules named in the pipeline
are created in later phases and must respect these boundaries.

## Component ownership

| Concern | Owner | Rationale |
|---|---|---|
| Frame capture, inference | Laptop | Needs a GPU/CPU and a full Python stack |
| Observation validation, temporal filtering | Laptop | Pure, unit-testable, time-based logic |
| Incident identity (`incident_id`) | Laptop | Generated once, then referenced by both sides |
| **Alarm latch** | **ESP32-S3** | The physical alarm must survive application crashes, cable pulls, and Blynk outages |
| LED and buzzer output | ESP32-S3 | Deterministic, non-blocking, `millis()`-scheduled |
| Silence / Acknowledge / Reset commands | Blynk → ESP32-S3 | Accepted/rejected by the device state machine, not by the dashboard |
| Event logging (JSONL/CSV) | Laptop | Single writer avoids interleaved files |

## Phase 4A amendment — the two authorities

Phase 4A refined the split above. There are exactly **two** authorities and each
has one job:

| Authority | Owns | Must never |
| --- | --- | --- |
| Laptop (`tools/incident_lifecycle.py`) | Detection, temporal filtering, incident identity, operator-facing state, whether recovery is proven | Assert that the lamp is off |
| ESP32-S3 (`incident_core.h`) | The physical latch, the buzzer, and whether a reset is permitted | Decide that the wearer is safe |

The only channel between them is a text line protocol (`docs/protocol.md`).
Neither side can clear an alarm alone: silence needs the laptop's `ACKNOWLEDGE`,
and clearing needs `RESET_READY <id>` followed by `RESET`, with the firmware
re-checking its own preconditions in a fixed order and refusing with a specific
reason. A refusal is not a no-op — the laptop re-asserts its state, because the
failure mode to avoid is the two authorities disagreeing.

Both sides are pure logic with the platform layer kept thin, so the whole
contract is host-tested. The firmware header has no Arduino dependency; the
sketch only reads pins, calls the core, and writes replies.

## Data flow

```text
Webcam ──frames──> Detector ──raw detections──> Observations (validate + classify)
                                                     │
                                                     v
                                        TemporalFilter (monotonic time)
                                                     │  UNKNOWN | SAFE |
                                                     │  VIOLATION_PENDING |
                                                     │  VIOLATION_CONFIRMED
                                                     v
                                        IncidentManager ──> EventLogger
                                                     │
                                                     v
                                        SerialTransport (NDJSON, retry, heartbeat)
                                                     │
                                                     v
                                        ESP32-S3: AlarmStateMachine ──> LEDs/buzzer
                                                     │
                                                     v
                                                   Blynk
```

## Trust boundaries

1. **Camera → laptop.** Untrusted input. Frames may be empty, dark, tiny, or
   contain a poster of a face. Nothing here can by itself assert compliance.
2. **Laptop → ESP32 serial.** Untrusted input. Lines may be malformed, truncated,
   duplicated, or oversized. The ESP32 rejects rather than crashes.
3. **Blynk → ESP32.** Untrusted commands. Every command is re-validated against
   the device's own state and link health; a dashboard button never asserts safety.
4. **Safe ↔ unsafe asymmetry.** `UNKNOWN` is never promoted to `SAFE`. Only a
   positive `goggles` observation can advance the clearance timer.

## Why the design is time-based

Frame rate varies with lighting, USB contention, and inference cost. A frame-count
rule would silently change the confirmation duration (e.g. 60 frames = 2 s at
30 FPS, but 6 s at 10 FPS). All thresholds are therefore expressed in seconds and
measured with `time.monotonic()`.

## Module responsibilities (populated in later phases)

| Module | Responsibility | Phase |
|---|---|---|
| `src/config.py` | Load/validate YAML and class map; fail fast | **Done** |
| `src/camera.py` | Capture with reconnect; expose frames + timestamps | 3 |
| `src/detector.py` | YOLO wrapper, class-ID mapping to `classes.yaml` | 3 |
| `src/observations.py` | Detection → validated observation, `UNKNOWN` rules | 3 |
| `src/temporal_filter.py` | Confirmation/clearance state machine | 3 |
| `src/incident_manager.py` | One incident per episode; reset eligibility | 3 |
| `src/serial_protocol.py` | Typed message encode/decode, schema validation | 4 |
| `src/serial_transport.py` | Port discovery, heartbeat, retry, reconnect | 4 |
| `src/event_logger.py` | JSONL + CSV writers, non-blocking | 3 |
| `src/overlay.py` | On-frame status rendering | 3 |
| `src/health_monitor.py` | Link/camera/Blynk health aggregation | 4 |
| `src/main.py` | Wiring and run loop | 3 |
| `tools/incident_lifecycle.py` | **Done (4A)** — operator state machine, acknowledgement, recovery, reset handshake |
| `firmware/.../incident_core.h` | **Done (4A)** — Arduino-free latch, buzzer intent, reset gate |
