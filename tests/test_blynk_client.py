"""Tests for the laptop-side Blynk bridge (``src/blynk_client.py``).

Everything here runs with **no network**. ``requests.Session`` is replaced by a
fake whose behaviour each test controls, so the suite can pin down the three
properties that actually matter for a safety system:

* the cloud can never reach the state machine -- the module is handed a frozen
  snapshot and has no import path back to ``incident_lifecycle``;
* a failure of any kind (timeout, refused connection, 500, bad token) is
  reported and survived, never raised;
* one incident produces at most one notification.

The tests deliberately assert on the *query string* that would be sent, because
pin casing and value type are what Blynk's API rejects, and that mistake is
invisible unless the request itself is inspected.

Run with::

    .venv/bin/python -m pytest tests/test_blynk_client.py -q
"""

from __future__ import annotations

import pytest
import requests

from src.blynk_client import (
    DATASTREAM_PINS,
    EYE_VIOLATION_EVENT,
    BlynkClient,
    BlynkConfigError,
    DeliveryStatus,
    LifecycleReading,
    build_datastream_values,
    map_reading_to_datastreams,
)

TOKEN = "test-token-not-a-real-secret"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")

        return self._payload


class _Session:
    """Records every call and replays a scripted response."""

    def __init__(self, response: _Response | None = None, error: Exception | None = None):
        self.response = response or _Response()
        self.error = error
        self.calls: list[tuple[str, dict]] = []
        self.timeouts: list[float] = []
        self.headers: dict[str, str] = {}
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error

        return self.response

    def close(self):
        self.closed = True

    # -- helpers ---------------------------------------------------------

    @property
    def paths(self) -> list[str]:
        return [url.split("/external/api/")[-1] for url, _ in self.calls]

    def param_sets(self, path: str) -> list[dict]:
        return [p for (u, p) in self.calls if u.endswith(f"/external/api/{path}")]


def _client(session: _Session, **overrides) -> BlynkClient:
    settings = {
        "timeout": 1.0,
        "min_request_interval": 0.0,
        "alert_cooldown_seconds": 0.0,
        "use_thread": False,
    }
    settings.update(overrides)

    return BlynkClient(TOKEN, session=session, **settings)


def _alarm(incident_id: int = 7, **overrides) -> LifecycleReading:
    fields = {
        "state": "ALARM_LATCHED",
        "incident_id": incident_id,
        "violation_ratio": 0.93,
        "incident_count": 1,
        "top_confidence": 0.87,
        "fps": 15.5,
    }
    fields.update(overrides)

    return LifecycleReading(**fields)


# ---------------------------------------------------------------------------
# Task 2: mapping
# ---------------------------------------------------------------------------


def test_mapping_covers_exactly_the_eleven_documented_pins():
    values = map_reading_to_datastreams(LifecycleReading(state="SAFE")).as_dict()

    assert set(values) == set(DATASTREAM_PINS)
    assert len(values) == 11


@pytest.mark.parametrize(
    "state,expected",
    [
        # V2/V3/V4 mirror the physical GPIO. ACKNOWLEDGED and
        # SAFE_CLEAR_PENDING still hold the red LED latched on the device, so
        # the dashboard must show red=1 until the hardware clears it.
        ("SAFE", {"V2": 1, "V3": 0, "V4": 0}),
        ("ALARM_LATCHED", {"V2": 0, "V3": 1, "V4": 1}),
        ("ACKNOWLEDGED", {"V2": 0, "V3": 1, "V4": 0}),
        ("SAFE_CLEAR_PENDING", {"V2": 0, "V3": 1, "V4": 0}),
        ("FAULT", {"V2": 0, "V3": 0, "V4": 0}),
        ("WAITING_FOR_TARGET", {"V2": 0, "V3": 0, "V4": 0}),
    ],
)
def test_state_maps_to_the_documented_output_pins(state, expected):
    values = map_reading_to_datastreams(LifecycleReading(state=state)).as_dict()

    for pin, want in expected.items():
        assert values[pin] == want, f"{state} {pin}"


def test_unknown_state_is_passed_through_and_claims_no_safe_lamp():
    values = map_reading_to_datastreams(LifecycleReading(state="brand_new")).as_dict()

    assert values["V0"] == "BRAND_NEW"
    # Showing a green lamp for a state this table has never seen would be a
    # silent false "everything is fine" on the dashboard.
    assert values["V2"] == 0
    assert values["V3"] == 0


