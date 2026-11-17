/*
 * esp32_eye_shield_monitor.ino
 * ===========================================================================
 * Smart Biohazard & Laser Lab Eye-Shield Monitor — ESP32-S3 UNO node
 *
 * PHASE 2A: HARDWARE BRING-UP ONLY
 * ---------------------------------------------------------------------------
 * This sketch exists to prove that the three physical outputs are wired and
 * driven correctly. It is a bench self-test, not the product firmware.
 *
 * What this sketch IS:
 *   - a non-blocking driver for green LED (GPIO11), red LED (GPIO12) and a
 *     passive buzzer (GPIO14);
 *   - a serial command menu so a human can exercise each output by hand;
 *   - a SELF_TEST sequence that walks every output in a known order.
 *
 * What this sketch is NOT (deliberately out of scope for Phase 2A):
 *   - no Blynk, no Wi-Fi, no credentials;
 *   - no YOLO / camera integration;
 *   - no JSON serial state machine, no incident IDs, no latching states;
 *   - no alarm state machine (BOOT/SAFE/ALARM_LATCHED/...) — that is Phase 4/5.
 *
 * ABSOLUTE RULE FOR THIS PHASE: nothing here is a claim that the hardware
 * works. The outputs are unverified until a human connects the board and
 * reports the observed behaviour. See docs/hardware_bringup.md.
 * ---------------------------------------------------------------------------
 * Timing discipline: loop() never blocks. The self-test is a millis()-driven
 * state machine and serial input is polled non-blockingly. There is no
 * delay() anywhere in the runtime path.
 * ===========================================================================
 */

#include <Arduino.h>

/*
 * config.h is machine-specific and git-ignored. If it does not exist the
 * committed defaults in config.example.h are used instead, so the sketch
 * always compiles from a fresh clone.
 */
#if defined(__has_include)
#  if __has_include("config.h")
#    include "config.h"
#    define USING_LOCAL_CONFIG 1
#  else
#    include "config.example.h"
#    define USING_LOCAL_CONFIG 0
#  endif
#else
#  include "config.example.h"
#  define USING_LOCAL_CONFIG 0
#endif

#include "command_table.h"

/* ===========================================================================
 * Output abstraction
 *
 * Every write goes through here so polarity is applied in exactly one place.
 * `g_*_on` tracks the *logical* state (true = energised), not the pin level,
 * which is what STATUS reports and what makes an inverted module obvious.
 * =========================================================================== */

static bool g_green_on = false;
static bool g_red_on = false;
static bool g_buzzer_on = false;

static inline void writeActiveLevel(uint8_t pin, bool active_high, bool on) {
  const uint8_t level = (on == active_high) ? HIGH : LOW;
  digitalWrite(pin, level);
}

static void setGreen(bool on) {
  g_green_on = on;
  writeActiveLevel(PIN_LED_GREEN, GREEN_ACTIVE_HIGH, on);
}

static void setRed(bool on) {
  g_red_on = on;
  writeActiveLevel(PIN_LED_RED, RED_ACTIVE_HIGH, on);
}

/*
 * Passive buzzer drive.
 *
 * A passive piezo has no internal oscillator, so it needs a square wave, not a
 * DC level. The Arduino-ESP32 core changed the LEDC API between 2.x and 3.x, so
 * both are supported: 3.x attaches by pin, 2.x attaches by channel and then
 * needs the pin mapped to that channel. Compile-time version check keeps this
 * honest instead of guessing.
 */
static bool g_buzzer_pwm_ready = false;

static void buzzerApplyTone() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  ledcAttach(PIN_BUZZER, BUZZER_TONE_HZ, BUZZER_PWM_RESOLUTION_BITS);
  const uint32_t max_duty = (1u << BUZZER_PWM_RESOLUTION_BITS) - 1u;
  ledcWrite(PIN_BUZZER, (max_duty * BUZZER_DUTY_PERCENT) / 100u);
#else
  ledcSetup(BUZZER_LEDC_CHANNEL, BUZZER_TONE_HZ, BUZZER_PWM_RESOLUTION_BITS);
  ledcAttachPin(PIN_BUZZER, BUZZER_LEDC_CHANNEL);
  const uint32_t max_duty = (1u << BUZZER_PWM_RESOLUTION_BITS) - 1u;
  ledcWrite(BUZZER_LEDC_CHANNEL, (max_duty * BUZZER_DUTY_PERCENT) / 100u);
