# Report Context — Smart Eye-Shield Safety Operations Centre (EIE3127)

> **Purpose.** This document is a *fact extract* for the EIE3127 final report.
> Every statement below is drawn from the actual source code, firmware, and
> committed documentation in this repository. It is **not** the report itself;
> it is the raw technical material from which the report must be written.
>
> **Accuracy rules.** No metric is invented. Where a value is a *target* rather
> than a *measured result* it is labelled as such. Where a feature is code-complete
> but **not** verified on hardware, or a model is **not** promoted, that is stated
> explicitly and must not be softened in the report.

---

## 1. System Architecture & Data Flow

### 1.1 End-to-end flow

The system is a **two-authority** design split across a laptop and an ESP32-S3
microcontroller, connected by a single USB serial link. The complete data path:

```text
Webcam ──frames──> YOLOv8n detector ──raw detections──> Observation (validate + classify)
                                                            │
                                                            v
                                               TemporalFilter (monotonic time)
                                                            │  UNKNOWN | SAFE |
                                                            │  VIOLATION_PENDING |
                                                            │  VIOLATION_CONFIRMED
                                                            v
                                               IncidentLifecycle (operator state machine)
                                                            │
                                                            v
                                         USB Serial (USB CDC, 115200, text protocol)
                                                            │
                                                            v
                                               ESP32-S3 IncidentCore ──> LEDs / buzzer / OLED
                                                            │
                                                            v
                                          Blynk Cloud (REST API, read-only telemetry)
                                                            │
                                                            v
                                                        Mobile / browser
```

The step-by-step chain is: **webcam** (laptop camera) → **YOLOv8n inference**
(`tools/live_eye_shield_demo.py`) → per-frame **Observation** classification
(`UNKNOWN`/`SAFE`/`VIOLATION`) → **TemporalFilter** (time-based confirmation) →
**IncidentLifecycle** (9-state operator machine, `tools/incident_lifecycle.py`) →
**USB serial JSON/text** commands → **ESP32-S3** `IncidentCore` (hardware latch,
LEDs, buzzer, OLED) → **Blynk Cloud** over HTTPS REST → **mobile app** (push
notifications + dashboard).

### 1.2 Component ownership (who owns what)

| Concern | Owner | Rationale |
|---|---|---|
| Frame capture, inference | **Laptop** | Needs a GPU/CPU and a full Python stack |
| Observation validation, temporal filtering | **Laptop** | Pure, unit-testable, time-based logic |
| Incident identity (`incident_id`) | **Laptop** | Generated once, then referenced by both sides |
| **Alarm latch** | **ESP32-S3** | The physical alarm must survive application crashes, cable pulls, Blynk outages |
| LED and buzzer output | **ESP32-S3** | Deterministic, `millis()`-scheduled, non-blocking |
| Silence / Acknowledge / Reset decision | **ESP32-S3** | Accepted/rejected by the device state machine, not the dashboard |
| Event logging (JSONL/CSV) | **Laptop** | Single writer avoids interleaved files |

### 1.3 Why the ESP32 owns the safety latch (and Blynk has no remote reset)

The **alarm latch is owned by the ESP32-S3**, not the laptop and not the cloud.
A confirmed `INCIDENT <id>` latches the alarm; it stays latched even if the
laptop application exits (the demo deliberately does **not** send `ALL_OFF` on
shutdown), and a serial disconnect never clears a latched incident.

This is a **safety constraint**, not an implementation convenience: the physical
alarm must survive three failure modes that are routine in a lab deployment —

1. **Application crash** — if the Python process dies, the red lamp and buzzer
   must keep warning.
2. **Cable pull** — if the USB serial link is unplugged, the latch must persist.
3. **Cloud outage** — if Blynk is unreachable, local hardware must be unaffected.

Consequently **Blynk has no remote reset**. `src/blynk_client.py` is read-only
telemetry: it publishes datastreams and fires events, but has no write callbacks,
no buttons, and no switches. There is no code path by which the cloud can clear
the alarm. The **only** path to clear a latched incident is the two-step local
handshake on the serial link:

```text
Laptop: RESET_READY <id>   →   Firmware: OK RESET_READY   (state = READY_TO_RESET)
Laptop: RESET              →   Firmware: OK RESET_ACCEPTED  (incident closed, SAFE)
```

