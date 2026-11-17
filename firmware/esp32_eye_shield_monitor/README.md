# ESP32-S3 eye-shield monitor node

Status: **not implemented** (Phase 5). This firmware is a Phase 0 placeholder that
reserves the structure required by [plan.md §4](../plan.md#4-required-repository-structure).

## Ownership split

The ESP32 owns the **latched** alarm state. The laptop proposes; the ESP32 decides
and holds. If the laptop process dies, the serial cable is unplugged, or Wi-Fi
drops, an active violation alarm stays active until a legitimate reset.

| Concern | Owner |
|---|---|
| Detection, temporal filtering, incident creation | Laptop |
| Alarm latch, LEDs, buzzer, link health | ESP32 (authoritative) |
| Acknowledge / silence / reset commands | ESP32 validates and applies |
| Notification delivery | ESP32 → Blynk |

## Files

| File | Purpose | Phase |
|---|---|---|
| `esp32_eye_shield_monitor.ino` | Setup/loop, non-blocking task scheduling | 5 |
| `config.example.h` | Template for Wi-Fi, Blynk token, pins | 5 |
| `config.h` | Real credentials — **git-ignored, never committed** | 5 |
| `protocol.h` | Shared message constants and field limits | 4/5 |
| `alarm_state.h` / `alarm_state.cpp` | Latched state machine | 5 |

## Acceptance gate (plan §11 Phase 5)

- A violation latches the red LED and buzzer.
- Removing the violation does **not** auto-clear the incident.
- Silence stops only the buzzer, not the visual alarm.
- An unsafe reset is rejected.
- A safe, acknowledged, link-healthy reset succeeds.

Until Phase 5 is complete, no claim about hardware behaviour is made anywhere in
this repository.