# Operations dashboard

A PySide6 desktop view of the Smart Eye-Shield monitor: the live camera with
detections drawn on it, the compliance verdict, the ESP32's own pin states, the
incident record, subsystem health, session telemetry, an event timeline, and the
operator controls.

The dashboard is **presentation only**. It does not contain a single safety
rule of its own. Every verdict, every transition, every reset precondition comes
from [`tools/incident_lifecycle.py`](../tools/incident_lifecycle.py), and every
serial command is the same one
[`tools/live_eye_shield_demo.py`](../tools/live_eye_shield_demo.py) sends,
produced by the same `serial_command_for_transition()` and gated by the same
`CommandCorrelator`.

## Why it exists

The CLI demo already shows the state, the timer and the lamp order in a terminal
overlay. What it cannot show is the three-way distinction this project keeps
having to make:

| Question | Where the dashboard answers it |
| --- | --- |
| What does the laptop believe? | Safety state panel, lifecycle diagram |
| What did we ask the board to do? | ESP32 node panel — *Requested* column |
| What did the board reply? | ESP32 node panel — *Acknowledged* column |
| What are the pins actually doing? | ESP32 node panel — *Physical* column, from `STATUS` |

Those last three disagreeing **is** the class of bug this project has spent the
most time on. Putting them side by side makes a disagreement visible in one
glance instead of inferable from a log.

## Running it

The dashboard is optional; nothing else in the project needs it. Install Qt once:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Demo-data mode — no camera, no serial port, deterministic fixtures. Use this for
screenshots and for confirming the UI works before touching hardware:

```bash
.venv/bin/python tools/run_dashboard.py --demo-data
```

Live, with the same detector settings as the demo:

```bash
.venv/bin/python tools/run_dashboard.py \
  --source 0 \
  --weights models/best_v2.pt \
  --candidate-confidence 0.40 \
  --goggles-confidence 0.60 \
  --no-goggles-confidence 0.55 \
  --iou 0.30 \
  --device auto \
  --serial-port /dev/cu.usbserial-10
```

View-only, deliberately unable to drive the board:

```bash
.venv/bin/python tools/run_dashboard.py --source 0 --weights models/best_v2.pt
```

Omit `--serial-port` and the window says `NO SERIAL PORT` in the banner for its
whole life. That is not a warning to be dismissed: it means the laptop is
observing and the lamps are being driven by whatever ran last.

`--max-runtime N` closes the window after N seconds, which is how the automated
smoke test keeps a GUI run bounded.

### Before a demo

Run the reset helper first, exactly as for the CLI demo:

```bash
.venv/bin/python tools/pre_demo_reset.py --port /dev/cu.usbserial-10
```

See [demo_startup.md](./demo_startup.md). The dashboard never clears a latched
incident on startup or on exit, because an open incident is closed by the
`ACKNOWLEDGE` → `RESET_READY` → `RESET` handshake and by nothing else.

## Operator controls

| Key | Button | What it does |
| --- | --- | --- |
| `A` | Acknowledge | Sends `ACKNOWLEDGE <id>`. Silences the buzzer. **Does not** restore green. |
| `S` | Silence | The same lifecycle request; the firmware silences the buzzer on acknowledgement. |
| `R` | Request reset | Sends `RESET_READY <id>`, then `RESET` when the board answers. Only offered when every precondition holds. |
| `Ctrl+E` | Export | Writes the session timeline to CSV. |
| `P` | Presentation | Enlarges type and hides the analytics tab. A view change only. |
| `Esc` | — | Leaves presentation mode, then fullscreen. |
| `T` | Status | Asks the board for a fresh `STATUS`. |
| `F1` | Help | Key reference. |

A disabled button is not a UI preference. It means the lifecycle would refuse
the request, and the reason is printed underneath in the reset-gate line.

Closing the window sends **no** `ALL_OFF` and **no** `RESET`. The outputs are
left exactly as the board reports them.

## Reading the colours

Green is a compliance claim, so exactly one state may use it.

| State | Accent | Meaning |
| --- | --- | --- |
| `SAFE` | green | Verified compliant. The only green on the screen. |
| `VIOLATION_PENDING` | amber | A violation is being timed, not yet confirmed. |
| `ALARM_LATCHED` | red | Confirmed. Red + buzzer, latched, needs a human. |
| `SAFE_CLEAR_PENDING` | red | Recovery is being timed. Still red, still an open incident. |
| `SAFE_RECOVERED` | amber | Recovery proven. Reset is now permitted. |
| `ACKNOWLEDGED` | orange | A human answered. Buzzer silenced, red stays on. |
| `FAULT` | magenta | A safety input is unusable. Outputs parked. |

