# Test plan

## Phase 0 acceptance checks

These are the checks the Phase 0 gate is judged against. Run them in order.

```bash
.venv/bin/python tools/env_check.py     # must print "Environment check OK"
.venv/bin/python -m pytest -q           # config contract tests must pass
.venv/bin/python tools/camera_check.py  # manual: prints "Camera check PASSED."
```

| ID | Check | Method | Pass condition |
|---|---|---|---|
| ENV-01 | Clean-clone bootstrap | Fresh venv + `pip install -r requirements.txt` | Install succeeds, no build errors |
| ENV-02 | Environment check | `tools/env_check.py` | Exit code 0, 0 failures |
| ENV-03 | Camera tool runs | `tools/camera_check.py` | Exit code 0, reports FPS |
| CFG-01 | Valid config loads | `pytest tests/test_config.py` | All pass |
| CFG-02 | Invalid value rejected | `pytest tests/test_config.py -k bounds` | Raises `ConfigError` naming the key |
| SEC-01 | No secrets committed | `git ls-files` review + `.gitignore` | No `.env`, `config.h`, `app.yaml`, or keys |

`ENV-03` needs a physical camera and therefore cannot run headless; the automated
suite skips it and the result is recorded manually in the phase report.

## Future test matrix (from plan.md §12)

Not yet executed. Listed so later phases report against fixed IDs.

| Group | IDs |
|---|---|
| AI | AI-01 … AI-05 |
| System | SYS-01 … SYS-08 |
| Communication | COM-01 … COM-03 |
| IoT | IOT-01, IOT-02 |
| Robustness | ROB-01, ROB-02 |

## Automated test layout

| File | Phase introduced | Covers |
|---|---|---|
| `tests/test_config.py` | **0** | Config bounds, missing keys, class-map contract |
| `tests/test_temporal_filter.py` | 3 | Threshold boundaries, dropped frames, clearance |
| `tests/test_incident_manager.py` | 3 | One incident per episode, reset eligibility |
| `tests/test_serial_protocol.py` | 4 | Round trips, malformed JSON, duplicates, version mismatch |
| `tests/test_recorded_video.py` | 3 | Deterministic end-to-end behaviour on recorded clips |

## Reporting rule

Every number reported for this project must be traceable to a raw record in
`experiments/` or a test run captured in this document. Results are never rounded
up, and failed cases are retained alongside successful ones.
