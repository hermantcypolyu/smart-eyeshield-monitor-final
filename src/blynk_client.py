"""Laptop-side Blynk IoT bridge: monitoring and notification only.

The eye-shield system is a *safety* device. Everything that matters -- the
latched alarm, the LEDs, the buzzer, the OLED, the incident lifecycle -- runs
locally in ``tools/incident_lifecycle.py`` and on the ESP32-S3. The cloud is a
noticeboard, not a control panel.

That single sentence drives every design decision in this file:

* **Read-only.** There are no ``write``/``read`` callbacks for Blynk buttons or
  switches anywhere in this module. A phone in a pocket, on a train, with no
  signal, must never be the reason a buzzer stops. Remote control is not
  "not implemented yet" -- it is deliberately absent. Never add it.

* **Advisory.** Every public method is total: it returns a result object and
  never raises. A dead network, a bad token, a rate-limited API, or a malformed
  answer all degrade to "the push did not happen" and a log line. The caller
  (YOLO loop, serial writer) cannot be interrupted by the cloud.

* **Non-blocking in practice.** Pushes run on a background daemon thread, so
  even a slow DNS lookup or a stalled TCP handshake cannot add latency to a
  frame. ``flush()`` and ``close()`` are the only blocking calls and exist for
  tests and shutdown.

* **Spam-resistant.** ``update_datastream`` sends only when a value actually
  changed, and ``trigger_alert`` fires at most once per incident id, with a
  local cooldown. Blynk's own limit is 500,000 updates per device per day;
  a 30 fps loop pushing eleven pins every frame would burn that in five hours.

Transport is the Blynk Device HTTPS API, verified against
https://docs.blynk.io/en/blynk.cloud/device-https-api on 2026-11-23::

    GET /external/api/update?token=<t>&v0=<value>      one datastream
    GET /external/api/batch/update?token=<t>&v0=a&v1=b many datastreams
    GET /external/api/logEvent?token=<t>&code=<c>&description=<d>

A ``200`` with an empty body means success. A ``400`` carries
``{"error": {"message": "..."}}`` and is surfaced as the result's ``message``,
because "Invalid token" and "No datastream setup for that pin" look identical
from the outside otherwise.

Nothing here imports ultralytics, torch, cv2, or PySide6. It can therefore be
unit-tested, and imported by a test script, on a laptop with no camera.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, NamedTuple

import requests

logger = logging.getLogger(__name__)

#: The eleven datastreams this bridge owns. Kept as a frozen tuple because the
#: set is a contract with the Blynk template: an unexpected ``V`` number in the
#: mapping is a bug, not a feature, and should be caught by a test rather than
#: silently creating a datastream nobody has configured.
DATASTREAM_PINS: tuple[str, ...] = (
    "V0",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6",
    "V7",
    "V8",
    "V9",
    "V10",
)

#: Blynk event code for a confirmed eye-protection violation. Must match the
#: event created in the Blynk template (Blynk.Console -> Templates -> Events),
#: otherwise ``logEvent`` answers 400 "Can't find Event with this code".
EYE_VIOLATION_EVENT = "eye_violation"

class BlynkConfigError(RuntimeError):
    """Raised only by :meth:`BlynkClient.from_env` when no token is configured.

    Deliberately *not* raised from ``__init__`` and never raised from a push
    method: a missing credential must be a startup decision the operator makes,
    while a network failure at runtime must never be.
    """


class DeliveryStatus(str, Enum):
    """Outcome of one push. ``string``-valued so logs read plainly."""

    OK = "ok"
    SKIPPED = "skipped"          # unchanged value / duplicate alert / disabled
    BACKING_OFF = "backing_off"  # circuit open after repeated failures
    ERROR = "error"              # network, HTTP, or rejection by Blynk


@dataclass(frozen=True)
class PushResult:
    """What happened to one request.

    Returned rather than raised so a caller can log a problem without being
    forced to write ``try:`` around every call in the detection loop. The
    ``status is DeliveryStatus.OK`` test is the only thing a caller should
    branch on; everything else is for diagnostics.
    """

    status: DeliveryStatus
    pin: str | None = None
    detail: str = ""
    http_status: int | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is DeliveryStatus.OK

    @property
    def sent(self) -> bool:
        """True when the value should be considered delivered.

        ``SKIPPED`` counts as sent: an unchanged value that is already in the
        cloud is not a failure, and a caller must not retry it.
        """
        return self.status in (DeliveryStatus.OK, DeliveryStatus.SKIPPED)

    def __str__(self) -> str:
        bits = [self.status.value]
        if self.pin:
            bits.append(self.pin)
        if self.http_status is not None:
            bits.append(f"http={self.http_status}")
        text = " ".join(bits)
        if self.detail:
            text += f" ({self.detail})"
        if self.message:
            text += f" [{self.message}]"
        return text


@dataclass(frozen=True)
class LifecycleReading:
    """One immutable snapshot of the laptop's safety state.

    The bridge consumes *this*, never an ``IncidentLifecycle`` directly. The
    separation is the whole point of the module: Blynk integration must be
    incapable of reaching into the state machine, so it is given a frozen
    value and nothing else.

    All fields are the plain data the demo already has each frame. ``None``
    means "not known yet" and is mapped to a neutral cloud value rather than a
    misleading zero.
    """

    state: str
    incident_id: int | None = None
    violation_ratio: float | None = None
    last_alert_time: str | None = None
    link_online: bool = True
    incident_count: int = 0
    top_confidence: float | None = None
    fps: float | None = None
    green: int | None = None
    red: int | None = None
    buzzer: int | None = None

    def __post_init__(self) -> None:
        # Normalised once, at construction, so every consumer sees the same
        # uppercase token and the mapping table never has to guess case.
        object.__setattr__(self, "state", str(self.state).strip().upper())
        for name in ("violation_ratio", "top_confidence"):
            raw = getattr(self, name)
            if raw is not None:
                # Clamp rather than reject: a 1.0000001 from float noise must
                # not turn into a rejected push mid-demo.
                object.__setattr__(self, name, min(1.0, max(0.0, float(raw))))


@dataclass(frozen=True)
class DatastreamValues:
    """The eleven ``V`` values derived from one :class:`LifecycleReading`."""

    values: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)

    def __getitem__(self, pin: str) -> Any:
        return self.values[pin]


# ---------------------------------------------------------------------------
# Task 2: state -> datastream mapping
# ---------------------------------------------------------------------------

#: Outputs are *pin* semantics, not "what the state machine wishes". During a
#: manual self-test the two deliberately disagree, and a cloud dashboard that
#: reports a green lamp while the physical LED is dark is worse than no
#: dashboard. So a reading may carry explicit ``green``/``red``/``buzzer``
#: flags; when it does they win, and the table below is only the fallback.
_STATE_OUTPUTS: Mapping[str, tuple[int, int, int]] = {
    "SAFE": (1, 0, 0),
    "SAFE_RECOVERED": (1, 0, 0),
    "WAITING_FOR_TARGET": (0, 0, 0),
    "STARTUP": (0, 0, 0),
    "VIOLATION_PENDING": (1, 0, 0),   # pending has not earned the alarm yet
    "ALARM_LATCHED": (0, 1, 1),
    "SAFE_CLEAR_PENDING": (0, 1, 0),  # red held, buzzer already acknowledged
    "ACKNOWLEDGED": (0, 1, 0),
    "FAULT": (0, 0, 0),               # parked; we do not trust our outputs
}


def map_reading_to_datastreams(
    reading: LifecycleReading,
    *,
    last_alert_time: str | None = None,
) -> DatastreamValues:
    """Map one lifecycle reading onto V0-V10.

    The pin semantics are fixed by the Blynk template; see module docstring and
    ``docs/blynk_setup.md``. Two choices are worth spelling out because they
    are the ones a reader would get wrong:

    * ``V2``/``V3``/``V4`` are **mirrors of the physical GPIO** -- the green
      LED, red LED and buzzer as the operator would see them on the device. A
      cloud dashboard for a safety system must never show "danger over" while
      the red LED is still latched on, so ``ACKNOWLEDGED`` and
      ``SAFE_CLEAR_PENDING`` report red=1 exactly as the hardware holds it.
      When the reading carries explicit ``green``/``red``/``buzzer`` fields
      they win; otherwise the state table below supplies the physical default.
    * A missing number is sent as ``0`` rather than omitted, because a Blynk
      datastream that stops receiving values shows its last value forever --
      a frozen "12.5 fps" from ten minutes ago is stale data pretending to be
      live.

    Unknown states are passed through as their uppercase token and treated as
    "nothing is proven safe": all lamps 0. If a new state is added to the
    lifecycle and forgotten here, the dashboard shows it honestly instead of
    showing SAFE, which is the failure mode that matters.
    """

    state = reading.state
    default_green, default_red, default_buzzer = _STATE_OUTPUTS.get(
        state, (0, 0, 0)
    )

    green = default_green if reading.green is None else int(bool(reading.green))
    red = default_red if reading.red is None else int(bool(reading.red))
    buzzer = (
        default_buzzer if reading.buzzer is None else int(bool(reading.buzzer))
    )

    return DatastreamValues(
        {
            "V0": state,
            "V1": int(reading.incident_id or 0),
            # Option B: physical pin mirrors, not state indicators.
            "V2": green,
            "V3": red,
            "V4": buzzer,
            "V5": round(float(reading.violation_ratio or 0.0), 3),
            "V6": last_alert_time or reading.last_alert_time or "",
            "V7": "ONLINE" if reading.link_online else "OFFLINE",
            "V8": int(reading.incident_count or 0),
            "V9": round(float(reading.top_confidence or 0.0), 3),
            "V10": round(float(reading.fps or 0.0), 1),
        }
    )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _parse_blynk_error(response: requests.Response) -> str:
    """Extract Blynk's human message from a 4xx body, or fall back to the text.

    Blynk answers errors as ``{"error": {"message": "Invalid token."}}``. Parsing
    that is worth the six lines because the difference between "Invalid token"
    and "No datastream setup for that pin" is the difference between checking
    ``.env`` and checking the Blynk console.
    """

    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()[:200]

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message.strip()
        if isinstance(error, str):
            return error.strip()

    return (response.text or "").strip()[:200]


class _Queued(NamedTuple):
    """One pending request, with the pins it covers.

    The pin list has to travel with the request: a batched push updates
    ``_last_values`` for every pin at once, so if the request later fails the
    worker needs to know which entries to forget before the next frame.
    """

    path: str
    params: dict[str, Any]
    pins: tuple[str, ...]


class _RateLimiter:
    """Minimum-interval gate. Cheap, monotonic, thread-safe.

    Blynk documents 50 requests per second per device for hardware limits and
    500,000 ``update`` calls per day. Neither is a problem for a well-behaved
    bridge, but a bug that pushes every frame would be. This limiter makes the
    floor explicit rather than relying on the caller to remember.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._last = 0.0
        self._lock = threading.Lock()

    def delay_until_allowed(self, now: float | None = None) -> float:
        """Seconds the caller would have to wait for a slot.

        ``0.0`` means the caller may send immediately. Computing the delay
        separately from taking the slot lets the worker thread sleep without
        holding the lock, and lets the synchronous path decline to wait at all.
        """

        moment = time.monotonic() if now is None else now
        with self._lock:
            elapsed = moment - self._last

            return max(0.0, self._min_interval - elapsed)

    def take(self, now: float | None = None) -> bool:
        """Claim the next slot if one is free right now."""

        moment = time.monotonic() if now is None else now
        with self._lock:
            if moment - self._last < self._min_interval:
                return False
            self._last = moment

            return True

    def allow(self, now: float | None = None) -> bool:
        return self.take(now)