The reset token is bound to the current `incident_id`, so a stale token from an
earlier episode is refused. The firmware also requires a **fresh heartbeat**
(within `HEARTBEAT_TIMEOUT_MS = 3000 ms`) before honouring `RESET`, so a crashed
or unplugged laptop cannot clear an alarm either.

### 1.4 Trust boundaries

1. **Camera → laptop** (untrusted): frames may be empty, dark, tiny, or contain
   a poster of a face.
2. **Laptop → ESP32 serial** (untrusted): lines may be malformed, truncated,
   duplicated, or oversized; the board rejects rather than crashes.
3. **Blynk → ESP32** (untrusted): every command is re-validated against the
   device's own state and link health.
4. **Safe/unsafe asymmetry**: `UNKNOWN` is never promoted to `SAFE`; only a
   positive `goggles` observation can advance the clearance timer.

---

## 2. AI & Data Method

### 2.1 Model

| Item | Value |
|---|---|
| Task | Object detection (single stage) |
| Architecture | Ultralytics YOLOv8n (COCO pre-trained `yolov8n.pt`) |
| Classes | `0 goggles` (safe), `1 no_goggles` (violation) — from `config/classes.yaml` |
| Image size | 640 px |
| Deployment | Laptop CPU/MPS (Apple M2); **not** on the ESP32 |

The class semantics are **not negotiable**. Class 0 means *genuine safety
goggles worn over the eyes*; class 1 means *the eye region is assessable but
goggles are absent or worn incorrectly*. Inverting them would silently invert
every downstream safety decision, so the training pipeline refuses to run
against a mismatched `data.yaml` (enforced by `tests/test_training_pipeline.py`).

### 2.2 V1 (`best.pt`) vs V2 (`best_v2.pt`)

Both are YOLOv8n checkpoints of identical architecture and 640 px geometry; the
difference is **training data**, not model structure.

| | V1 (`models/best.pt`) | V2 (`models/best_v2.pt`) |
|---|---|---|
| Size | 6,508,202 B (6.5 MB) | ~6.5 MB |
| Trained on | 240-image Phase 2B baseline (160/40/40) | 300-image Domain-V2 set |
| Start weights | COCO `yolov8n.pt` | **V1 `best.pt`** (transfer learning) |
| Purpose | baseline detector | domain adaptation to deployment camera |
| Status | committed reference / fallback | **unpromoted** (not officially accepted) |

**V2 rationale.** V1 reached P 0.8040 / R 0.7993 / F1 0.8016 / mAP50 0.8439 /
mAP50-95 0.4867 on its locked 40-image test split, but on live webcam frames it
exhibits a **false-safe** failure: a bare-eye face is occasionally reported as
`goggles`. A false safe is the worst possible error (the alarm never fires), and
V1's aggregate F1 does not expose it because the baseline was captured under
different lighting/framing/camera geometry than deployment.

The remedy is a small **transfer-learning** step: start from the already-trained
V1 checkpoint (never from COCO) and adapt it to 60 newly captured webcam images
(Domain V2) that share the deployment camera's actual appearance. V2 is written
to `best_v2.pt`; V1 remains read-only and is the fallback for the live demo.

**Important (honest status):** V2 is **not promoted**. The V2 live acceptance
matrix (45 trials) has 0 of 45 trials recorded, verdict `V2 NOT APPROVED`
(`docs/v2_live_acceptance.md`). `models/best.pt` (V1) remains the reference
weights. The report must state V2 as "unpromoted / under acceptance", not as an
approved result.

### 2.3 Dataset (source, license, adaptation)

| Property | Value |
|---|---|
| Original source | Roboflow `workspace-3vzwb/safety-glasses-dataset` |
| Fork/export workspace | `eie3360-fwbnu` |
| Licence | **CC BY 4.0** |
| Raw archive | `data/raw/roboflow_safety_glasses_original.zip` (25,717,848 B, SHA-256 `39afc3ac…3cf53`) |
| Raw size | 400 images / 770 instances (5 classes: boots/glasses/gloves/helmet/vest) |

