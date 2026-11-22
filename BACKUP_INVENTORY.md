# BACKUP INVENTORY — Smart Eye-Shield Monitor

What is in the repository at the frozen commit, how big it is, and what must be
backed up **outside** Git.

- **Frozen commit:** `ec5241cb22c0de8da39fabe6795842f1c1d8b946`
- **Date:** 2026-11-22
- **Sizes:** measured with `du -sh` at freeze time; they are indicative, not exact.

---

## 1. What Git does and does not protect

| Category | In Git? | Needs separate backup? |
|---|---|---|
| Source, tests, docs, firmware, dashboard | yes | no |
| `models/best.pt` | **no** — ignored (`models/*.pt`) | **yes, mandatory** |
| `models/best_v2.pt` | **no** — ignored (`models/*.pt`) | **yes, mandatory** |
| Datasets under `data/` | mostly ignored (payloads) | yes, if you need to retrain |
| `config/app.yaml` | **no** — ignored (machine-specific) | yes, it is hand-edited |
| `.env` | **no** — ignored (secrets) | yes, if used |
| `.venv/` | no | no — rebuild from `requirements.txt` |
| `runs/`, `training/runs/`, `firmware/**/build/` | no | no — regenerable |

**The single most important fact in this document:** the working model weights
are *not* in Git. A `git clone` alone cannot restore this project.

---

## 2. Top-level directories

| Directory | Size | Tracked? | Purpose |
|---|---|---|---|
| `src/` | 144 KB | yes | Application library: camera, detector, temporal filter, event logging, overlay, health monitor, serial protocol/transport |
| `tools/` | 1.2 MB (752 KB is `__pycache__`) | partly | Executable scripts: live demo, incident lifecycle, acceptance runner, dataset builders, serial probes, dashboard server |
| `tests/` | 1.5 MB | yes | Pytest suite — 381 tests at the frozen commit |
| `firmware/` | 19 MB (**19 MB is `build/`**) | partly | ESP32-S3 sketch, Arduino-free core, host harness, prebuilt binaries |
| `dashboard/` | 32 KB | **no — untracked** | Web dashboard: HTML UI, launcher, docs |
| `docs/` | 232 KB | yes | Protocol, architecture, decision log, bring-up, troubleshooting, acceptance reports |
| `config/` | 12 KB | partly | `app.example.yaml`, `classes.yaml` tracked; `app.yaml` ignored |
| `experiments/` | 16 KB | partly | Metrics CSVs; `v2_live_acceptance.csv` must stay header-only |
| `data/` | 736 MB | mostly ignored | Datasets, splits, review artefacts, test cases |
| `models/` | 12 MB | **no** (only `README.md`) | YOLOv8n weights |
| `training/` | — | partly | Training scripts/configs; run outputs ignored |
| `runs/` | — | no | Ultralytics run output |

---

## 3. Models — mandatory separate backup

| Path | Size | SHA-256 | Git status | Protection |
|---|---|---|---|---|
| `models/best.pt` | 6,508,202 B | `9366b73b35d4f820af401808aa4e0e58cb29a7b557991b123e5cd6a813fa3591` | ignored, **untouched by this task** | local file present, matches expected digest |
| `models/best_v2.pt` | 6,501,866 B | `fec02c88f4236d85d4e913df57638a96cf0a35498f6a65c413b7fe14077f22fc` | ignored, **untouched by this task** | local file present |

`models/README.md` **is** tracked — it documents the model contract (class 0
`goggles`, class 1 `no_goggles`) that the loader enforces.

Neither weight file was opened, modified, re-saved or re-hashed in any
destructive way. `shasum -a 256` is read-only.

---

## 4. Firmware