def test_explicit_pin_levels_win_over_the_state_table():
    # A manual self-test can drive lamps that the lifecycle does not describe;
    # the dashboard must show the pins, not the state machine's intent.
    values = map_reading_to_datastreams(
        LifecycleReading(state="SAFE", green=0, red=1, buzzer=1)
    ).as_dict()

    assert values["V2"] == 0
    assert values["V3"] == 1
    assert values["V4"] == 1


def test_ratios_are_clamped_and_floats_are_bounded():
    reading = LifecycleReading(
        state="SAFE", violation_ratio=1.0 + 1e-9, top_confidence=-0.4
    )

    assert reading.violation_ratio == 1.0
    assert reading.top_confidence == 0.0


def test_missing_numbers_become_zero_rather_than_being_omitted():
    # A datastream that stops receiving values keeps displaying its last one
    # forever, so silence is worse than an explicit 0.
    values = map_reading_to_datastreams(LifecycleReading(state="SAFE")).as_dict()

    assert values["V1"] == 0
    assert values["V5"] == 0.0
    assert values["V9"] == 0.0
    assert values["V10"] == 0.0
    assert values["V6"] == ""


def test_build_datastream_values_matches_the_dataclass_path():
    loose = build_datastream_values(
        "alarm_latched", incident_id=2, violation_ratio=0.5, fps=12.34
    )
    strict = map_reading_to_datastreams(
        LifecycleReading(
            state="alarm_latched", incident_id=2, violation_ratio=0.5, fps=12.34
        )
    ).as_dict()

    assert loose == strict
    assert loose["V10"] == 12.3


def test_link_status_reflects_the_flag():
    online = map_reading_to_datastreams(LifecycleReading(state="SAFE")).as_dict()
    offline = map_reading_to_datastreams(
        LifecycleReading(state="SAFE", link_online=False)
    ).as_dict()

    assert online["V7"] == "ONLINE"
    assert offline["V7"] == "OFFLINE"


# ---------------------------------------------------------------------------
# Task 1: single datastream update
# ---------------------------------------------------------------------------


def test_update_datastream_sends_a_batch_update_with_the_token():
    session = _Session()
    client = _client(session)

    result = client.update_datastream("V0", "SAFE")

    assert result.ok
    assert session.paths == ["batch/update"]
    (params,) = session.param_sets("batch/update")
    assert params["token"] == TOKEN
    assert params["V0"] == "SAFE"


def test_update_datastream_accepts_lowercase_pin_names():
    session = _Session()
    client = _client(session)

    client.update_datastream("v1", 4)

    (params,) = session.param_sets("batch/update")
    assert params["V1"] == 4
    assert "v1" not in params


def test_unknown_pin_is_rejected_without_a_request():
    session = _Session()
    client = _client(session)

    result = client.update_datastream("V11", 1)

    assert result.status is DeliveryStatus.ERROR
    assert "unknown datastream" in result.detail
    assert session.calls == []


def test_unchanged_value_is_skipped_but_changed_value_is_sent():
    session = _Session()
    client = _client(session)

    client.update_datastream("V8", 3)
    same = client.update_datastream("V8", 3)
    different = client.update_datastream("V8", 4)

    assert same.status is DeliveryStatus.SKIPPED
    assert same.sent  # the cloud already holds this value
    assert different.ok
    assert len(session.calls) == 2


def test_force_republishes_an_unchanged_value():
    session = _Session()
    client = _client(session)

    client.update_datastream("V0", "SAFE")
    forced = client.update_datastream("V0", "SAFE", force=True)

    assert forced.ok
    assert len(session.calls) == 2


def test_string_values_are_newline_free_and_bounded():
    session = _Session()
    client = _client(session)

    client.update_datastream("V6", "2026-11-23T14:00:00Z\nsecond line" + "x" * 300)

    (params,) = session.param_sets("batch/update")
    assert "\n" not in params["V6"]
    assert len(params["V6"]) <= 255


# ---------------------------------------------------------------------------
# Task 1 + 3: the alert event
# ---------------------------------------------------------------------------


def test_trigger_alert_calls_log_event_with_the_documented_code():
    session = _Session()
    client = _client(session)

    result = client.trigger_alert(5, "Goggles removed")

    assert result.ok
    (params,) = session.param_sets("logEvent")
    assert params["code"] == EYE_VIOLATION_EVENT
    assert "#5" in params["description"]
    assert "Goggles removed" in params["description"]
    assert params["token"] == TOKEN