The raw 5-class PPE export was **rejected for training** by the Phase 1 dataset
audit (`docs/dataset_audit.md`) for five blocking findings: (1) the required
`goggles`/`no_goggles` classes were absent; (2) 5 classes declared vs. the
required 2; (3) 59 split-leakage groups; (4) test split only 4.25% (target
~10%); (5) 2 malformed annotation rows. A curated, re-labelled 240-image
baseline was then prepared, and Domain-V2 added 60 fresh webcam images (42/12/6
split) re-labelled through `0 eyes_with_goggles → goggles`,
`1 eyes_without_goggles → no_goggles`.

**Domain-V2 composition** (300 images / 314 boxes, verified in the notebook):

| Split | Images | Boxes | `goggles` | `no_goggles` |
|---|---|---|---|---|
| train | 202 | 214 | 107 | 107 |
| val | 52 | 52 | 26 | 26 |
| test | 46 | 48 | 23 | 25 |
| total | 300 | 314 | 214 | 216 |

**Honest caveat (must be quoted):** the six webcam test stills
(`goggles_webcam_001/024/028`, `no_goggles_webcam_003/007/018`) are the *entire*
target-domain test evidence. V2 metrics are dominated by the 40 baseline-derived
test images and are therefore a weak estimate of deployment behaviour.

### 2.4 Per-frame observation decision rule

`decide_observation()` in `tools/live_eye_shield_demo.py` converts accepted
detections into one conservative frame decision:

- Any accepted `no_goggles` detection → `VIOLATION` (unsafe wins).
- `goggles` → `SAFE` **only if** no accepted `no_goggles` exists.
- No accepted detections → `UNKNOWN`.

### 2.5 Temporal filter (exact time logic)

`TemporalFilter` (in `tools/live_eye_shield_demo.py`) is the frame-level filter;
`IncidentLifecycle` (in `tools/incident_lifecycle.py`) is the operator-level
state machine. The confirmation window is `confirmation_seconds = 2.0`
(default), clearance is `clearance_seconds = 2.0`, with
`violation_ratio_required = 0.80` and `unknown_tolerance_seconds = 0.35`.

**Why time-based, not frame-based.** Frame rate varies with lighting, USB
contention, and inference cost. A frame-count rule would silently change the
confirmation duration (e.g. 60 frames = 2 s at 30 FPS but 6 s at 10 FPS). All
thresholds are therefore expressed in **seconds** and measured with
`time.monotonic()` — a monotonic clock injected by the caller, never wall-clock
time, so the 2 s is invariant to frame rate.

**How the 2-second confirmation works (exact):**

- A `VIOLATION` observation sets `pending_since = now` (first time only).
- `elapsed = now - pending_since`. When `elapsed >= confirmation_seconds` **and**
  `ratio >= violation_ratio_required` (0.80), the filter confirms:
  `confirmed = True`, `incident_id = next_incident_id++`, `confirmed_after_seconds`
  frozen once at confirmation so later frames cannot overwrite it with `0.00`.
- A `SAFE` observation **cancels** a pending violation (`pending_since = None`).
- **UNKNOWN handling:** `UNKNOWN` must never become `SAFE`. If there is no
  pending violation, `UNKNOWN` simply leaves state at `UNKNOWN`. During a pending
  violation, a brief `UNKNOWN` (≤ `unknown_tolerance_seconds = 0.35 s` past the
  last `VIOLATION`) is tolerated and the pending clock keeps running. If `UNKNOWN`
  lasts longer than that, the pending confirmation is **cancelled** — no incident
  is created. `UNKNOWN` never *extends* a pending confirmation.

**Confirmed-incident behaviour:** once confirmed, a `SAFE` observation starts the
clear timer (`safe_since = now`, state `SAFE_CLEAR_PENDING`); any `UNKNOWN` or
continued `VIOLATION` resets `safe_since = None` and returns to
`VIOLATION_CONFIRMED`, so the incident stays latched and its measured duration is
preserved.

### 2.6 Training settings (V2, fixed in advance)

| Setting | Value | Reason |
|---|---|---|
| start weights | `/content/best.pt` (V1) | adapt the trained detector, not relearn from COCO |
| epochs | 25 | 202 target images; 60 would overfit |
| patience | 8 | early stop on val split |
| imgsz | 640 | identical to V1 and deployment |
| batch | 16 | fits 16 GB T4 at 640 px |
| seed / deterministic | 42 / True | reproducibility |
| device | 0 (T4 GPU) | training happens in Google Colab |
| Ultralytics | 8.4.215 (pinned) | locally verified |

