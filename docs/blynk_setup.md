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

### Enabling push notifications (one-time, console-side)

Firing the event via the HTTPS API only *logs* it. For the Blynk app to push a
notification to your phone (or send email/SMS), you must enable notifications
in **two places** in the console — no amount of Python code can do this for
you. Both are required; missing the second is the most common reason a push
never arrives even though `logEvent` returns HTTP 200.

**Step 1 — enable the event's notifications (template level):**

1. Blynk.Console → Developer Zone → **My Templates** → your template → **Edit**.
2. Open **Events** and select the `eye_violation` event.
3. Open the event's **Notifications** tab and turn **Enable notifications** ON.
4. Save the template.

**Step 2 — turn on Push and pick a recipient (device level):**

1. Blynk.Console → **Devices** → select the device that logs the event.
2. Open its **Actions** (⋯) menu → **Notification Settings**.
3. Select the `eye_violation` event → **Edit Settings**.
4. Turn **Push** ON, add a recipient (search your own name/email, or
   **All members**), then **Apply**.

> Only **registered Blynk users** can be recipients — you cannot type an
> arbitrary phone number. The push is delivered to the Blynk app on the phone
> that is logged in as that user (and the app must be allowed notifications in
> iOS Settings). Re-provisioning a device resets Step 2, so if pushes stop
> after reflashing/recreating the device, re-apply the device-level settings.

Without both steps, `logEvent` succeeds (HTTP 200, event logged) but nothing
reaches your phone. Note the event still fires only when the lifecycle actually
reaches `ALARM_LATCHED` with an incident id — you must keep the "no goggles"
violation present for longer than `--confirmation-seconds` (default 2 s) for
the state machine to latch.

## "Offline" label in the console (expected)

The Blynk.Console shows a device as **Online/Offline** based on an MQTT /
Blynk-Protocol connection (the `Blynk.run()` heartbeat), *not* on datastream
writes. This project uses the **HTTPS REST API**, which writes values but never
opens that connection, so the console's device status will always read
**"Offline"** even while telemetry is updating normally.

This is cosmetic and expected, and it does **not** block data updates or
notifications (events work independently over HTTPS). The `V7` "Link Status"
datastream is what this bridge controls, and it reflects the laptop's own
serial link — that is why the console's "Offline" label and the dashboard's
"ONLINE" widget can disagree. Do not re-diagnose the "Offline" label as a bug;
it is an inherent property of the REST bridge.

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