def test_trigger_alert_fires_only_once_per_incident():
    session = _Session()
    client = _client(session)

    first = client.trigger_alert(5, "Goggles removed")
    second = client.trigger_alert(5, "Goggles removed")

    assert first.ok
    assert second.status is DeliveryStatus.SKIPPED
    assert len(session.param_sets("logEvent")) == 1


def test_a_new_incident_id_fires_again():
    session = _Session()
    client = _client(session, alert_cooldown_seconds=0.0)

    client.trigger_alert(5, "one")
    client.trigger_alert(6, "two")

    assert len(session.param_sets("logEvent")) == 2


def test_alert_cooldown_suppresses_a_burst_of_new_incident_ids():
    # A pathological lifecycle allocating an id every few seconds must not turn
    # into a stream of push notifications.
    session = _Session()
    client = _client(session, alert_cooldown_seconds=60.0)

    results = [client.trigger_alert(i, "x") for i in range(1, 6)]

    assert results[0].ok
    assert all(r.status is DeliveryStatus.SKIPPED for r in results[1:])
    assert len(session.param_sets("logEvent")) == 1


def test_alert_without_an_incident_id_is_a_skip_not_an_error():
    session = _Session()
    client = _client(session)

    result = client.trigger_alert(0, "nothing")

    assert result.status is DeliveryStatus.SKIPPED
    assert session.calls == []


def test_a_failed_first_attempt_does_not_blacklist_the_incident():
    # Regression test for the bug where ``_alerts_fired`` recorded the id
    # *before* the request, so a skipped/failed first attempt permanently
    # suppressed every later trigger of the same id and the notification never
    # reached the phone. The id must only be remembered on a successful HTTP
    # delivery.
    session = _Session(error=requests.ConnectionError("boom"))
    client = _client(session)

    first = client.trigger_alert(5, "Goggles removed")
    assert first.status is DeliveryStatus.ERROR
    assert 5 not in client._alerts_fired

    # Once the network recovers, the same id must be allowed through.
    session.error = None
    second = client.trigger_alert(5, "Goggles removed")
    assert second.ok
    assert 5 in client._alerts_fired
    # Only the successful retry counts as a delivered event; the failed first
    # attempt must not have blacklisted the id (the whole point of the fix).
    assert client.stats.events == 1


def test_clear_alerts_allows_a_reused_incident_id_to_fire_again():
    session = _Session()
    client = _client(session)

    assert client.trigger_alert(5, "one").ok
    assert client.trigger_alert(5, "again").status is DeliveryStatus.SKIPPED

    client.clear_alerts()
    assert client.trigger_alert(5, "after clear").ok
    assert len(session.param_sets("logEvent")) == 2


def test_push_status_fires_the_event_when_entering_alarm_latched():
    session = _Session()
    client = _client(session)

    results = client.push_status(_alarm(9))

    assert results["event"].ok
    assert len(session.param_sets("logEvent")) == 1


def test_push_status_does_not_fire_the_event_on_repeated_alarm_frames():
    session = _Session()
    client = _client(session)

    for _ in range(30):  # a second of 30 fps alarm frames
        results = client.push_status(_alarm(9))

    assert len(session.param_sets("logEvent")) == 1
    assert results["event"].status is DeliveryStatus.SKIPPED


def test_push_status_does_not_fire_the_event_outside_alarm_latched():
    session = _Session()
    client = _client(session)

    for state in ("SAFE", "WAITING_FOR_TARGET", "VIOLATION_PENDING", "FAULT"):
        client.push_status(LifecycleReading(state=state, incident_id=3))

    assert session.param_sets("logEvent") == []


# ---------------------------------------------------------------------------
# Failure behaviour: nothing may escape into the detection loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout("slow"),
        requests.ConnectionError("refused"),
        requests.RequestException("broken"),
    ],
)
def test_network_errors_are_reported_and_swallowed(error):
    session = _Session(error=error)
    client = _client(session)

    result = client.update_datastream("V0", "SAFE")

    assert result.status is DeliveryStatus.ERROR
    assert client.stats.failures == 1


def test_a_failed_push_is_retried_on_the_next_identical_value():
    # The value must not be marked "already sent" when the request failed,
    # otherwise the dashboard keeps a stale reading indefinitely.
    session = _Session(error=requests.ConnectionError("down"))
    client = _client(session)

    client.update_datastream("V8", 3)
    session.error = None
    retry = client.update_datastream("V8", 3)

    assert retry.ok
    assert len(session.calls) == 2