Training is executed in Google Colab (`training/colab_domain_v2_finetuning.ipynb`),
because the local Mac has no CUDA GPU (`torch.cuda.is_available()` → False; MPS
available). The test split is structurally firewalled: the training YAML has no
`test:` key, and evaluation redirects `val:` to `test/images`.

---

## 3. Implementation & State Machine

### 3.1 Operator state machine (`OperatorState`)

`tools/incident_lifecycle.py` defines `OperatorState(str, Enum)` with **9 states**:

| State | Meaning |
|---|---|
| `STARTUP` | initialising, before the target is seen |
| `WAITING_FOR_TARGET` | link/health not yet valid, no target |
| `SAFE` | normal, green |
| `VIOLATION_PENDING` | violation observed, confirmation timer running |
| `ALARM_LATCHED` | confirmed violation; alarm latched |
| `SAFE_CLEAR_PENDING` | recovery observed, clearance timer running |
| `SAFE_RECOVERED` | recovery proven; reset gate armed |
| `ACKNOWLEDGED` | operator acknowledged the alarm |
| `FAULT` | link/health invalid; outputs parked |

Supporting enums: `Event` (the lifecycle transition triggers, e.g.
`TARGET_SEEN`, `VIOLATION_OBSERVED`, `VIOLATION_CONFIRMED`, `ACKNOWLEDGED`,
`SAFE_OBSERVED`, `SAFE_CONFIRMED`, `RESET`), and `ResetBlocker` (see §3.3).

**Two invariants:**

1. **Alarm is "sticky".** A latched alarm requires the full 5-precondition reset;
   an ordinary `SAFE` never clears it (test `test_09_safe_frames_never_clear_a_latched_incident`).
2. **`UNKNOWN` is never `SAFE`.** Only a positive `goggles` observation advances
   clearance (test `test_03_unknown_never_becomes_safe`).

**Transitions (simplified):** `SAFE → VIOLATION_PENDING` on a violation;
`VIOLATION_PENDING → ALARM_LATCHED` when `elapsed ≥ confirmation_seconds`;
`ALARM_LATCHED → ACKNOWLEDGED` on `confirm_acknowledge()`;
`ALARM_LATCHED → SAFE_CLEAR_PENDING` on `SAFE`; `SAFE_CLEAR_PENDING →
SAFE_RECOVERED` when `elapsed ≥ clearance_seconds`; `SAFE_RECOVERED → SAFE` only
after the firmware accepts `RESET`.

### 3.2 Firmware state machine (`FwState`)

The ESP32 firmware (`firmware/esp32_eye_shield_monitor/incident_core.h`) has its
own **6-state** machine, kept deliberately simpler than the laptop's:

| State | Outputs |
|---|---|
| `Safe` | green, no incident |
| `AlarmLatched` | red + buzzer, incident open, not acknowledged |
| `Silenced` | red only, incident open, not acknowledged |
| `Acknowledged` | red only, incident open, acknowledged |
| `ReadyToReset` | red only, acknowledged, matching `RESET_READY` seen |
| `Fault` | outputs parked, refuses normal operation |

The firmware "only knows what it has been told and what it has latched" — it has
no concept of `WAITING_FOR_TARGET` or `SAFE_CLEAR_PENDING`, because those are
laptop-side reasoning about *detection*, which the board does not do.

### 3.3 Reset — five preconditions (checked in order)

`ResetBlocker(str, Enum)` in `incident_lifecycle.py` lists the five preconditions
that must all be satisfied before a reset is permitted:

1. `INCIDENT_ACTIVE` — an incident must be open.
2. `ACKNOWLEDGED` — the operator must have acknowledged.
3. `SAFE_RECOVERED` — recovery must be proven (clearance timer elapsed).
4. `HEALTH_VALID` — system health must be valid.
5. `LINK_VALID` — the serial link must be alive (fresh heartbeat).

The first missing condition is returned, so the operator sees the earliest unmet
precondition.

### 3.4 Serial protocol (command/reply table)

