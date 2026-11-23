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
 * I2C OLED status display — SSD1306, 128x64
 *
 * A local head-up display for the operator. It is PURELY ADVISORY: the OLED
 * mirrors state that the incident lifecycle has already decided, and nothing on
 * the display can change a pin, clear an alarm or gate a transition. Losing the
 * display must never affect safety behaviour, which is why the sketch treats a
 * missing panel as a warning and keeps running.
 *
 * Wiring (3.3 V ONLY - the module is not 5 V tolerant):
 *   OLED VCC -> 3V3      OLED GND -> GND
 *   OLED SDA -> GPIO8    OLED SCL -> GPIO9
 *
 * GPIO8/GPIO9 are free on this board build. The three fixed outputs stay on
 * GPIO11 (green), GPIO12 (red) and GPIO14 (buzzer) and are NOT touched by this
 * feature. Do not move the OLED onto a strapping pin (GPIO0/3/45/46) or onto a
 * pin already used by the outputs.
 * ------------------------------------------------------------------------ */

/* 1 = build the OLED support in. Set to 0 to compile the sketch exactly as it
 * was before the display existed, with zero flash/RAM cost. */
#define OLED_ENABLED 1

/* I2C pins. ASSUMED: this is the requested wiring (SDA GPIO8, SCL GPIO9) and has
 * not been observed on hardware yet. Record it in docs/hardware_bringup.md once
 * the panel is actually connected. */
#define OLED_SDA_PIN 8
#define OLED_SCL_PIN 9

/* Panel geometry, in pixels. 128x64 is the common SSD1306 module. */
#define OLED_WIDTH 128
#define OLED_HEIGHT 64

/* I2C addresses to probe, in order. Modules ship as either 0x3C or 0x3D
 * depending on the DC-pin strap on the back of the panel, and the address is
 * detected at runtime rather than compiled in. */
#define OLED_PRIMARY_I2C_ADDRESS 0x3C
#define OLED_ALTERNATE_I2C_ADDRESS 0x3D

/* Bus clock. 400 kHz is the SSD1306 maximum and keeps a full 1 KB frame push
 * near 23 ms instead of the ~92 ms a 100 kHz bus would cost. That difference
 * matters because the refresh shares loop() with the serial poll and the
 * non-blocking self-test. */
#define OLED_I2C_CLOCK_HZ 400000UL

/* Minimum gap between full-frame pushes. The renderer also redraws immediately
 * when the content actually changes, so this is a floor, not the only trigger.
 * A full frame is ~1 KB, so 500 ms costs well under 5 % of loop() time. */
#define OLED_REFRESH_MS 500

/* Absolute floor between two pushes, even when the content changed. Protects
 * loop() from a burst of state changes turning into a burst of I2C traffic. */
#define OLED_MIN_DRAW_INTERVAL_MS 100

/* How long the boot splash stays up before live telemetry takes over. Counted
 * with millis(); loop() never blocks for it. */
#define OLED_BOOT_HOLD_MS 1500

/* If the panel is absent at power-up (unplugged, miswired, dead), retry the
 * address probe on this interval so plugging it in later still works. Bounded by
 * OLED_MAX_INIT_ATTEMPTS so a shorted bus does not cost a probe every cycle for
 * the whole run. */
#define OLED_RETRY_MS 5000
#define OLED_MAX_INIT_ATTEMPTS 3

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