The full contract, state by state, is in
[`SYSTEM_STATE_REFERENCE.md`](../SYSTEM_STATE_REFERENCE.md).

## Honesty rules the panels obey

These are the rules the tests enforce; breaking one is a bug, not a taste
question.

1. **`SAFE` is never inferred.** Before a `STATUS` line has arrived, the pins
   read `UNKNOWN`, not `off`. An empty frame is missing information, and the
   gauge shows `UNKNOWN` rather than 0 %.
2. **The lamp panels report the board.** Green/red/buzzer come from
   `STATUS GREEN=… RED=… BUZZER=…`. A dashboard that lights its own green from
   the lifecycle verdict is only echoing its own belief.
3. **Only the board's reply acknowledges.** A keypress logs
   `ACKNOWLEDGE_REQUESTED` and sets `acknowledge_pending`; the incident becomes
   acknowledged when `OK ACKNOWLEDGED` arrives for the right incident id. A
   heartbeat can never confirm an acknowledgement.
4. **A stale `STATUS` is not a healthy link.** Past `STALE_STATUS_SECONDS`
   (5 s — the firmware's own heartbeat window is 3 s) the serial row degrades.
5. **A dropped command stays queued.** If the port is missing or the correlator
   is busy, the command is re-queued rather than dropped, and the banner says so.
6. **Demo data says so.** With `--demo-data` the banner reads `DEMO DATA` for the
   whole session. There is no way to make fixture data look live.
7. **Model figures are read, never computed.** The model evidence panel reads
   `experiments/ai_metrics.csv` from the locked test split.

## Layout

The window is two tabs rather than one long page. Everything an operator needs
while watching a lamp is on **LIVE OPERATIONS**; everything that is only
interesting afterwards is on **ANALYTICS & EVIDENCE**.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ HeaderBar    Smart Eye-Shield Safety Operations Centre      [logos]      │
├──────────────────────────────────────────────────────────────────────────┤
│ Chip strip   DEMO DATA · LOGO MISSING · BLYNK NOT CONFIGURED · YELLOW …  │
├─ LIVE OPERATIONS ─┬─ ANALYTICS & EVIDENCE ───────────────────────────────┤
│ CameraPanel       │ SafetyStatePanel                │ HardwarePanel      │
│                   ├─────────────────────────────────┤  lamps+provenance  │
│                   │ IncidentPanel                   ├────────────────────┤
│                   │                                 │ HealthPanel        │
├───────────────────┴─────────┬───────────────────────┴────────────────────┤
│ LifecycleDiagram            │ EventTimeline (meaningful transitions only) │
│ + operator action buttons   │                                             │
├─────────────────────────────┴────────────────────────────────────────────┤
│ Footer   tab · state · link · port · model · frames   P presentation      │
└──────────────────────────────────────────────────────────────────────────┘
```

The analytics tab is the rolling telemetry (`ChartsPanel`: FPS, latency,
per-class confidence, a duty gauge, session counters, state durations), the
model evidence read from `experiments/ai_metrics.csv`, and the unfiltered event
record. Charts are fed continuously even while the tab is hidden — switching
tabs is a paint operation, never a replay.

The live tab is designed to fit a 1080p viewport without scrolling. It is
measured, not assumed: `test_dashboard_presentation.py` resizes the window to
1920x1080 and asserts the scroll bar stays hidden **in both normal and
presentation mode**. The assertion compares the rendered page height against the
viewport, because panels demand height as a function of their width - a passing
`minimumSizeHint()` is not proof the page fits.

Below 1080p the page is genuinely too tall to compress further, and it scrolls.
That is deliberate: a clipped reset gate would be worse than a scroll bar. At
1440x900 the page needs roughly 920 px against a ~709 px viewport.

**Presentation mode (`P`)** goes fullscreen, scales the type up 1.35x and hides
the analytics tab. It also hides the **tab bar**: with one page left a single tab
is a dead control, and dropping it is what pays for the larger fonts so 1080p
stays scroll-free. It touches no lifecycle state, no correlator and no worker,
so toggling it mid-incident is safe.

**The banner** carries one condition, chosen by consequence rather than by
whichever check ran last:

| Priority | Condition | Colour |
| --- | --- | --- |
| 1 | `DEMO DATA` (fixtures, not a live safety system) | amber |
| 2 | `FAULT` — a safety input is unusable, outputs parked | magenta |
| 3 | No serial port / serial link down | red |
| 4 | Camera unavailable | red |
| 5 | Firmware `STATUS` stale beyond 5 s | orange |
| 6 | Advisory: metrics unavailable, branding logo missing | amber |

Advisory conditions never overwrite an urgent one; a banner that mixes "no
serial port" with "logo missing" trains the operator to ignore both.
Unimplemented Blynk and the unwired yellow lamp are chips, not banners.

`SignalFlowWidget` shows where the chain breaks — camera → detector → filter →
lifecycle → serial → ESP32 → outputs. It names the **first** dead node, not the
last symptom: when serial is down, the ESP32 and the outputs are also dead, and
reporting "outputs stopped" would send the operator to the wrong end of the
cable.

## Code map

| Module | Responsibility |
| --- | --- |
| [`app.py`](../src/dashboard/app.py) | `QApplication` bootstrap and the argument surface. |
| [`main_window.py`](../src/dashboard/main_window.py) | Wiring: workers, timers, command drain, banner, export. |
| [`workers.py`](../src/dashboard/workers.py) | Camera/inference and serial threads. Nothing else touches a device. |
| [`adapters.py`](../src/dashboard/adapters.py) | Narrow adapters over the core: `STATUS` parsing, health rows, metrics CSV, demo fixtures. |
| [`state_models.py`](../src/dashboard/state_models.py) | Qt-free snapshot value objects shared with the tests. |
| [`theme.py`](../src/dashboard/theme.py) | Palette, `STATE_STYLES`, stylesheet. |
| [`branding.py`](../src/dashboard/branding.py) | PolyU / EIE logo handling and the placeholder. |
| [`widgets.py`](../src/dashboard/widgets.py) | Card, MetricTile, Dot, StatusPill, KeyValueRow. |
| [`lifecycle_diagram.py`](../src/dashboard/lifecycle_diagram.py) | The state machine drawn in a `QGraphicsScene`; bounded height so the page fits. |
| [`signal_flow.py`](../src/dashboard/signal_flow.py) | Seven-node causal chain and the first-break resolver. |
| [`event_timeline.py`](../src/dashboard/event_timeline.py) | Dual view: meaningful transitions, plus the raw serial record on demand. |
| the nine `*_panel.py` modules | One concern each; `update_from(snapshot)` and nothing more. |

`main_window.py` imports `IncidentLifecycle`, `CommandCorrelator`,
`parse_serial_reply` and `expected_code_for` from the existing tools rather than
copying them. If a safety rule changes, the dashboard changes with it.

## Branding assets

`assets/branding/POLYU_LOGO` and `EIE_LOGO` are not committed — the university
marks are not redistributable from this repository. Without them the header
shows a neutral placeholder and the health panel reports the logo row as
`DEGRADED`. Drop the real files in and the placeholder disappears; see
[`assets/branding/README.md`](../assets/branding/README.md).

## Tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_dashboard_*.py -q
```

`tests/test_dashboard_presentation.py` is the visual-contract suite: tabs,
presentation mode (including tab-bar hiding and the button object-name/selector
pairing), chip visibility, banner priority ordering, the 7-node flow, the
diagram's bounded height, timeline filtering, and the 1080p no-scroll layout
assertion in both modes.

The suites are Qt-free where possible (`adapters`, `theme`, `branding`) and use
a single session-scoped `QApplication` from [`tests/conftest.py`](../tests/conftest.py)
otherwise. The window fixtures construct `MainWindow(autostart=False)`: a window
built with `autostart=True` inside pytest starts real camera threads, which
crashes the interpreter rather than failing a test.

`autostart=False` also stops the window from claiming health it cannot know, so
the live-window fixture declares camera and model health explicitly. A real
camera failure still faults — that path is unchanged.

## Known limitations

- **No remote, no Blynk.** Wi-Fi and Blynk are not part of this build; the
  health panel reports them `NOT CONFIGURED` rather than offline.
- **The dashboard cannot make the board safe.** It can only ask. If the link is
  down, the firmware raises `FAULT` on heartbeat loss, which is the correct
  behaviour and not something the UI can override.
- **One window, one board.** No multi-node support.
- **Export is a CSV of the on-screen timeline**, capped at the last 300 rows.
