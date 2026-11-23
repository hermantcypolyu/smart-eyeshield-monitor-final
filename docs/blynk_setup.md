# Blynk Cloud setup

Status: **implemented** (Phase 6) — monitoring & notification only.

Blynk Cloud is a **read-only bridge** for the laptop-side Python process. It
publishes the state machine's telemetry to the Blynk IoT dashboard so a remote
operator can watch the Smart Eye-Shield Safety Operations Centre from a phone
or browser. It is **not** a control channel: there are no buttons, switches, or
write callbacks. Local hardware — the ESP32 state machine, latched alarm, LEDs,
buzzer, and OLED — is the sole authority for safety and must remain unaffected
if the cloud is unreachable.

## Architecture

```
Laptop (Python)                         Cloud                     Phone/browser
YOLOv8n + state machine ──► BlynkClient ──► Blynk IoT REST API ──► Blynk.Console
        │                        ▲
        └─ USB serial JSON ──────┘ (unchanged; ESP32 firmware untouched)
```

The ESP32 firmware (C++) is **not modified**. It continues to read USB Serial
JSON exactly as before. `BlynkClient` only *listens* to the existing state and
pushes a snapshot to the cloud; it never changes how detection or the state
machine behave.

## Credentials

Credentials live in a git-ignored `.env` file (template in `.env.example`):

```dotenv
BLYNK_AUTH_TOKEN=your-token-here
BLYNK_SERVER=blynk.cloud
BLYNK_ENABLED=1
```

Never hardcode the token in source. `.env` is listed in `.gitignore` and is
never committed.

## Datastream mapping (V0–V10)

| Pin | Purpose | Values |
|---|---|---|
| V0 | Current state | `"SAFE"`, `"ALARM_LATCHED"`, `"ACKNOWLEDGED"`, … |
| V1 | Incident ID | integer |
| V2 | Green LED | 1 = on, 0 = off |
| V3 | Red LED | 1 = on, 0 = off |
| V4 | Buzzer | 1 = on, 0 = off |
| V5 | Violation ratio | float 0.00–1.00 |
| V6 | Last alert time | ISO-8601 timestamp |
| V7 | Link status | `"ONLINE"` / `"OFFLINE"` |
| V8 | Incident count | integer (session total) |
| V9 | Top confidence | float 0.00–1.00 |
| V10 | FPS | float |

### Physical pin mirroring (V2/V3/V4)

`V2`, `V3`, and `V4` mirror the **physical GPIO state** — the green LED, red
LED, and buzzer as an operator would see them on the device — not the "intent"
of the state machine.

This matters during `ACKNOWLEDGED` and `SAFE_CLEAR_PENDING`: the operator has
acknowledged the alarm, but the hardware **latches the red LED on** until the
danger is actually cleared. The dashboard must keep showing `V3 = 1` in those
states so a remote operator never believes the danger has passed while the red
LED is still physically lit. When a reading carries explicit `green`/`red`/
`buzzer` values they win; otherwise the state table supplies the physical
default.

## Events & anti-spam

The `eye_violation` event is triggered **once per incident** — only on the
transition *into* `ALARM_LATCHED` for a specific `incident_id`. It is never
fired on every frame, so the Blynk event log and push notifications are not
spammed.

## Failure behaviour

- **Network/timeout:** `BlynkClient` is non-blocking. Failed pushes are queued
  and retried with exponential backoff (circuit breaker, 30 s → 300 s).
- **Rate limiting:** pushes are throttled to Blynk's limits; the background
  worker waits for a slot instead of silently dropping safety telemetry.
- **Cloud down:** the YOLO script and ESP32 serial link keep running. A warning
  is printed; nothing halts.

## Testing

- Offline unit tests: `tests/test_blynk_client.py` (`pytest`).
- Live smoke test: `test_blynk.py` (simulates `SAFE` then a violation /
  `ALARM_LATCHED`), with `--dry-run` to run without the network.