Line-oriented **ASCII text** over USB CDC at 115200 baud (JSON was proposed in
the plan but never built and is superseded by the text protocol). One command
per newline-terminated line; case-insensitive; max line 40 bytes.

**Laptop → ESP32 (lifecycle commands):**

| Command | Meaning | Requires open incident? |
|---|---|---|
| `HEARTBEAT` | link-alive proof; never changes state | no |
| `INCIDENT <id>` | confirmed violation; latches alarm | no (starts one) |
| `SILENCE <id>` | mute buzzer, red stays on | yes |
| `ACKNOWLEDGE <id>` | human saw alarm; mute buzzer, never clears red | yes |
| `NO_INCIDENT <id>` | clearance verdict; never clears red | yes |
| `RESET_READY <id>` | recovery proven; arms reset gate | yes |
| `RESET` | clear incident (only after `RESET_READY <id>`) | yes |
| `STATUS` | print human block + one machine-readable `STATUS` line | no |

**ESP32 → laptop replies (success):** `OK HEARTBEAT`, `OK INCIDENT`,
`OK SILENCED`, `OK ACKNOWLEDGED`, `OK NO_INCIDENT`, `OK RESET_READY`,
`OK RESET_ACCEPTED`, `OK ALL_OFF`, `OK GREEN_ON/OFF`, `OK RED_ON/OFF`,
`OK BUZZER_ON/OFF`.

**Refusals (each names the reason):** `ERR SAFE_REJECTED INCIDENT_ACTIVE`,
`ERR PRESET_DIAGNOSTIC_ONLY INCIDENT_ACTIVE <P>`,
`ERR SILENCE_REJECTED NO_INCIDENT`, `ERR ACKNOWLEDGE_REJECTED NO_INCIDENT`,
`ERR INCIDENT_REJECTED BAD_ARGUMENT`, `ERR INCIDENT_REJECTED ALREADY_OPEN`,
`ERR INCIDENT_ID_MISMATCH`, `ERR RESET_REJECTED NOT_ACKNOWLEDGED`,
`ERR RESET_REJECTED NOT_SAFE_RECOVERED`,
`ERR RESET_REJECTED LINK_OR_HEALTH_INVALID`, `ERR BAD_ARGUMENT <CMD>`,
`ERR UNKNOWN_COMMAND`, etc. The `RESET` refusal order is
`NO_INCIDENT → NOT_ACKNOWLEDGED → NOT_SAFE_RECOVERED → LINK_OR_HEALTH_INVALID`.

**Reply correlation.** The laptop acts on a reply **only** if it answers the
command it actually sent, with the expected reply code
(`CommandCorrelator` + `EXPECTED_REPLY_CODES`, one `OutstandingCommand` slot).
`HEARTBEAT` and `STATUS` are fire-and-forget and never claim the slot; an
unanswered command expires and marks the link degraded (never read as success).

### 3.5 Blynk integration (`src/blynk_client.py`)

Blynk Cloud is a **non-blocking, read-only** bridge. It degrades to a no-op when
disabled, so a missing `.env` or unreachable cloud never halts detection or the
serial link. Transport is **HTTPS REST only**:

```text
GET /external/api/update?token=<t>&v0=<value>        one datastream
GET /external/api/batch/update?token=<t>&v0=a&v1=b   many datastreams
GET /external/api/logEvent?token=<t>&code=<c>&description=<d>
```

Credentials live in a git-ignored `.env` (`BLYNK_AUTH_TOKEN`,
`BLYNK_SERVER=blynk.cloud`, `BLYNK_ENABLED=1`); the token is never hardcoded.

**Datastream mapping (V0–V10):**

| Pin | Purpose | Values |
|---|---|---|
| V0 | Current state | `"SAFE"`, `"ALARM_LATCHED"`, `"ACKNOWLEDGED"`, … |
| V1 | Incident ID | integer |
| V2 | Green LED | 1/0 |
| V3 | Red LED | 1/0 |
| V4 | Buzzer | 1/0 |
| V5 | Violation ratio | 0.00–1.00 (3 d.p.) |
| V6 | Last alert time | ISO-8601 |
| V7 | Link status | `"ONLINE"` / `"OFFLINE"` |
| V8 | Incident count | integer |
| V9 | Top confidence | 0.00–1.00 (3 d.p.) |
| V10 | FPS | float (1 d.p.) |

