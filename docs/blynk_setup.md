# Blynk setup

Status: **not started** (Phase 6).

This is a Phase 0 placeholder so the documentation set matches the repository
structure in [plan.md §4](../plan.md#4-required-repository-structure). Contents
will be written when Phase 6 begins, and will describe only verified behaviour.

## Required content

- Blynk template ID, template name, and datastream allocation:

  | Virtual pin | Purpose |
  |---|---|
  | V0 | Current state (text/enumerated) |
  | V1 | Active incident ID |
  | V2 | Detection confidence |
  | V3 | Silence button |
  | V4 | Acknowledge button |
  | V5 | Reset button |
  | V6 | Laptop/serial link health |
  | V7 | Last event timestamp |

- Exact dashboard widget settings and the event configuration used for push
  notifications.
- Where credentials live: `firmware/esp32_eye_shield_monitor/config.h`, generated
  from `config.example.h` and git-ignored.
- The one-notification-per-incident rule and how duplicate serial messages are
  prevented from producing duplicate events.
- Behaviour when Wi-Fi or Blynk is unavailable: the local serial link, latched
  alarm, LEDs, and buzzer must be unaffected.
- A dated record of notification delivery and command-mapping tests (plan §11
  Phase 6 acceptance, IOT-01/IOT-02).
