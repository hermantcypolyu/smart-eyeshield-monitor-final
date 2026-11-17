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