**"Option B" — physical GPIO mirroring (V2/V3/V4).** V2/V3/V4 mirror the
**physical GPIO state** (green/red/buzzer as an operator would see them), not the
state machine's "intent". This matters in `ACKNOWLEDGED` and `SAFE_CLEAR_PENDING`:
the operator has acknowledged, but hardware latches red on until the danger is
cleared, so V3 must stay `1`. Explicit `green`/`red`/`buzzer` values win when
present; otherwise a state table (`_STATE_OUTPUTS`) supplies the physical default.

**Engineering robustness:**

- **`_RateLimiter`** — minimum **0.25 s** between pushes, added to prevent Blynk
  (10 writes/sec/datastream) from dropping packets. A background worker
  `wait_for_slot=True` sleeps for the slot (off the detection thread); a
  synchronous call that cannot take a slot returns `SKIPPED` and **unequenches**
  the value so it is retried, never silently dropped.
- **`_CircuitBreaker`** — trips after **3 consecutive failures**; cooldown is
  geometric **30 s → 300 s**. While open, pushes return `BACKING_OFF`. 429/5xx →
  back off; other 4xx → config problem (bad token / missing datastream), logged
  once, not retried.
- **Per-incident deduplication** — `trigger_alert` fires the `eye_violation`
  event **once per `incident_id`**, only on the transition into `ALARM_LATCHED`,
  never per-frame. `_alerts_fired` records an id **only after** a successful
  (`status == OK`) HTTP delivery — a skipped/failed attempt is *not* blacklisted,
  so a later trigger can retry. `clear_alerts()` clears the local memory on
  `SAFE` so the next incident (even reusing an id) fires fresh.
- **Change-only pushes** — `_push_many` diffs against `_last_values` and skips
  unchanged values, so the dashboard updates but the network is not spammed.

**"Offline" label (expected, not a bug).** The Blynk console shows a device as
Online/Offline based on an MQTT/Blynk-Protocol heartbeat (`Blynk.run()`), *not*
on datastream writes. This project uses the HTTPS REST API, which writes values
but never opens that connection, so the console reads "Offline" even while
telemetry updates. This is cosmetic; `V7` "Link Status" (controlled by the
bridge) reflects the laptop's serial link and may legitimately disagree.

---

## 4. Testing & Results

### 4.1 Automated test suite

The suite runs with `.venv/bin/python -m pytest tests/ -q` and currently reports
**700 passed** (543 `def test_` functions across 21 files; parameterised cases
expand to 700). Breakdown by file:

| File | Tests |
|---|---|
| `test_dashboard_presentation.py` | 56 |
| `test_incident_core.py` | 54 |
| `test_incident_lifecycle.py` | 39 |
| `test_blynk_client.py` | 39 |
| `test_dashboard_adapters.py` | 36 |
| `test_config.py` | 36 |
| `test_dashboard_panels.py` | 32 |
| `test_v2_live_acceptance.py` | 30 |
| `test_serial_correlation.py` | 27 |
| `test_training_pipeline.py` | 26 |
| `test_live_eye_shield_timer.py` | 24 |
| `test_prepare_eye_region_baseline.py` | 23 |
| `test_inspect_dataset.py` | 23 |
| `test_dashboard_widgets.py` | 20 |
| `test_dashboard_window.py` | 18 |
| `test_dashboard_theme_and_branding.py` | 17 |
| `test_screen_construction_ppe.py` | 12 |
| `test_oled_status_model.py` | 12 |
| `test_select_reduced_candidates.py` | 11 |
| `test_sketch_preset_guard.py` | 4 |
| `test_lifecycle_integration_contract.py` | 4 |

**What the tests prove (the safety-critical claims):**

