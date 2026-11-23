#!/usr/bin/env python
"""Manual end-to-end check of the Blynk bridge against the real cloud.

Run this once after filling ``.env`` so you can *see* something happen in the
Blynk app before trusting it during a demo::

    .venv/bin/python test_blynk.py            # full sequence
    .venv/bin/python test_blynk.py --dry-run  # no network, prints payloads

The sequence is Task 4's, made a little stricter so it also proves the
anti-spam rule instead of only hoping for it:

1. Push ``SAFE``              -> V0 text, V2 lamp, V8 counter
2. Enter ``ALARM_LATCHED``    -> V3 + V4, and the ``eye_violation`` event once
3. Re-push the same alarm     -> the event must be SKIPPED, not sent again
4. Acknowledge (red only)     -> V4 buzzer off, V0 shows ACKNOWLEDGED
5. Reset back to SAFE         -> all quiet, incident count retained

Every step prints what it sent and what came back. A non-zero exit code means
at least one push genuinely failed; a SKIPPED duplicate is success.

``--dry-run`` is the safe default for a classroom or a shared network: it uses
the same mapping and the same anti-spam logic, but talks to an in-process fake
session instead of blynk.cloud.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from src.blynk_client import (
    BlynkClient,
    BlynkConfigError,
    DeliveryStatus,
    LifecycleReading,
    PushResult,
)

STEP = "  {mark} {label:<34} {detail}"

OK = "PASS"


class _OfflineSession:
    """Minimal stand-in for ``requests.Session`` that never opens a socket.

    It records exactly what would have been sent so ``--dry-run`` can show the
    real query string, which is the part most likely to be wrong (pin casing,
    value type, event code).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float = 0):
        self.calls.append((url, dict(params or {})))

        return _OfflineResponse()

    def close(self) -> None:
        pass


class _OfflineResponse:
    status_code = 200
    text = ""

    def json(self):  # noqa: D102 - trivial stub
        return {}


def _report(label: str, results: dict[str, PushResult]) -> bool:
    """Print one step's outcome. Returns True when nothing actually failed."""

    sent = [f"{pin}={res.status.value}" for pin, res in sorted(results.items())]
    failed = [res for res in results.values() if res.status is DeliveryStatus.ERROR]
    mark = "x" if failed else "v"
    print(STEP.format(mark=mark, label=label, detail=", ".join(sent)))

    for res in failed:
        print(f"      ! {res.pin or 'event'}: {res.detail} {res.message}".rstrip())

    return not failed


def run(fake: bool) -> int:
    session = _OfflineSession() if fake else None

    try:
        client = BlynkClient.from_env(require=not fake, session=session)
    except BlynkConfigError as exc:
        print(f"x  {exc}", file=sys.stderr)

        return 2

    if fake and not client.enabled and session is not None:
        # --dry-run with no token: force-enable so the sequence still runs and
        # the payloads are visible. Network is a fake, so this is harmless.
        client = BlynkClient(
            "dry-run-placeholder-token",
            session=session,
            use_thread=False,
            min_request_interval=0.0,
            alert_cooldown_seconds=0.0,
        )

    print(f"\nBlynk bridge {BlynkClient.VERSION} - "
          f"{'DRY RUN (no network)' if fake else 'LIVE'}")
    print(f"  device token : {'set' if client.enabled else 'MISSING'}")
    print(f"  threading    : {'off (deterministic)' if fake else 'on'}\n")

    failures = 0

    # -- 1. SAFE ---------------------------------------------------------
    safe = LifecycleReading(
        state="SAFE",
        incident_id=None,
        violation_ratio=0.0,
        link_online=True,
        incident_count=0,
        top_confidence=0.91,
        fps=15.5,
    )
    failures += not _report("1. push SAFE", client.push_status(safe))

    # -- 2. violation -> ALARM_LATCHED -----------------------------------
    alarm = LifecycleReading(
        state="ALARM_LATCHED",
        incident_id=1,
        violation_ratio=0.94,
        link_online=True,
        incident_count=1,
        top_confidence=0.88,
        fps=14.9,
    )
    first = client.push_status(alarm)
    failures += not _report("2. push ALARM_LATCHED #1", first)

    event = first.get("event")
    if event is None:
        print("      ! no event result returned")
        failures += 1
    elif event.sent:
        print(f"      v eye_violation event delivered ({event.detail})")
    else:
        print(f"      ! eye_violation event NOT delivered: {event.detail} "
              f"{event.message}".rstrip())
        failures += event.status is DeliveryStatus.ERROR

    # -- 3. anti-spam: same incident again -------------------------------
    repeat = client.push_status(alarm)
    repeat_event = repeat.get("event")
    if repeat_event is not None and repeat_event.status is DeliveryStatus.SKIPPED:
        print(STEP.format(mark="v", label="3. repeat alarm, no new event",
                          detail=repeat_event.detail))
    else:
        print(STEP.format(mark="x", label="3. repeat alarm, no new event",
                          detail="event was NOT suppressed"))
        failures += 1

    # -- 4. acknowledge: red held, buzzer off ----------------------------
    acked = LifecycleReading(
        state="ACKNOWLEDGED",
        incident_id=1,
        violation_ratio=0.0,
        link_online=True,
        incident_count=1,
        top_confidence=0.0,
        fps=15.1,
    )
    failures += not _report("4. acknowledge (buzzer off)", client.push_status(acked))

    # -- 5. reset --------------------------------------------------------
    reset = LifecycleReading(
        state="SAFE",
        incident_id=None,
        violation_ratio=0.0,
        link_online=True,
        incident_count=1,
        top_confidence=0.0,
        fps=15.4,
    )
    failures += not _report("5. reset to SAFE", client.push_status(reset))

    client.flush()
    client.close()

    if fake and session is not None:
        print("\nRequests that would have been sent:")
        for url, params in session.calls:
            shown = {k: v for k, v in params.items() if k != "token"}
            print(f"  GET {url.split('/external/api/')[-1]}  {shown}")

    print(f"\nstats: {client.stats}")
    print(f"\n{'ALL STEPS PASSED' if not failures else f'{failures} STEP(S) FAILED'}")

    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="use an in-process fake session; no network, no credentials needed",
    )
    args = parser.parse_args(argv)

    return run(fake=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
