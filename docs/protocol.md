# Serial protocol (draft for Phase 4)

Status: **not implemented**. This document records the contract defined in
[plan.md §8](../plan.md#8-serial-protocol) so Phase 4 implements a reviewed
specification rather than inventing one.

## Transport

| Property | Value |
|---|---|
| Physical layer | USB CDC serial (ESP32-S3) |
| Baud rate | 115200 |
| Encoding | UTF-8 |
| Framing | One JSON object per `\n`-terminated line |
| Max line length | 512 bytes — longer lines are discarded and counted as errors |
| Protocol version | 1 |

## Laptop → ESP32

```json
{"type":"hello","protocol":1,"app":"eye_shield_monitor"}
{"type":"heartbeat","seq":101,"safe_observed":true}
{"type":"violation","incident_id":12,"class":"no_goggles","confidence":0.91,"duration_ms":2087}
{"type":"safety_state","state":"safe","clear_duration_ms":2150}
```

## ESP32 → laptop

```json
{"type":"hello_ack","protocol":1,"device":"esp32_s3"}
{"type":"ack","incident_id":12,"state":"alarm_latched"}
{"type":"state","incident_id":12,"state":"silenced"}
{"type":"reset_result","incident_id":12,"accepted":false,"reason":"unsafe_or_link_unhealthy"}
{"type":"heartbeat_ack","seq":101}
```

## Validation rules

| Rule | Behaviour on violation |
|---|---|
| `type` must be a known string | Drop line, increment `malformed_count` |
| Required fields present, correct types | Drop line, log reason |
| `protocol` must equal 1 | Reply/expect re-handshake; do not act |
| `incident_id` integers, monotonic per session | Duplicate ID is acknowledged but never re-alarms or re-notifies |
| Line length ≤ 512 bytes | Drop line |
| Invalid JSON | Drop line; never raise out of the parser |

## State effects

| Message | ESP32 effect |
|---|---|
| `violation` (new `incident_id`) | → `ALARM_LATCHED`, red solid, buzzer pattern, one Blynk event |
| `violation` (duplicate ID) | `ack` only, no state change, no Blynk event |
| `safety_state` (`clear_duration_ms` ≥ clearance) | Enables `READY_TO_RESET` **only if** acknowledged |
| `heartbeat` timeout (> 3 × `heartbeat_seconds`) | Link unhealthy: reset rejected; alarm stays latched |
| `hello` | Reset parser state, reply `hello_ack`, then `state` sync |

## Reliability commitments

- Unacknowledged `violation` is retried `retry_count` times at `ack_timeout_seconds`,
  then reported as a degraded link — never silently dropped.
- Reconnection performs `hello` → `hello_ack` → state sync before new violations.
- A serial disconnect **never** clears a latched incident.
- The laptop never displays "safe" without a fresh, valid state report from the device.
