/*
 * config.example.h — ESP32-S3 UNO eye-shield monitor (Phase 2A bring-up)
 * ---------------------------------------------------------------------------
 * THIS FILE IS COMMITTED AND CONTAINS NO MACHINE-SPECIFIC DATA.
 *
 * To change pins, polarity, tone or baud on your own board, copy this file to
 * `config.h` in the same folder and edit the copy:
 *
 *     cp config.example.h config.h
 *
 * `config.h` is git-ignored (see .gitignore) and must never be committed. It is
 * the file that will later hold Wi-Fi SSID/password and the Blynk auth token in
 * Phase 5. Nothing secret belongs in this example file.
 *
 * If `config.h` is absent, the sketch falls back to the defaults below and
 * prints a notice on the serial monitor. The sketch always compiles unmodified.
 * ---------------------------------------------------------------------------
 */

#ifndef CONFIG_EXAMPLE_H
#define CONFIG_EXAMPLE_H

/* ---------------------------------------------------------------------------
 * Pin assignment — must match the physical wiring in docs/hardware_bringup.md
 * ------------------------------------------------------------------------ */

/* Green traffic-light LED signal. */
#define PIN_LED_GREEN 11

/* Red traffic-light LED signal. */
#define PIN_LED_RED 12

/* Passive buzzer signal (PWM/tone output, never a static DC level). */
#define PIN_BUZZER 14

/* ---------------------------------------------------------------------------
 * Output polarity — set per output after you observe the real hardware.
 *
 * 1 = active-HIGH : a logic HIGH level turns the output ON
 * 0 = active-LOW  : a logic LOW  level turns the output ON
 *
 * Both are supported at runtime with no code change. Leave at 1 until the
 * physical test in docs/hardware_bringup.md tells you otherwise; the bring-up
 * SELF_TEST makes the difference obvious (inverted output = on when it should
 * be off).
 * ------------------------------------------------------------------------ */

#define GREEN_ACTIVE_HIGH 1
#define RED_ACTIVE_HIGH 1
#define BUZZER_ACTIVE_HIGH 1

/* ---------------------------------------------------------------------------
 * Passive buzzer PWM settings
 * ------------------------------------------------------------------------ */

/* Tone frequency in Hz. A passive piezo is loudest roughly 2-4 kHz. */
#define BUZZER_TONE_HZ 2700

/* LEDC PWM resolution. 10 bits => duty range 0..1023. */
#define BUZZER_PWM_RESOLUTION_BITS 10

/* Duty cycle in percent while sounding. 50 % gives the loudest square wave. */
#define BUZZER_DUTY_PERCENT 50

/* LEDC channel index. Only used by the ESP32 Arduino core 2.x API; core 3.x
 * addresses the buzzer by pin number. Value is otherwise ignored. */
#define BUZZER_LEDC_CHANNEL 0

/* ---------------------------------------------------------------------------
 * Serial and self-test timing
 * ------------------------------------------------------------------------ */

/* Must match the Serial Monitor baud rate exactly. */
#define SERIAL_BAUD 115200

/* Dwell time per step of the non-blocking SELF_TEST sequence, in milliseconds.
 * Longer than 400 ms so a human can clearly see each step. */
#define SELF_TEST_STEP_MS 500

#endif /* CONFIG_EXAMPLE_H */
