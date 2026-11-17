# Decision log

Append-only record of engineering decisions. Each entry states the decision, why
it was made, and what would justify revisiting it. Numeric claims must reference
an experiment record or test run; do not record unmeasured results here.

---

## D-001 — Python 3.12 as the supported runtime

**Date:** 2026-11-17 (Phase 0)
**Decision:** Pin the project to Python 3.12; reject 3.13+ in `tools/env_check.py`.
**Why:** PyTorch 2.15.0 and Ultralytics 8.4.215 were installed and verified against
Python 3.12.4 on this machine. The host defaulted to Python 3.13.9 (Anaconda), where
the pinned stack is neither installed nor verified.
**Revisit if:** a verified 3.13+ wheel set exists and the full test suite passes there.

## D-002 — Pinned dependency set

**Date:** 2026-11-17 (Phase 0)
**Decision:** Pin `ultralytics==8.4.215`, `torch==2.15.0`, `torchvision==0.29.2`,
`numpy==2.5.5`, `opencv-python==5.0.0.93`, `PyYAML==6.0.3`,
`python-dotenv==1.2.4`, `pyserial==3.5`, `pytest==9.2.0`.
**Why:** These are the versions that actually installed together and were
smoke-tested (a YOLOv8n architecture ran a forward pass and returned an empty
detection set). OpenCV 5.0 was explicitly smoke-tested with the YOLO predictor
rather than assumed compatible.
**Revisit if:** an upgrade is required, in which case re-run `tools/env_check.py`
and the full test suite before re-pinning.

## D-003 — Local weights are git-ignored; `models/best.pt` is not committed

**Date:** 2026-11-17 (Phase 0)
**Decision:** Ignore `models/*.pt` in Git while allowing the path to exist.
**Why:** Plan §4 forbids committing large model artifacts without an explicit
requirement. The demo laptop must still have the file locally, so the path and
contract are documented in `models/README.md`.
**Revisit if:** the submission format requires an embedded model; then the
training artifacts are attached to the report instead of the repository.

## D-004 — `config/app.yaml` is local-only; the example is committed

**Date:** 2026-11-17 (Phase 0)
**Decision:** Commit `config/app.example.yaml`, ignore `config/app.yaml`.
**Why:** The live config is machine-specific (camera index, serial port) and is the
natural place a student might paste a key. Keeping it untracked prevents accidents.
Startup and `tools/env_check.py` both fail with a copy-paste instruction if it is
missing, so this cannot silently be forgotten.

## D-005 — Fail fast on configuration, with the exact key named

**Date:** 2026-11-17 (Phase 0)
**Decision:** All config values are typed and range-checked in `src/config.py`;
errors are `ConfigError` messages naming `section.key` and the allowed range.
**Why:** Plan §14 requires invalid values to produce a concise startup error rather
than silent unsafe behaviour. A ratio of `1.4` or a negative duration would change
whether an incident is created, so refusal is the only safe response.

## D-006 — Class map is source code, not data

**Date:** 2026-11-17 (Phase 0)
**Decision:** `config/classes.yaml` defines class IDs, names, and compliance meaning;
`unmapped_class_policy` must be `unknown`.
**Why:** Swapping classes 0 and 1 silently inverts safe and unsafe. The loader
rejects non-contiguous IDs, duplicate names, missing safe/violation coverage, and
any policy other than `unknown`.

## D-007 — Media is never committed; split manifests are

**Date:** 2026-11-17 (Phase 0)
**Decision:** Ignore images/video/archives and dataset label files tree-wide, while
committing `data/splits/*.csv|json|yaml` and folder READMEs.
**Why:** Plan §4 forbids committing private raw images. The split manifest and
class statistics are small text records that must be auditable and reproducible.

---

## Open decisions (to be recorded when resolved)

| ID | Question | Earliest phase |
|---|---|---|
| O-01 | Final confidence threshold (validation-selected, then frozen) | 2 |
| O-02 | Whether LED module is active-high or active-low | 5 |
| O-03 | NVS persistence of incident state across reboot | 5 |
| O-04 | Exact Blynk virtual-pin allocation | 6 |
| O-05 | YOLOv8n vs. an alternative, based on measured comparison | 2 |