| Property | Test |
|---|---|
| No ordinary `SAFE` clears a latched incident | `test_09_safe_frames_never_clear_a_latched_incident` |
| `UNKNOWN` is never reported as `SAFE` | `test_03_unknown_never_becomes_safe` |
| Acknowledge silences the buzzer but keeps red | `test_13_acknowledge_silences_buzzer_but_keeps_red` |
| Reset refused without acknowledge | `test_19_reset_rejected_without_acknowledge` |
| Reset refused without proven recovery | `test_20_reset_rejected_without_safe_recovery` |
| Reset refused on stale `RESET_READY` token | `test_22_reset_rejected_when_link_invalid` |
| Completed handshake does not re-send `RESET` | `test_35_completed_handshake_does_not_re_send_reset` |
| `ALL_OFF` cannot extinguish a live alarm | `test_incident_core` — `ALL_OFF` refusal |

The two host-tested safety layers are the central claim: the firmware state
machine (`incident_core.h`, Arduino-free, 54 tests compiled from the real header)
and the operator lifecycle (`incident_lifecycle.py`, 39 tests) are driven by host
tests that assert against the **exact reply-string literals** (which live only in
`incident_core.h` under `namespace reply`), so the two ends cannot drift apart.

### 4.2 Hardware verification (physically observed)

Independently observed on the physical rig (release freeze `v0.9-working-demo`):

- YOLOv8n V2 weights (`models/best_v2.pt`) load successfully.
- Real safety goggles → `SAFE`; real `no_goggles` → violation.
- The 2-second temporal filter suppresses single-frame noise in both directions.
- Latched lifecycle transitions correctly (`VIOLATION_PENDING` → `ALARM_LATCHED`
  → … → `ACKNOWLEDGED` → `RESET`).
- Green LED (GPIO11), Red LED (GPIO12), Buzzer (GPIO14) all work.
- ESP32 serial link answers commands and carries the 1.0 s heartbeat.
- I2C OLED (SSD1306 128×64) status panel is **code-complete but hardware-unverified**
  (compiles with `OLED_ENABLED 1`, 352,058 B flash / 23,840 B RAM; the physical
  OLED was not wired at freeze).

Firmware compile result (from `docs/hardware_bringup.md`): **success, zero
warnings** — 308,594 B (23%) flash, 22,176 B (6%) RAM (without OLED). Upload at
115200 baud with flash hash verified (921600 failed).

### 4.3 Known limitations (honest evaluation)

- **B-01 Acknowledge desynchronisation bug (under investigation).** After
  `ALARM_LATCHED` the board could return to green while the laptop still showed
  `ACKNOWLEDGED: YES`, and a periodic `OK HEARTBEAT` could be mistaken for an
  acknowledge confirmation. The fix made acknowledge two-phase
  (`acknowledge()` → intent only; `confirm_acknowledge()` → the only path to
  `acknowledged = True`, requiring `reply.ok` and `code == "ACKNOWLEDGED"`), plus
  reply correlation (`CommandCorrelator`). **Status: corrected in the working
  tree but unverified on hardware** — the physical handshake remains unproven
  until re-tested.
- **V2 model is unpromoted.** `models/best.pt` (V1) remains the reference; V2 has
  no official acceptance (0/45 live trials).
- **ESP32 RAM constraints.** The board has ~520 KB RAM and no camera interface,
  so it can only *render* decisions — it **cannot run YOLO**. All inference is
  on the laptop.
- **Single camera viewpoint**; the demo must be run from the laptop.
- **B-02 firmware upload instability** — occasional chip-verification failure at
  921600 baud; IDE auto-selecting the wrong port with several USB serial devices.

---

## 5. GenAI Disclosure & Challenges

### 5.1 Major technical challenges overcome