#endif
  g_buzzer_pwm_ready = true;
}

/*
 * Silence the passive buzzer.
 *
 * Duty 0 alone is enough to stop a piezo, but the pin must then be released
 * back to plain GPIO before driving it to its inactive level: writing
 * digitalWrite() to a pin still routed to the LEDC peripheral does not do what
 * it looks like it does, because the LEDC mux owns the pin. Detach, then park.
 */
static void buzzerSilence() {
  if (g_buzzer_pwm_ready) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    ledcWrite(PIN_BUZZER, 0);
    ledcDetach(PIN_BUZZER);
#else
    ledcWrite(BUZZER_LEDC_CHANNEL, 0);
    ledcDetachPin(PIN_BUZZER);
#endif
    g_buzzer_pwm_ready = false;
  }
  /* Park the pin at its inactive level so a miswired active-low driver stage
   * cannot hum while the buzzer is supposed to be silent. */
  writeActiveLevel(PIN_BUZZER, BUZZER_ACTIVE_HIGH, false);
}

static void setBuzzer(bool on) {
  g_buzzer_on = on;
  if (on) {
    buzzerApplyTone();
  } else {
    buzzerSilence();
  }
}

static void allOff() {
  setGreen(false);
  setRed(false);
  setBuzzer(false);
}

/* ===========================================================================
 * Non-blocking SELF_TEST sequencer
 *
 * Steps every SELF_TEST_STEP_MS. loop() returns between steps, so the serial
 * port stays responsive and the board never stalls.
 * =========================================================================== */

/*
 * The sequence is a flat table rather than nested switches: each entry states
 * the complete desired output combination, so a step can never leave a stale
 * output on, and adding or reordering a step is a one-line change.
 */
struct SelfTestStep {
  const char *label;
  bool green;
  bool red;
  bool buzzer;
};

static const SelfTestStep SELF_TEST_SEQUENCE[] = {
  { "GREEN ON    - expect GREEN lit, red dark, silent",            true,  false, false },
  { "GREEN OFF   - expect all dark, silent",                       false, false, false },
  { "RED ON      - expect RED lit, green dark, silent",            false, true,  false },
  { "RED OFF     - expect all dark, silent",                       false, false, false },
  { "BUZZER ON   - expect tone, both LEDs dark",                   false, false, true  },
  { "BUZZER OFF  - expect silence, both LEDs dark",                false, false, false },
  { "RED+BUZZER  - expect RED lit AND tone together",              false, true,  true  },
  { "ALL OFF     - expect all dark, silent (sequence ends)",       false, false, false },
};

static const uint8_t SELF_TEST_STEPS = sizeof(SELF_TEST_SEQUENCE) / sizeof(SELF_TEST_SEQUENCE[0]);

/* 0xFF = not running. */
static const uint8_t SELF_TEST_IDLE = 0xFF;
static uint8_t g_self_test_index = SELF_TEST_IDLE;
static uint32_t g_self_test_mark = 0;

/* Apply the complete output combination for one step. */
static void selfTestApply(uint8_t index) {
  const SelfTestStep &step = SELF_TEST_SEQUENCE[index];
  setGreen(step.green);
  setRed(step.red);
  setBuzzer(step.buzzer);
}

static void selfTestStart() {
  allOff();
  g_self_test_index = 0;
  g_self_test_mark = millis();
  Serial.println(F("SELF_TEST started. Watch each step; report anything unexpected."));
  Serial.print(F("STEP 1/"));
  Serial.print(SELF_TEST_STEPS);
  Serial.print(F(": "));
  Serial.println(SELF_TEST_SEQUENCE[0].label);
  selfTestApply(0);
}

static void selfTestCancel(const __FlashStringHelper *why) {
  if (g_self_test_index == SELF_TEST_IDLE) return;
  allOff();
  g_self_test_index = SELF_TEST_IDLE;
  Serial.print(F("SELF_TEST aborted ("));
  Serial.print(why);
  Serial.println(F("). All outputs OFF."));
}