| Path | Size | Purpose |
|---|---|---|
| `firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino` | 28 KB | Platform layer: pin setup, serial command dispatch, status/help printing |
| `firmware/esp32_eye_shield_monitor/incident_core.h` | 28 KB | Arduino-free C++ state machine — the authoritative board-side lifecycle |
| `firmware/esp32_eye_shield_monitor/config.example.h` | 8 KB | Template for the ignored `config.h` (pins, polarity, buzzer mode) |
| `firmware/esp32_eye_shield_monitor/test/incident_core_harness.cpp` | 4 KB | Host-side harness for the core |
| `firmware/esp32_eye_shield_monitor/README.md` | 12 KB | Wiring, commands, self-test |
| `firmware/esp32_eye_shield_monitor/build/` | **19 MB** | `arduino-cli` output including a prebuilt `.merged.bin`. Ignored, regenerable — **the bulk of the repository's non-data size** |

Hardware values in force: green GPIO11, red GPIO12, buzzer GPIO14 active-LOW.

---

## 5. Tests

| Path | Size | Purpose |
|---|---|---|
| `tests/` (root files) | 1.5 MB | Full suite across dataset, model, temporal filter, lifecycle, protocol and integration contracts |
| `tests/test_incident_lifecycle.py` | — | 42 lifecycle tests; includes command-string assertions (`INCIDENT 1`, `ACKNOWLEDGE 1`) |
| `tests/test_lifecycle_integration_contract.py` | — | AST guard: every `lifecycle.<method>()` call in the demo must exist on the class |

Frozen result: **381 passed in ~10.5 s**.

---

## 6. Dashboard

| Path | Size | Purpose |
|---|---|---|
| `dashboard/index.html` | 16 KB | Live UI: state card, FPS, health dots, LED/buzzer indicators, reconnect logic |
| `dashboard/start.sh` | 4 KB | Auto-installs `websockets` if missing, launches the server |
| `dashboard/README.md` | 8 KB | How it works |
| `dashboard/QUICKSTART.md` | 4 KB | Fast path |
| `tools/dashboard_server.py` | 8 KB | WebSocket server on `ws://localhost:8765`, 20 FPS |

`websockets` is **not** in `requirements.txt`; `start.sh` installs it on demand.

---

## 7. Docs, configs and scripts

**Docs** (`docs/`, 232 KB): `architecture.md`, `protocol.md`,
`hardware_bringup.md`, `demo_runbook.md`, `decision_log.md`, `test_plan.md`,
`troubleshooting.md`, `model_training.md`, `dataset_audit.md`,
`eye_region_baseline.md`, `domain_v2_finetuning.md`, `v2_live_acceptance.md`,
`construction_ppe_candidate_selection.md`, `construction_ppe_suitability.md`,
`annotation_guideline.md`, `ai_usage_log.md`, `blynk_setup.md` (reference only —
Blynk is not implemented), plus the four release documents in the repo root.

**Configs** (`config/`, 12 KB): `app.example.yaml` tracked, `classes.yaml`
tracked (class order is a safety contract), `app.yaml` ignored and
machine-specific.

**Scripts** (`tools/`): `live_eye_shield_demo.py` (the demo, 68 KB),
`incident_lifecycle.py` (36 KB), `run_v2_live_acceptance.py`,
`summarise_v2_live_acceptance.py`, `v2_acceptance_schema.py`, `env_check.py`,
`camera_check.py`, `measure_latency.py`, `export_demo_logs.py`,
`build_domain_v2_dataset.py`, `capture_domain_images.py`,
`prepare_eye_region_baseline.py`, `inspect_dataset.py`, `screen_construction_ppe.py`,
`select_reduced_candidates.py`, `serial_mock.py`, `dashboard_server.py`,
`test_esp32_quick.py`, `test_esp32_serial.py`.

---

## 8. Untracked and modified files at freeze time

### Modified (tracked, uncommitted)

