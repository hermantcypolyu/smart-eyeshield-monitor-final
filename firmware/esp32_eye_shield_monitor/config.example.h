/*
 * config.example.h — ESP32-S3 UNO eye-shield monitor (Phase 2A bring-up)
 * ===========================================================================
 * THIS FILE IS COMMITTED AND CONTAINS NO MACHINE-SPECIFIC DATA AND NO SECRETS.
 *
 * To change pins, polarity, buzzer mode or timing on your own board, copy this
 * file to `config.h` in the same folder and edit the copy:
 *
 *     cp config.example.h config.h
 *
 * `config.h` is git-ignored (see .gitignore) and must never be committed. In
 * Phase 5 it will hold the Wi-Fi SSID/password and the Blynk auth token.
 *
 * If `config.h` is absent the sketch falls back to the defaults below, so a
 * fresh clone always compiles unmodified.
 *
 * THE VALUES BELOW ARE THE VERIFIED CONFIGURATION for the actual hardware,
 * recorded 2026-11-18 from a physical Phase 2A test. Values annotated MEASURED
 * were observed on the board. Anything not so annotated is still an assumption:
 * do not label a value MEASURED without observing it yourself. See
 * docs/hardware_bringup.md section 9.
 * ===========================================================================
 */

#ifndef CONFIG_EXAMPLE_H
#define CONFIG_EXAMPLE_H

/* ---------------------------------------------------------------------------
 * Pin assignment — must match the physical wiring in docs/hardware_bringup.md
 * ------------------------------------------------------------------------ */

/* Green traffic-light LED signal. MEASURED: GPIO11 drives the green lamp. */
#define GREEN_LED_PIN 11

/* Red traffic-light LED signal. MEASURED: GPIO12 drives the red lamp. */
#define RED_LED_PIN 12

/* Buzzer signal. MEASURED: GPIO14 drives the three-pin buzzer module. */
#define BUZZER_PIN 14

/* ---------------------------------------------------------------------------
 * Output polarity — set per output after you observe the real hardware.
 *
 * 1 = active-HIGH : a logic HIGH level turns the output ON
 * 0 = active-LOW  : a logic LOW  level turns the output ON
 *
 * These are NOT guesses: both traffic-light channels and the buzzer were
 * observed on the real hardware on 2026-11-18. The two subsystems differ in
 * polarity, which is why each output has its own flag rather than one global
 * setting. Polarity is applied in exactly one place (writeActiveLevel) so
 * changing it remains a one-line edit.
 * ------------------------------------------------------------------------ */

/* MEASURED: traffic-light inputs are active-HIGH (a HIGH level lights the lamp). */
#define GREEN_LED_ACTIVE_HIGH 1
#define RED_LED_ACTIVE_HIGH 1

/* MEASURED: this three-pin buzzer module is LOW-level trigger, so ON is a LOW
 * level on GPIO14. This is a common module convention and is NOT a fault. */
#define BUZZER_ACTIVE_HIGH 0

/* ---------------------------------------------------------------------------
 * Buzzer type
 *
 * 1 = passive piezo: has no internal oscillator, so it needs a square wave.
 *     The sketch drives it with LEDC PWM at BUZZER_FREQUENCY_HZ.
 * 0 = active buzzer: has its own oscillator, so it only needs a DC level.
 *     The sketch drives it with digitalWrite and ignores the frequency.
 *
 * MEASURED: the installed module is ACTIVE (it has its own oscillator), so it is
 * driven as a DC level and BUZZER_FREQUENCY_HZ is ignored. Do not set this back
 * to 1 unless you physically replace the buzzer with a passive piezo.
 * ------------------------------------------------------------------------ */

#define BUZZER_IS_PASSIVE 0

/* ---------------------------------------------------------------------------
 * Buzzer tone settings (only used when BUZZER_IS_PASSIVE is 1)
 * ------------------------------------------------------------------------ */

/* Initial tone frequency in Hz. A passive piezo is loudest roughly 2-4 kHz. */
#define BUZZER_FREQUENCY_HZ 2000

/* LEDC PWM resolution. 10 bits => duty range 0..1023. */
#define BUZZER_PWM_RESOLUTION_BITS 10

/* Duty cycle in percent while sounding. 50 % gives the loudest square wave. */
#define BUZZER_DUTY_PERCENT 50

/* LEDC channel index. Used only by the ESP32 Arduino core 2.x API; core 3.x
 * addresses the buzzer by pin number, so the value is otherwise ignored. */
#define BUZZER_LEDC_CHANNEL 0

/* ---------------------------------------------------------------------------
 * Optional yellow traffic-light channel — DISABLED BY DEFAULT
 *
 * IMPORTANT: the yellow input is NOT CONNECTED on the current hardware. It was
 * recorded as not connected in docs/hardware_bringup.md (sections 2.1, 2.2 and
 * 9) and in plan.md. Do not enable this, and do not pick a pin, until you have
 * physically wired it and verified it the same way green and red were verified
 * on 2026-11-18.
 *
 * Yellow is a *third* advisory lamp, not part of the safety interlock: green and
 * red remain the only channels that carry the safe/alarm decision, and they keep
 * their mutual exclusion. Yellow is compiled out by default so no unverified pin
 * is ever driven.
 * ------------------------------------------------------------------------ */

/* 1 = yellow channel present and wired. Leave at 0 until verified in person. */
#define YELLOW_LED_ENABLED 0

/* Only meaningful when YELLOW_LED_ENABLED is 1. There is no verified pin for
 * this channel, so 0 means "unassigned" rather than "GPIO0". */
#define YELLOW_LED_PIN 0

/* Same convention as the other channels: 1 = active-HIGH, 0 = active-LOW.
 * UNVERIFIED — this is a placeholder, not a measurement. */
#define YELLOW_LED_ACTIVE_HIGH 1

/* Blink half-period in milliseconds for YELLOW_SLOW_BLINK / YELLOW_FAST_BLINK. */
#define YELLOW_SLOW_BLINK_MS 500
#define YELLOW_FAST_BLINK_MS 150

/* ---------------------------------------------------------------------------
 * Incident lifecycle timing
 * ------------------------------------------------------------------------ */

/* How long the board waits for any line from the laptop before it treats the
 * link as dead. A RESET is refused while the link is stale, so a crashed laptop
 * cannot clear a latched alarm. Keep this comfortably larger than the laptop's
 * heartbeat period (the demo sends HEARTBEAT every 1 s). */
#define HEARTBEAT_TIMEOUT_MS 3000

/* ---------------------------------------------------------------------------
 * Serial and self-test timing
 * ------------------------------------------------------------------------ */

/* Must match the Serial Monitor baud rate exactly. MEASURED: 115200 is stable
 * for both upload and Serial Monitor on this board. */
#define SERIAL_BAUD_RATE 115200

/* Dwell time per step of the non-blocking SELF_TEST sequence, in milliseconds.
 * Long enough that a human can clearly see and hear each step. */
#define SELF_TEST_STEP_MS 500

#endif /* CONFIG_EXAMPLE_H */