class _CircuitBreaker:
    """Stops hammering a cloud that is already failing.

    Without this, a laptop on hotel Wi-Fi spends every request waiting for a
    multi-second timeout. The breaker trips after ``threshold`` consecutive
    failures and stays open for a growing cooldown, during which pushes return
    :attr:`DeliveryStatus.BACKING_OFF` immediately. The local alarm never
    notices either way -- this exists to protect the *loop's* latency and the
    operator's log.
    """

    def __init__(
        self,
        threshold: int = 3,
        cooldown_seconds: float = 30.0,
        max_cooldown_seconds: float = 300.0,
    ) -> None:
        self._threshold = max(1, int(threshold))
        self._base_cooldown = max(0.0, float(cooldown_seconds))
        self._max_cooldown = max(self._base_cooldown, float(max_cooldown_seconds))
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def is_open(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            return moment < self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                # Back off geometrically so a long outage costs one probe per
                # five minutes rather than one per thirty seconds.
                doublings = self._failures - self._threshold
                cooldown = min(
                    self._max_cooldown, self._base_cooldown * (2 ** doublings)
                )
                self._open_until = moment + cooldown
                logger.warning(
                    "blynk: %d consecutive failures, pausing pushes for %.0f s",
                    self._failures,
                    cooldown,
                )


@dataclass
class BlynkStats:
    """Counters for the status overlay and for the demo write-up.

    Mutable on purpose: it is only ever touched by the client's own thread(s)
    and read by the caller for display.
    """

    requests: int = 0
    successes: int = 0
    failures: int = 0
    skipped: int = 0
    events: int = 0
    last_error: str = ""
    last_success_at: str = ""


class BlynkClient:
    """Push-only bridge to the Blynk IoT cloud.

    Usage in the demo loop::

        client = BlynkClient.from_env()            # once, at startup
        ...
        client.push_status(reading)                # every frame; cheap
        ...
        client.close()                             # on exit

    ``push_status`` is safe to call at 30 fps: it diffs against the last
    values sent, then hands the changed pins to a single background worker.
    It never touches the socket on the caller's thread.
    """

    #: Bump when the wire format changes, so a log shows which version ran.
    VERSION = "1.0"

    def __init__(
        self,
        auth_token: str,
        *,
        server: str = "blynk.cloud",
        enabled: bool = True,
        timeout: float = 4.0,
        min_request_interval: float = 0.0,
        alert_cooldown_seconds: float = 0.0,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown: float = 30.0,
        max_queue: int = 64,
        use_thread: bool = True,
        session: requests.Session | None = None,
        clock: Any | None = None,
        sleep: Any | None = None,
    ) -> None:
        self._token = (auth_token or "").strip()
        self._server = (server or "blynk.cloud").strip().rstrip("/")
        if self._server and not self._server.startswith(("http://", "https://")):
            self._server = f"https://{self._server}"
        self._enabled = bool(enabled) and bool(self._token)
        self._timeout = max(0.5, float(timeout))
        self._use_thread = bool(use_thread)
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep

        self._session = session if session is not None else requests.Session()
        # requests' own default is fine, but stating it keeps a caller's global
        # monkeypatch from silently changing retry behaviour mid-demo.
        self._session.headers.setdefault(
            "User-Agent", f"eye-shield-blynk-bridge/{self.VERSION}"
        )

        self._limiter = _RateLimiter(min_request_interval)
        self._breaker = _CircuitBreaker(
            threshold=circuit_breaker_threshold,
            cooldown_seconds=circuit_breaker_cooldown,
        )
        self._alert_cooldown = max(0.0, float(alert_cooldown_seconds))

        self._last_values: dict[str, Any] = {}
        self._alerts_fired: dict[int, float] = {}
        self._last_alert_wall: float = 0.0
        self._lock = threading.Lock()

        self.stats = BlynkStats()

        self._queue: deque[_Queued] = deque(
            maxlen=max(1, int(max_queue))
        )
        self._queue_lock = threading.Lock()
        self._wakeup = threading.Event()
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None

        if self._enabled and self._use_thread:
            self._worker = threading.Thread(
                target=self._run,
                name="blynk-bridge",
                daemon=True,
            )
            self._worker.start()

        if not enabled:
            logger.info("blynk: disabled by configuration; pushes are no-ops")
        elif not self._token:
            logger.warning(
                "blynk: no auth token configured; running without cloud "
                "monitoring (local alarm unaffected)"
            )

    # -- construction ----------------------------------------------------

    @classmethod
    def from_env(cls, *, require: bool = True, **kwargs: Any) -> "BlynkClient":
        """Build a client from ``BLYNK_AUTH_TOKEN`` / ``BLYNK_SERVER``.

        A ``.env`` in the repository root is read if python-dotenv is
        installed, but the environment always wins: a value exported in the
        shell must not be overwritten by a stale file. No credential ever
        appears in source, and ``.env`` is git-ignored.

        ``require=True`` raises :class:`BlynkConfigError` when no token is
        present, so a demo fails at startup with a readable message instead of
        silently monitoring nothing. Set ``BLYNK_ENABLED=0`` to opt out
        deliberately.
        """

        try:
            from dotenv import load_dotenv

            load_dotenv(override=False)
        except ImportError:  # pragma: no cover - dotenv is a pinned dependency
            logger.debug("blynk: python-dotenv not installed; using os.environ")

        token = os.environ.get("BLYNK_AUTH_TOKEN", "").strip()
        server = os.environ.get("BLYNK_SERVER", "").strip() or "blynk.cloud"
        enabled_env = os.environ.get("BLYNK_ENABLED", "1").strip().lower()
        enabled = enabled_env not in {"0", "false", "no", "off"}

        if not token and require and enabled:
            raise BlynkConfigError(
                "BLYNK_AUTH_TOKEN is not set. Copy .env.example to .env and "
                "paste the device auth token from Blynk.Console (Device info), "
                "or set BLYNK_ENABLED=0 to run without cloud monitoring."
            )

        return cls(token, server=server, enabled=enabled, **kwargs)

    # -- properties ------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when a token exists and pushes are actually attempted."""

        return self._enabled

    # -- Task 1: single datastream push ----------------------------------

    def update_datastream(
        self, v_pin: str, value: Any, *, force: bool = False
    ) -> PushResult:
        """Push one value to V0-V10.

        Never raises. Returns a :class:`PushResult` the caller may log.

        ``force=True`` bypasses the "unchanged since last push" filter, for the
        one case that needs it: re-publishing current values after a Blynk
        dashboard has been cleared or a token rotated.
        """

        # _push_many reports per pin because a batch can fail for one pin and
        # succeed for another. A single-pin call has exactly one entry, so lift
        # it out rather than making every caller index a one-element dict.
        results = self._push_many({v_pin: value}, force=force)
        if not results:
            return PushResult(
                DeliveryStatus.ERROR, pin=v_pin.strip().upper(), detail="no result"
            )

        return results[next(iter(results))]

    # -- Task 1: alert event ---------------------------------------------

    def trigger_alert(
        self,
        incident_id: int,
        description: str,
        *,
        code: str = EYE_VIOLATION_EVENT,
        force: bool = False,
    ) -> PushResult:
        """Fire the ``eye_violation`` event for one incident.

        **At most once per incident id.** This is Task 3's anti-spam rule and
        it is enforced here, at the edge, so no caller can flood the API by
        looping over frames -- the guard belongs where the request is made, not
        where it is remembered. A cooldown covers the pathological case where
        the lifecycle allocates a fresh id every few seconds.

        Returns ``SKIPPED`` when suppressed; that is a normal outcome, not an
        error, and callers should not log it as a failure.
        """

        if not self._enabled:
            return PushResult(DeliveryStatus.SKIPPED, detail="disabled")

        incident = int(incident_id)
        if incident <= 0:
            return PushResult(
                DeliveryStatus.SKIPPED, detail="no incident id to alert on"
            )

        now_wall = float(self._clock())
        with self._lock:
            if not force and incident in self._alerts_fired:
                reason = f"already alerted for incident {incident}"
                print(f"DEBUG BLYNK LOCAL: Skipping ID {incident}. Reason: {reason}")
                return PushResult(DeliveryStatus.SKIPPED, detail=reason)
            if not force and now_wall - self._last_alert_wall < self._alert_cooldown:
                remaining = self._alert_cooldown - (now_wall - self._last_alert_wall)
                reason = f"alert cooldown, {remaining:.0f} s remaining"
                print(f"DEBUG BLYNK LOCAL: Skipping ID {incident}. Reason: {reason}")
                return PushResult(DeliveryStatus.SKIPPED, detail=reason)

        text = (description or "").strip() or f"Incident {incident}"
        # Event descriptions are shown in the Blynk app and in push
        # notifications; keeping them one line and under ~120 chars keeps the
        # notification readable on a lock screen.
        text = " ".join(text.split())[:120]

        params = {
            "code": code,
            "description": f"#{incident} {text}"[:160],
        }
        result = self._request("logEvent", params, pin=None)
        if result.ok:
            # Only remember a successful delivery. Recording the id *before*
            # the request was the bug that silently blacklisted an incident
            # even when the first attempt was skipped or failed, so the real
            # notification never reached the phone.
            with self._lock:
                self._alerts_fired[incident] = now_wall
                self._last_alert_wall = now_wall
                # Bounded: a six-hour demo can open a lot of incidents.
                if len(self._alerts_fired) > 512:
                    oldest = sorted(self._alerts_fired.items(), key=lambda kv: kv[1])
                    for stale_id, _ in oldest[:256]:
                        self._alerts_fired.pop(stale_id, None)
            self.stats.events += 1
            logger.info("blynk: %s event sent for incident %s", code, incident)
        else:
            print(
                f"DEBUG BLYNK LOCAL: alert for ID {incident} NOT delivered "
                f"(status={result.status.value}, http={result.http_status}, "
                f"msg={result.message!r}); id NOT blacklisted, will retry"
            )

        return PushResult(
            result.status,
            detail=f"incident {incident}",
            http_status=result.http_status,
            message=result.message,
        )

    def clear_alerts(self) -> None:
        """Forget every incident id that has already been alerted on.

        Called when the lifecycle returns to ``SAFE`` so the next violation
        (even one that reuses a recently-seen incident id) is guaranteed to
        fire a fresh HTTP request and a fresh push notification. Without this,
        a reused id would be suppressed locally by the one-per-incident rule.
        """

        with self._lock:
            self._alerts_fired.clear()
            self._last_alert_wall = 0.0

    # -- Task 2/4: whole-snapshot push -----------------------------------

    def push_status(
        self,
        reading: LifecycleReading,
        *,
        last_alert_time: str | None = None,
        force: bool = False,
    ) -> dict[str, PushResult]:
        """Map a reading to V0-V10 and push whatever changed.

        This is the method the detection loop calls every frame. It costs one
        dict comparison when nothing changed and a single batched request when
        something did, so eleven pins do not consume eleven request slots.

        Also enforces the transition rule: entering ``ALARM_LATCHED`` (or a
        related incident state) fires the event exactly once per incident id,
        no matter how many frames see that state.
        """

        values = map_reading_to_datastreams(
            reading, last_alert_time=last_alert_time
        ).as_dict()

        results = self._push_many(values, force=force)

        # The event is deliberately keyed off the incident id rather than off
        # the transition, because the lifecycle may pass through
        # VIOLATION_PENDING -> ALARM_LATCHED while the serial link lags. Keying
        # on the id means one incident, one notification, regardless of how the
        # state machine got there.
        if reading.state == "ALARM_LATCHED" and reading.incident_id:
            alert = self.trigger_alert(
                int(reading.incident_id),
                self._alert_description(reading),
            )
            results["event"] = alert

        return results

    # -- lifecycle -------------------------------------------------------

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for the queue to drain. For tests and clean shutdown.

        Returns ``True`` if the queue emptied within the timeout.
        """

        if self._worker is None:
            self._drain_pending()

            return True

        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._queue_lock:
                empty = not self._queue
            if empty:
                return True
            time.sleep(0.01)

        with self._queue_lock:
            return not self._queue

    def close(self, timeout: float = 2.0) -> None:
        """Stop the worker and release the HTTP session. Idempotent.

        Pending updates are given ``timeout`` to reach the cloud *before* the
        worker stops, so a clean shutdown leaves the dashboard showing the
        final state rather than whatever happened to be in flight. Anything
        still queued after that is dropped: at shutdown the local state is the
        one that matters, and a half-dead network is not worth blocking on.
        """

        if self._worker is not None and self._worker.is_alive():
            if not self.flush(timeout=timeout):
                logger.debug("blynk: shutting down with updates still queued")

        self._shutdown.set()
        self._wakeup.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=max(0.0, timeout))
            if self._worker.is_alive():  # pragma: no cover - stuck socket
                logger.warning("blynk: worker did not stop within %.1fs", timeout)
            self._worker = None
        try:
            self._session.close()
        except Exception:  # pragma: no cover - closing must never raise
            logger.debug("blynk: session close raised", exc_info=True)

    def __enter__(self) -> "BlynkClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- internals -------------------------------------------------------

    @staticmethod
    def _alert_description(reading: LifecycleReading) -> str:
        bits = ["Eye protection violation confirmed"]
        if reading.violation_ratio is not None:
            bits.append(f"ratio {reading.violation_ratio:.0%}")
        if reading.top_confidence is not None:
            bits.append(f"conf {reading.top_confidence:.2f}")

        return " | ".join(bits)

    def _push_many(
        self, values: Mapping[str, Any], *, force: bool = False
    ) -> dict[str, PushResult]:
        """Diff, validate, then enqueue or send.

        Returns one result per requested pin. Pins filtered out by the
        unchanged value check report ``SKIPPED``, which is truthful: the cloud
        already holds that value.
        """

        results: dict[str, PushResult] = {}
        changed: dict[str, Any] = {}

        with self._lock:
            for raw_pin, value in values.items():
                pin = str(raw_pin).strip().upper()
                normalised, problem = _normalise_datastream_value(pin, value)
                if problem:
                    results[pin] = PushResult(
                        DeliveryStatus.ERROR, pin=pin, detail=problem
                    )
                    continue
                if not force and self._last_values.get(pin) == normalised:
                    self.stats.skipped += 1
                    results[pin] = PushResult(
                        DeliveryStatus.SKIPPED, pin=pin, detail="unchanged"
                    )
                    continue
                # Recorded optimistically so a burst of identical frames does
                # not enqueue the same update repeatedly while the first one
                # is still in flight.
                self._last_values[pin] = normalised
                changed[pin] = normalised

        if not changed:
            return results

        if not self._enabled:
            for pin in changed:
                results[pin] = PushResult(
                    DeliveryStatus.SKIPPED, pin=pin, detail="disabled"
                )

            return results

        payload = {"token": self._token, **changed}
        if self._worker is None:
            # Synchronous mode (tests, or use_thread=False). One batched
            # request covers every changed pin, so a single outcome is
            # reported once per pin rather than pretending to per-pin detail.
            outcome = self._dispatch("batch/update", payload, pins=list(changed))
            for pin in changed:
                results[pin] = PushResult(
                    outcome.status,
                    pin=pin,
                    detail=outcome.detail,
                    http_status=outcome.http_status,
                    message=outcome.message,
                )
        else:
            self._enqueue("batch/update", payload, pins=list(changed))
            for pin in changed:
                results[pin] = PushResult(
                    DeliveryStatus.OK, pin=pin, detail="queued"
                )

        return results

    def _enqueue(
        self, path: str, params: dict[str, Any], pins: list[str] | None = None
    ) -> None:
        with self._queue_lock:
            if len(self._queue) == self._queue.maxlen:
                # Drop the oldest: for a live telemetry stream the newest state
                # is the one that matters, and a backlog of stale frames would
                # arrive out of order.
                dropped = self._queue.popleft()
                logger.debug("blynk: queue full, dropped %s", dropped[0])
            self._queue.append(_Queued(path, params, tuple(pins or ())))
        self._wakeup.set()

    def _run(self) -> None:
        while not self._shutdown.is_set():
            self._wakeup.wait(timeout=0.5)
            self._wakeup.clear()
            self._drain_pending()

    def _drain_pending(self) -> None:
        while not self._shutdown.is_set():
            with self._queue_lock:
                if not self._queue:
                    return
                item = self._queue.popleft()
            try:
                self._dispatch(
                    item.path, item.params, pins=list(item.pins), wait_for_slot=True
                )
            except Exception:  # pragma: no cover - defensive: worker must live
                logger.warning("blynk: worker caught an unexpected error",
                               exc_info=True)

    def _dispatch(
        self,
        path: str,
        params: dict[str, Any],
        *,
        pins: list[str] | None = None,
        wait_for_slot: bool = False,
    ) -> PushResult:
        return self._request(
            path, params, pin=None, pins=pins, wait_for_slot=wait_for_slot
        )

    def _request(
        self,
        path: str,
        params: dict[str, Any],
        *,
        pin: str | None = None,
        pins: list[str] | None = None,
        wait_for_slot: bool = False,
    ) -> PushResult:
        """One HTTP attempt with limiter, breaker, and exhaustive error handling.

        Every failure that can happen over a network is caught here, because
        the alternative is a ``requests`` exception escaping into the YOLO loop
        and stopping the demo mid-incident.
        """

        # The background worker may wait for a slot: it is not on the detection
        # loop's thread, so a 250 ms sleep costs nothing. Dropping the request
        # there would be a correctness bug, not a saving -- a state change that
        # is discarded never gets sent, because the value is now cached as
        # "already published".
        if wait_for_slot:
            delay = self._limiter.delay_until_allowed()
            if delay > 0.0:
                self._sleep(delay)
        if not self._limiter.take():
            self.stats.skipped += 1
            # Critical: forget the values too, so the next frame re-diffs them
            # and tries again. Without this a rate-limited burst leaves the
            # cloud showing an old state forever.
            self._unequench(pin, pins)

            return PushResult(
                DeliveryStatus.SKIPPED, pin=pin, detail="rate limited locally"
            )

        if self._breaker.is_open():
            self.stats.skipped += 1

            return PushResult(
                DeliveryStatus.BACKING_OFF, pin=pin, detail="circuit open"
            )

        url = f"{self._server}/external/api/{path}"
        self.stats.requests += 1

        # The token goes on every call, including logEvent. Blynk has no header
        # form for this API, so a request built without it is rejected as
        # unauthorised. Built here rather than at each call site so no future
        # endpoint can forget it.
        params = {**params, "token": self._token}

        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.Timeout:
            return self._fail(pin, "timeout", pins=pins)
        except requests.ConnectionError:
            return self._fail(pin, "connection refused or unresolvable", pins=pins)
        except requests.RequestException as exc:
            return self._fail(
                pin, f"request failed: {type(exc).__name__}", pins=pins
            )
        except Exception as exc:  # pragma: no cover - last-resort guard
            return self._fail(
                pin, f"unexpected error: {type(exc).__name__}", pins=pins
            )

        status = int(response.status_code)
        if 200 <= status < 300:
            self._breaker.record_success()
            self.stats.successes += 1
            self.stats.last_success_at = datetime.now(timezone.utc).isoformat()

            return PushResult(DeliveryStatus.OK, pin=pin, http_status=status)

        message = _parse_blynk_error(response)
        if status == 429 or status >= 500:
            # Server-side trouble: back off, the value will be sent next time
            # it changes (or on the periodic refresh).
            self._breaker.record_failure()

            return self._fail(
                pin, f"http {status}", status=status, message=message, pins=pins
            )

        # 4xx other than 429 is a configuration problem -- a bad token or a
        # datastream that does not exist. Retrying cannot fix it, so log once
        # and keep going rather than tripping the breaker.
        self.stats.failures += 1
        self.stats.last_error = f"http {status}: {message}"
        logger.warning("blynk: rejected %s (%s)", path, message or status)

        return PushResult(
            DeliveryStatus.ERROR,
            pin=pin,
            http_status=status,
            detail="rejected",
            message=message,
        )

    def _fail(
        self,
        pin: str | None,
        detail: str,
        *,
        status: int | None = None,
        message: str = "",
        pins: list[str] | None = None,
    ) -> PushResult:
        self.stats.failures += 1
        self.stats.last_error = detail
        self._unequench(pin, pins)
        logger.warning("blynk: push failed (%s); local operation unaffected",
                       detail)

        return PushResult(
            DeliveryStatus.ERROR,
            pin=pin,
            http_status=status,
            detail=detail,
            message=message,
        )

    def _unequench(self, pin: str | None, pins: list[str] | None = None) -> None:
        """Forget the cached value of a failed pin so it is retried.

        Without this an update lost to a timeout would be marked "already
        sent", and the dashboard would show a stale value until the reading
        happened to change again -- possibly never, for something like the
        incident count.
        """

        targets = {pin} if pin else set(pins or ())
        if not targets:
            return
        with self._lock:
            for target in targets:
                self._last_values.pop(target, None)


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------

#: Blynk rejects a value whose type does not match the datastream. These are
#: the declared types from the template, so a bug becomes a local ``ERROR``
#: with a readable reason instead of a 400 in the middle of a demo.
_PIN_TYPES: Mapping[str, type] = {
    "V0": str,
    "V1": int,
    "V2": int,
    "V3": int,
    "V4": int,
    "V5": float,
    "V6": str,
    "V7": str,
    "V8": int,
    "V9": float,
    "V10": float,
}


def _normalise_datastream_value(pin: str, value: Any) -> tuple[Any, str]:
    """Coerce ``value`` to the declared type of ``pin``.

    Returns ``(normalised, problem)``. ``problem`` is a non-empty string when
    the pin is unknown or the value cannot be represented; the caller then
    reports an error without making a doomed request.
    """

    if pin not in DATASTREAM_PINS:
        return None, f"unknown datastream {pin}; expected V0-V10"

    expected = _PIN_TYPES[pin]
    if expected is str:
        text = "" if value is None else str(value)
        # Blynk's GET API puts values in the query string; a newline or an
        # over-long string would be silently truncated, so it is bounded here.
        return text.replace("\n", " ")[:255], ""

    if value is None:
        return 0 if expected is int else 0.0, ""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{pin} expects a number, got {value!r}"

    if number != number or number in (float("inf"), float("-inf")):
        return 0, f"{pin} received a non-finite value"

    if expected is int:
        return int(round(number)), ""
    if pin in ("V5", "V9"):
        return round(min(1.0, max(0.0, number)), 3), ""  # ratios

    return round(number, 1), ""  # FPS


def build_datastream_values(
    state: str,
    *,
    incident_id: int | None = None,
    violation_ratio: float | None = None,
    last_alert_time: str | None = None,
    link_online: bool = True,
    incident_count: int = 0,
    top_confidence: float | None = None,
    fps: float | None = None,
    buzzer: int | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for callers that hold loose state, not a reading.

    Kept as a thin adapter so ``tools/live_eye_shield_demo.py`` can call one
    function with its existing locals without importing the dataclass. The
    mapping logic itself lives in exactly one place,
    :func:`map_reading_to_datastreams`.
    """

    reading = LifecycleReading(
        state=state,
        incident_id=incident_id,
        violation_ratio=violation_ratio,
        last_alert_time=last_alert_time,
        link_online=link_online,
        incident_count=incident_count,
        top_confidence=top_confidence,
        fps=fps,
        buzzer=buzzer,
    )

    return map_reading_to_datastreams(reading).as_dict()


__all__ = [
    "BlynkClient",
    "BlynkConfigError",
    "BlynkStats",
    "DATASTREAM_PINS",
    "DatastreamValues",
    "DeliveryStatus",
    "EYE_VIOLATION_EVENT",
    "LifecycleReading",
    "PushResult",
    "build_datastream_values",
    "map_reading_to_datastreams",
]