| File | Nature |
|---|---|
| `tools/incident_lifecycle.py` | In-progress acknowledge-handshake correction (`ACKNOWLEDGE_REQUESTED`, `confirm_acknowledge`) plus `INCIDENT <id>` / `ACKNOWLEDGE <id>` / `RESET` on the wire |
| `firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino` | `needs_id` corrected; status line + help text |
| `firmware/esp32_eye_shield_monitor/incident_core.h` | Core changes for the same handshake |
| `tests/test_incident_lifecycle.py` | Command-string assertions updated |
| `tools/live_eye_shield_demo.py` | HEARTBEAT restored; `[LIFECYCLE STATE]` / `[LIFECYCLE STATUS]` / `[LIFECYCLE TX]` / `[ESP32 RX]` diagnostics |

These are the in-flight `B-01` work. They pass the test suite but are **not
hardware-verified** — the corrected firmware was never flashed.

### Untracked (not in Git)

| Path | Recommendation |
|---|---|
| `dashboard/` | **Commit** — verified working UI, part of the release |
| `tools/dashboard_server.py` | **Commit** — required by `dashboard/` |
| `tools/test_esp32_quick.py`, `tools/test_esp32_serial.py` | **Commit** — the serial probes used in this guide |
| `tools/build_domain_v2_dataset.py`, `tools/capture_domain_images.py` | **Commit** — real tooling referenced by the V2 docs |
| `data/processed/eye_shield_domain_v2/data.yaml` | **Leave untracked** — the only untracked file in that directory; records a machine-specific absolute `path:` (the same reason the baseline `data.yaml` is deliberately not committed). Its 602 images/labels are already ignored. |
| `data/processed/eye_shield_baseline/data.yaml` | **Leave untracked** — machine-specific absolute path, deliberately not committed |
| `.tmp/` | **Delete or gitignore** — see below |

### `.tmp/` — 3.6 MB of scratch output, **not ignored**

`git check-ignore .tmp` returns nothing, so this directory is *untracked, not
ignored*. It holds two unrelated things:

- `.tmp/pytest-of-hermanyeung/` — pytest's scratch tree (~630 files). Pure output.
- `.tmp/evidence/` — two hand-captured evidence files, `before.txt` and
  `eyeshield_log.txt`, kept from earlier debugging.

Recommendation: **delete** `.tmp/pytest-of-hermanyeung/` (regenerated on every run),
**move** `.tmp/evidence/` somewhere durable if it matters, and add `.tmp/` to
`.gitignore` so it can never be committed by accident. Nothing here was deleted
by this task.

### Backup/demo leftovers in `tools/`

`live_eye_shield_demo_before_timer_fix.py`,
`live_eye_shield_demo_phase3_backup.py`, `live_eye_shield_demo_v1_backup.py`.

These are **tracked and unmodified** (no diff at freeze time). They are historical
copies of the demo from earlier phases. Recommendation: **keep** — cheap, already
committed, and useful history. Do not delete; deletion would be a behaviour-neutral
but unnecessary change to the frozen tree.

### Ignored but important

`config/app.yaml` (ignored, present), `models/*.pt`, `runs/`, `training/runs/`,
`firmware/**/build/`, `.venv/`, `.DS_Store`, `.pytest_cache/`.

`firmware/esp32_eye_shield_monitor/config.h` is **ignored and does not currently
exist** — the sketch falls back to the defaults in `config.example.h`, which is
what the working build used. If you ever create it with custom pin or polarity
overrides, back it up separately. `.env` is also ignored and **not present** at
freeze time.

---

## 9. What to back up now

Priority order:

1. **`models/best.pt` and `models/best_v2.pt`** — not in Git, unrecoverable if lost.
2. **`config/app.yaml`** — hand-edited and ignored. (The firmware's `config.h`
   does not currently exist; the sketch uses `config.example.h` defaults.)
3. **A full ZIP of the working tree** — captures the uncommitted `B-01` work,
   the dashboard and the weights in one file.
4. **`data/processed/eye_shield_domain_v2/`** — only if you may retrain.

`data/` is 736 MB, of which the processed datasets dominate. Exclude it from the
ZIP unless you have the space; the model does not need it at runtime.

Commands are in [FINAL_WORKING_VERSION.md](./FINAL_WORKING_VERSION.md) and
repeated in the release summary provided with this freeze.
