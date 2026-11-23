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
in the console — no amount of Python code can do this for you. There are two
**independent** recipient mechanisms; either one alone is enough, but each has
a non-obvious gotcha that will make a push silently fail even though
`logEvent` returns HTTP 200.

### Mechanism A — device-level recipient (the one you want for a demo)

This is the normal path: pick yourself as the recipient on the device.

1. First, in the **template**, the event must expose notifications to end
   users. Blynk.Console → Developer Zone → **My Templates** → your template →
   **Edit** → **Events** → `eye_violation` → **Notifications** tab → turn
   **"Notification management"** ON (this makes the event appear in the
   device-level list). Save the template.
2. Then, at the **device** level: Blynk.Console → **Devices** → your device →
   **Actions (⋯)** → **Notification Settings** → `eye_violation` → **Edit
   Settings** → turn **Push** ON → add a recipient (search your own name, or
   choose **All members**) → **Apply**.

> ⚠️ If you do **not** see `eye_violation` in the device-level Notification
> Settings list (or the **Edit Settings** button is greyed out), the
> "Notification management" toggle from step 1 is **OFF**. Go back and turn it
> on — this is the single most common silent failure.

### Mechanism B — default recipient (template level)

A "default recipient" can **only** be selected from **Metadata fields**
(`Device Owner`, `Contact`, or `Email`) — you **cannot** pick an arbitrary
user here. If you try, the list will be empty. To use it you must first create
a Metadata field (e.g. an `Email` field with your address) and then select that
field as the default recipient. For a personal demo, Mechanism A is simpler.

### The two remaining silent killers (check both)

1. **Notification Limits** (in the event's Notifications tab): if a
   **Limit Period** is set (e.g. "1 hour"), Blynk sends **one notification per
   period and silently drops the rest**. This exactly matches the symptom
   "I got *one* notification earlier, then nothing no matter how many times I
   test." **Set Limit Period to "No limit"** (or remove the limit) while
   testing, or every repeated `test_blynk.py` run within the period is
   suppressed even though `logEvent` returns 200.
2. **iOS notification permission**: the push is delivered to the Blynk app on
   the phone logged in as the recipient, and iOS must be allowing notifications
   for Blynk (Settings → Notifications → Blynk). Also make sure you are logged
   into the **same** Blynk account on the phone as the recipient you selected.

> Only **registered Blynk users** can be recipients — you cannot type an
> arbitrary phone number. Re-provisioning a device resets the device-level
> settings, so if pushes stop after reflashing/recreating the device, re-apply
> Mechanism A step 2.

Even with notifications configured correctly, the event fires only when the
lifecycle actually reaches `ALARM_LATCHED` with an incident id — you must keep
the "no goggles" violation present for longer than `--confirmation-seconds`
(default 2 s) for the state machine to latch.

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