/* Advance the sequence when the dwell time has elapsed. Never blocks. */
static void selfTestTick() {
  if (g_self_test_index == SELF_TEST_IDLE) return;
  if ((uint32_t)(millis() - g_self_test_mark) < SELF_TEST_STEP_MS) return;

  g_self_test_mark = millis();
  const uint8_t next = (uint8_t)(g_self_test_index + 1);

  if (next >= SELF_TEST_STEPS) {
    allOff();
    g_self_test_index = SELF_TEST_IDLE;
    Serial.println(F("SELF_TEST complete. All outputs OFF."));
    Serial.println(F("If any step was inverted, set the matching *_ACTIVE_HIGH to 0 in config.h."));
    return;
  }

  g_self_test_index = next;
  selfTestApply(next);
  Serial.print(F("STEP "));
  Serial.print((uint16_t)next + 1);
  Serial.print(F("/"));
  Serial.print(SELF_TEST_STEPS);
  Serial.print(F(": "));
  Serial.println(SELF_TEST_SEQUENCE[next].label);
}

/* ===========================================================================
 * Command handling
 * =========================================================================== */

static void printHelp() {
  Serial.println(F("--------------------------------------------------------------"));
  Serial.println(F("Commands (type in Serial Monitor, line ending = Newline):"));
  Serial.println(F("  GREEN_ON    green LED on, red unchanged"));
  Serial.println(F("  GREEN_OFF   green LED off"));
  Serial.println(F("  RED_ON      red LED on, green unchanged"));
  Serial.println(F("  RED_OFF     red LED off"));
  Serial.println(F("  BUZZER_ON   passive buzzer tone on"));
  Serial.println(F("  BUZZER_OFF  buzzer silent"));
  Serial.println(F("  ALL_OFF     every output off"));
  Serial.println(F("  SELF_TEST   run the 8-step non-blocking sequence"));
  Serial.println(F("  STATUS      report pin map, polarity and current state"));
  Serial.println(F("  HELP        this list"));
  Serial.println(F("--------------------------------------------------------------"));
}

static void printStatus() {
  Serial.println(F("---------------- STATUS ----------------"));
  Serial.println(F("board firmware  : Phase 2A bring-up (self-test only)"));
  Serial.print(F("config.h        : "));
  Serial.println(USING_LOCAL_CONFIG ? F("local config.h in use") : F("defaults from config.example.h"));
  Serial.print(F("PIN_LED_GREEN   : GPIO"));
  Serial.print(PIN_LED_GREEN);
  Serial.print(F("  polarity "));
  Serial.print(GREEN_ACTIVE_HIGH ? F("active-HIGH") : F("active-LOW"));
  Serial.print(F("  state "));
  Serial.println(g_green_on ? F("ON") : F("OFF"));
  Serial.print(F("PIN_LED_RED     : GPIO"));
  Serial.print(PIN_LED_RED);
  Serial.print(F("  polarity "));
  Serial.print(RED_ACTIVE_HIGH ? F("active-HIGH") : F("active-LOW"));
  Serial.print(F("  state "));
  Serial.println(g_red_on ? F("ON") : F("OFF"));
  Serial.print(F("PIN_BUZZER      : GPIO"));
  Serial.print(PIN_BUZZER);
  Serial.print(F("  polarity "));
  Serial.print(BUZZER_ACTIVE_HIGH ? F("active-HIGH") : F("active-LOW"));
  Serial.print(F("  tone "));
  Serial.print(BUZZER_TONE_HZ);
  Serial.print(F(" Hz @ "));
  Serial.print(BUZZER_DUTY_PERCENT);
  Serial.print(F("%  state "));
  Serial.println(g_buzzer_on ? F("ON") : F("OFF"));
  Serial.print(F("SELF_TEST       : "));
  Serial.println(g_self_test_index == SELF_TEST_IDLE ? F("idle") : F("running"));
  Serial.print(F("uptime_ms       : "));
  Serial.println(millis());
  Serial.println(F("----------------------------------------"));
}

/*
 * Command table.
 *
 * Dispatch is a table lookup rather than a chain of String comparisons for two
 * reasons: every command is declared in one visible place, and comparing a
 * String against `F("...")` would construct a temporary String per comparison
 * (WString has no __FlashStringHelper comparison overload). On ESP32, PSTR() is
 * the identity macro, so string literals already live in flash and F() adds
 * nothing here.
 *
 * The struct itself lives in command_table.h because the Arduino builder injects
 * generated prototypes above every sketch definition, and one of those
 * prototypes returns a CommandEntry pointer.
 */

