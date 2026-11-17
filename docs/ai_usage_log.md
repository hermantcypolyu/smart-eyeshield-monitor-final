# AI usage log

Record of generative-AI assistance, as required for academic integrity and
reviewability. **Every AI-produced change must be reviewed and verified by a team
member before it is relied upon.** Unverified AI output must never become a
reported result.

## Entry template

```text
Date:
Phase:
Tool/agent:
Task requested:
Files affected:
Output accepted as-is / modified / rejected:
How it was verified (command, test, manual check):
Team reviewer:
```

---

## 2026-11-17 — Phase 0: repository and reproducibility

**Tool/agent:** AI coding assistant (Copilot SDK in VS Code), acting as principal
engineer per `plan.md §1`.
**Task requested:** Create the Phase 0 repository structure and baseline files,
install and verify the Python environment, run the Phase 0 checks, and report
files, assumptions, tests, and blockers. Stop before Phase 1.

**Work performed:**
- Created the §4 directory skeleton with `.gitkeep` placeholders.
- Wrote `.gitignore`, `.env.example`, `requirements.txt`, `config/app.example.yaml`,
  `config/classes.yaml`, `README.md`, and the Phase 0 subset of `docs/`.
- Implemented `src/config.py` (typed, bounded configuration loading).
- Implemented `tools/env_check.py` and `tools/camera_check.py`.
- Added `tests/test_config.py` covering the configuration contract.
- Created `.venv` with Python 3.12.4 and installed the pinned dependency set.

**Verification actually performed:**
- `pip install -r requirements.txt` completed with exit code 0, in the working tree
  and again in a fresh `git clone` into a temporary directory.
- `tools/env_check.py` → `Environment check OK: 5 passed, 1 warning(s), 0 failure(s)`.
- `python -m pytest -q` → `108 passed`.
- `tools/camera_check.py` → `Camera check PASSED.`, 29.9–30.1 FPS at 1280×720.
- Ultralytics smoke test: YOLOv8n architecture constructed and ran a forward pass
  on a synthetic 640×640 image, returning 0 detections.
- Two deliberate failure paths: missing `config/app.yaml` (readable `FAIL`, exit 1)
  and camera index that does not exist (readable `ERROR`, exit 1).

**Defects this verification caught (and corrected):**
- `serial.port` was silently coerced instead of validated; a blank or non-string
  value could have triggered unintended autodiscovery (`src/config.py`).
- `tools/env_check.py` printed `python-dotenv==?` while still passing, because that
  package has no `__version__`; it now reads installed metadata.
- `.gitignore` allowed verified-bad re-inclusions and never ignored `*.jpg`;
  model weights were briefly re-included by a negation, contradicting plan §4.
- `tools/camera_check.py` printed a mojibake FOURCC (`'ÿÿÿÿ'`) on macOS.

**Not verified (and therefore not claimed):**
- No model weights exist; no inference accuracy is measured or claimed.
- No ESP32, Blynk, or serial-hardware behaviour has been tested.

**Team action required:** Each team member must read the source files they will
present, re-run the Phase 0 checks on their own machine, and record their own
verification entry below.

---

## Team verification entries

_(add one per member after re-running the checks)_

---

# Phase 1 — Dataset audit: AI usage record

## What the AI did

Wrote `tools/inspect_dataset.py` (read-only auditor) and
`tests/test_inspect_dataset.py` (23 tests on synthetic fixtures), ran the audit
against `data/raw/roboflow_safety_glasses_original.zip`, and wrote
`docs/dataset_audit.md` plus the 13 records in `data/reviewed/audit/`.

## Claims the AI made and how each was checked

| Claim | How it was verified |
|---|---|
| Archive is unmodified | SHA-256 recomputed after the run and compared to the pre-run value; also asserted by a test that checks bytes and mtime |
| 400 images / 400 labels / 770 instances | Cross-checked against an independent extraction of the ZIP; the first run reported 387/755 and that discrepancy exposed a real pairing bug (see below) |
| 2 malformed rows | Independently re-read the two named label files; both really contain 9 fields |
| 59 leakage groups | Broken down by kind and the five source stems (`SG6`, `SG8`, `SG11`, `SG2`, `SG14`) were listed individually and matched against the earlier ad hoc audit |
| Outputs are deterministic | Ran the tool twice and diffed all 13 records; only the timestamps differed |
| 131 tests pass | Ran `pytest -q`; result captured verbatim |
| Phase 0 still works | Re-ran `env_check.py` and `camera_check.py` after the Phase 1 changes |

## Two errors the AI made and corrected

Both were caught by executing code, not by re-reading it. Recorded here because
the corrected behaviour is the load-bearing part, not the original mistake.

1. **`zipfile.ZipFile(path, "rb")`.** The auditor crashed on the very first run
   with `ValueError: ZipFile requires mode 'r', 'w', 'x', or 'a'`. Fixed to
   `"r"`. The exception handler was widened to cover `ValueError` so a bad
   archive surfaces as a readable error rather than a traceback.

2. **Pairing images to labels on a normalised stem.** The first successful run
   reported 387 label files and 755 instances against 400 images. Investigation
   showed two images sharing a normalised stem (`SG10_jpg.rf.<hashA>` and
   `SG10_jpg.rf.<hashB>`) were collapsed into one pairing key, so one of each
   pair looked unlabelled and its instances were dropped. Pairing now uses the
   exact stem; normalisation is retained only for leakage grouping. Corrected
   counts: 400 labels, 770 instances.

A third, smaller error: three of the first tests asserted a `run_audit` keyword
(`contact_sheets`) that does not exist — the real parameter is
`make_contact_sheets`, and the return value is a wrapper dict whose `summary`
key holds the findings. The tests were corrected to match the real signature.
That mismatch also exposed a genuine gap: `run_audit` hashed the archive before
checking it existed, so a missing path leaked a raw `FileNotFoundError`. An
explicit existence check was added.

## What the AI did NOT do

- Did not train, fine-tune, or evaluate any model.
- Did not create `models/best.pt`.
- Did not rename `glasses` to `goggles`, remap any class, or infer `no_goggles`
  from the absence of a box.
- Did not modify, move, rename, or re-zip the source archive.
- Did not rewrite splits or delete any duplicate.
- Did not commit extracted images, the archive, contact sheets, or weights.
- Did not claim any accuracy, mAP, precision, recall, or latency figure. The
  audit produces no performance metric, because no model was run.

## Residual uncertainty

The near-duplicate threshold (Hamming <= 10 on a 64-bit dHash) is a judgement
call, not a calibrated value. It is exposed as `--near-dup-bits` so a human can
re-run with a different bound. The 377 figure should be read as "many frames are
near-identical, consistent with video sampling", not as a defect count.