def test_rejected_token_surfaces_blyinks_own_message():
    session = _Session(
        response=_Response(
            400, {"error": {"message": "Invalid token."}}, text="{}"
        )
    )
    client = _client(session)

    result = client.update_datastream("V0", "SAFE")

    assert result.status is DeliveryStatus.ERROR
    assert result.http_status == 400
    assert result.message == "Invalid token."


def test_server_errors_trip_the_circuit_breaker_and_stop_the_hammering():
    session = _Session(response=_Response(503, text="unavailable"))
    client = _client(
        session, circuit_breaker_threshold=2, circuit_breaker_cooldown=60.0
    )

    client.update_datastream("V0", "SAFE")
    client.update_datastream("V1", 1)
    before = len(session.calls)
    third = client.update_datastream("V2", 1)

    assert third.status is DeliveryStatus.BACKING_OFF
    assert len(session.calls) == before
    assert third.sent is False


def test_rate_limiter_drops_requests_inside_the_minimum_interval():
    session = _Session()
    client = _client(session, min_request_interval=10.0)

    first = client.update_datastream("V0", "SAFE")
    second = client.update_datastream("V1", 1)

    assert first.ok
    assert second.status is DeliveryStatus.SKIPPED
    assert len(session.calls) == 1


def test_disabled_client_makes_no_requests_at_all():
    session = _Session()
    client = _client(session, enabled=False)

    result = client.update_datastream("V0", "SAFE")
    alert = client.trigger_alert(1, "x")

    assert result.status is DeliveryStatus.SKIPPED
    assert alert.status is DeliveryStatus.SKIPPED
    assert session.calls == []


def test_missing_token_never_raises_from_a_push():
    client = BlynkClient("", session=_Session(), use_thread=False)

    assert client.enabled is False
    assert client.update_datastream("V0", "SAFE").status is DeliveryStatus.SKIPPED


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_from_env_raises_a_readable_error_without_a_token(monkeypatch):
    # python-dotenv resolves the .env relative to the *calling module*, so a
    # developer's real token in the repo's .env is found no matter what the
    # test's cwd is. Stub the loader rather than chdir around it.
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("BLYNK_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BLYNK_ENABLED", raising=False)

    with pytest.raises(BlynkConfigError) as excinfo:
        BlynkClient.from_env(require=True, session=_Session())

    assert "BLYNK_AUTH_TOKEN" in str(excinfo.value)


def test_a_disabled_client_needs_no_token(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("BLYNK_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("BLYNK_ENABLED", "0")

    client = BlynkClient.from_env(require=True, session=_Session(), use_thread=False)

    assert client.enabled is False


def test_environment_wins_over_the_dotenv_file(monkeypatch, tmp_path):
    # A stale .env must never shadow a token the shell exported.
    (tmp_path / ".env").write_text("BLYNK_AUTH_TOKEN=from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BLYNK_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("BLYNK_ENABLED", "1")

    client = BlynkClient.from_env(session=_Session(), use_thread=False)

    assert client.enabled


def test_from_env_reads_the_token_and_can_be_opted_out(monkeypatch):
    monkeypatch.setenv("BLYNK_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("BLYNK_ENABLED", "1")
    client = BlynkClient.from_env(session=_Session(), use_thread=False)
    assert client.enabled

    monkeypatch.setenv("BLYNK_AUTH_TOKEN", "")
    monkeypatch.setenv("BLYNK_ENABLED", "0")
    disabled = BlynkClient.from_env(require=True, session=_Session(), use_thread=False)
    assert disabled.enabled is False


def test_no_credential_is_hardcoded_in_the_module():
    import inspect

    import src.blynk_client as module

    source = inspect.getsource(module)
    assert "w-rBFbcmiPfIHtQhQdZGWWfpjffjX-N1" not in source


# ---------------------------------------------------------------------------
# Threaded mode
# ---------------------------------------------------------------------------


def test_threaded_client_delivers_without_blocking_the_caller():
    session = _Session()
    client = BlynkClient(TOKEN, session=session, use_thread=True,
                         min_request_interval=0.0)
    try:
        result = client.update_datastream("V0", "SAFE")
        assert result.ok
        assert client.flush(timeout=3.0)
        assert session.paths == ["batch/update"]
    finally:
        client.close()


def test_close_is_idempotent_and_stops_the_worker():
    session = _Session()
    client = BlynkClient(TOKEN, session=session, use_thread=True)

    client.close()
    client.close()

    assert session.closed


def test_context_manager_closes_the_session():
    session = _Session()

    with BlynkClient(TOKEN, session=session, use_thread=False) as client:
        client.update_datastream("V0", "SAFE")

    assert session.closed