static void cmdGreenOn()  { setGreen(true);  }
static void cmdGreenOff() { setGreen(false); }
static void cmdRedOn()    { setRed(true);    }
static void cmdRedOff()   { setRed(false);   }
static void cmdBuzzerOn() { setBuzzer(true); }
static void cmdBuzzerOff(){ setBuzzer(false);}
static void cmdAllOff()   { allOff();        }

static const CommandEntry OUTPUT_COMMANDS[] = {
  { "GREEN_ON",   "OK GREEN_ON",   cmdGreenOn   },
  { "GREEN_OFF",  "OK GREEN_OFF",  cmdGreenOff  },
  { "RED_ON",     "OK RED_ON",     cmdRedOn     },
  { "RED_OFF",    "OK RED_OFF",    cmdRedOff    },
  { "BUZZER_ON",  "OK BUZZER_ON",  cmdBuzzerOn  },
  { "BUZZER_OFF", "OK BUZZER_OFF", cmdBuzzerOff },
  { "ALL_OFF",    "OK ALL_OFF",    cmdAllOff    },
};
static const uint8_t OUTPUT_COMMAND_COUNT =
    sizeof(OUTPUT_COMMANDS) / sizeof(OUTPUT_COMMANDS[0]);

static const CommandEntry *findOutputCommand(const String &cmd) {
  for (uint8_t i = 0; i < OUTPUT_COMMAND_COUNT; i++) {
    if (cmd.equals(OUTPUT_COMMANDS[i].name)) return &OUTPUT_COMMANDS[i];
  }
  return nullptr;
}

static bool strEquals(const String &cmd, const char *literal) {
  return cmd.equals(literal);
}

static void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) return;

  /* Read-only commands never disturb a running self-test. */
  if (strEquals(cmd, "STATUS")) { printStatus(); return; }
  if (strEquals(cmd, "HELP") || strEquals(cmd, "?")) { printHelp(); return; }

  if (strEquals(cmd, "SELF_TEST")) { selfTestStart(); return; }

  /* Only a real output command cancels the self-test. A typo must leave the
   * sequence alone. */
  const CommandEntry *entry = findOutputCommand(cmd);
  if (entry == nullptr) {
    Serial.print(F("ERROR unknown command: "));
    Serial.println(cmd);
    Serial.println(F("Type HELP for the command list."));
    return;
  }

  selfTestCancel(F("manual command"));
  entry->handler();
  Serial.println(entry->reply);
}

/* ===========================================================================
 * setup() / loop()
 * =========================================================================== */

static String g_line;

void setup() {
  Serial.begin(SERIAL_BAUD);
  /* Native USB CDC boards can enumerate after setup() runs; wait briefly so the
   * banner is not lost. This is a bounded startup wait, not a runtime delay. */
  const uint32_t wait_start = millis();
  while (!Serial && (uint32_t)(millis() - wait_start) < 2000) {
    /* spin */
  }

  /* Park outputs at their inactive level before anything else, so no output
   * glitches on during boot. */
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  allOff();

  Serial.println();
  Serial.println(F("=============================================================="));
  Serial.println(F(" ESP32-S3 eye-shield monitor - PHASE 2A HARDWARE SELF-TEST"));
  Serial.println(F(" NOT the product firmware: no Blynk, no YOLO, no JSON protocol."));
  Serial.println(F("=============================================================="));
  Serial.print(F("config.h        : "));
  Serial.println(USING_LOCAL_CONFIG ? F("local config.h in use") : F("defaults from config.example.h"));
  Serial.print(F("green LED       : GPIO"));
  Serial.println(PIN_LED_GREEN);
  Serial.print(F("red LED         : GPIO"));
  Serial.println(PIN_LED_RED);
  Serial.print(F("passive buzzer  : GPIO"));
  Serial.println(PIN_BUZZER);
  Serial.print(F("serial baud     : "));
  Serial.println(SERIAL_BAUD);
  Serial.println(F("All outputs parked OFF."));
  printHelp();
  Serial.println(F("Ready. Type a command and press Enter."));
  g_line.reserve(64);
}

void loop() {
  /* Non-blocking serial read: accumulate a line, act on newline. */
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      handleCommand(g_line);
      g_line = "";
    } else if (g_line.length() < 63) {
      g_line += c;
    }
  }

  /* Non-blocking self-test advance. */
  selfTestTick();
}