| Challenge | Symptom / root cause | Fix |
|---|---|---|
| **Rate limiter dropping packets (Blynk)** | The rate limiter could refuse a push and return early without forgetting the about-to-be-published value; the discarded state change was cached as "published" and never re-sent, leaving the cloud stale | `_request` now **unequenches** (forgets) the cached value of every affected pin on a rate-limited/failed push, so the next frame re-diffs and retries |
| **Missing auth token in `logEvent` (Blynk)** | Blynk has no header form for the token on this API; a `logEvent` built without the token is rejected as unauthorised | The token is attached to **every** request (including `logEvent`) in one central place (`_request`) |
| **Local anti-spam bug in `trigger_alert`** | `_alerts_fired` recorded an `incident_id` even when the first attempt was `skipped`/failed, permanently blacklisting it so the phone never got a notification | Only record the id **after** a successful (`status == OK`) delivery; add `clear_alerts()` on `SAFE` |
| **Acknowledge desynchronisation / `OK HEARTBEAT` mistaken for ACK (B-01)** | `acknowledge()` flipped a local flag with zero `ACKNOWLEDGED` lines on the wire | Two-phase acknowledge + `CommandCorrelator` reply matching (§4.3) |
| **`TIMER: 0.00 s` on a confirmed incident** | A single `timer_seconds` was reused for every state, so a confirmed incident showed 0.00 | `TemporalStatus` now carries one named field per timer, each `None` when inapplicable; `confirmed_after_seconds` frozen once at confirmation |
| **`SAFE`/`ALARM`/`SILENCE` treated as pin presets (safety defect)** | `handleCommand()` consulted the preset table before the lifecycle parser, so `ALARM` drove pins while `IncidentCore` stayed `Safe` with `incident_id=0` | Those words now reach `IncidentCore`; presets are refused while an incident is open (`ERR PRESET_DIAGNOSTIC_ONLY INCIDENT_ACTIVE <P>`) |
| **Latched alarm reverting (safety)** | A latched alarm could revert to a non-latched state | Commits `0a2f9a0` (two-phase ACK + sticky-latch invariant) and `ec5241c` (reset precondition chain) |
| **`zipfile.ZipFile(path, "rb")`** | The auditor crashed with `ValueError` (mode must be `"r"`) | Fixed to `"r"`; exception handler widened |
| **Image/label pairing on a normalised stem** | Two images sharing a normalised stem collapsed into one key, reporting 13 phantom missing labels and undercounting instances (755 vs 770) | Pairing uses the exact stem; normalisation only for leakage grouping |

### 5.2 How GenAI was used in development

Per `docs/ai_usage_log.md`, a generative-AI coding assistant (Copilot SDK in
VS Code) acted as the principal engineer under the team's review. Its
contributions, each **reviewed and verified** by a team member before being
relied upon:

- **Boilerplate generation** — repository scaffolding, `.gitignore`,
  `.env.example`, `requirements.txt`, `config/app.example.yaml`,
  `config/classes.yaml`, README, and the Phase 0 subset of `docs/`.
- **Implementation** — `src/config.py` (typed/bounded config), `src/blynk_client.py`
  (the Blynk bridge), `tools/inspect_dataset.py` (read-only auditor),
  `tools/incident_lifecycle.py`, and the dashboard modules under `src/dashboard/`.
- **Test-case authoring** — `tests/test_config.py` (36), `tests/test_blynk_client.py`
  (39), `tests/test_inspect_dataset.py` (23), `tests/test_incident_core.py` (54),
  `tests/test_incident_lifecycle.py` (39), and the dashboard test files.
- **Documentation** — `docs/` (architecture, protocol, model_training, dataset_audit,
  blynk_setup, hardware_bringup, decision_log, ai_usage_log, test_plan).

**Verification discipline (for Appendix A):** every AI-produced change was
verified by running code, not by re-reading it. Examples of defects caught this
way: the `zipfile` mode bug, the stem-pairing bug, three tests asserting a
non-existent `contact_sheets` keyword, `serial.port` silently coerced instead of
validated, and `env_check.py` printing `python-dotenv==?` while still passing.
No AI-produced metric was ever reported without tracing it to a raw record in
`experiments/` or a captured test run; unverified output was never promoted to a
reported result.

### 5.3 Key files (for citation)

| Layer | File |
|---|---|
| Operator lifecycle / state machine | `tools/incident_lifecycle.py` |
| Live demo, temporal filter, YOLO inference, serial correlation | `tools/live_eye_shield_demo.py` |
| Blynk bridge | `src/blynk_client.py` |
| Config (typed/validated) | `src/config.py` |
| Firmware state machine (Arduino-free) | `firmware/esp32_eye_shield_monitor/incident_core.h` |
| Firmware sketch | `firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino` |
| Serial protocol spec | `docs/protocol.md` |
| V2 fine-tuning runbook | `docs/domain_v2_finetuning.md` |
| V2 live acceptance | `docs/v2_live_acceptance.md` |
| Decision log / AI usage log | `docs/decision_log.md`, `docs/ai_usage_log.md` |
