/*
 * esp32_eye_shield_monitor.ino
 * ===========================================================================
 * Smart Biohazard & Laser Lab Eye-Shield Monitor — ESP32-S3 UNO node
 *
 * PHASE 2A: MANDATORY FIRMWARE AND HARDWARE BRING-UP
 * ---------------------------------------------------------------------------
 * This is working firmware, not a placeholder and not a wiring diagram. It
 * proves that the three physical outputs are wired and driven correctly, and it
 * is the same sketch that Phases 4 and 5 will upgrade in place. There is
 * deliberately no second sketch: every later feature (JSON protocol, latched
 * alarm state machine, Blynk) evolves in this file.
 *
 * What this sketch IS:
 *   - a non-blocking driver for green LED (GPIO11), red LED (GPIO12) and a
 *     buzzer (GPIO14), with polarity applied in exactly one place;
 *   - a line-oriented Serial Monitor command interface;
 *   - a non-blocking SELF_TEST sequence that walks every output in order.
 *
 * What this sketch is NOT (deliberately out of scope for Phase 2A):
 *   - no Blynk, no Wi-Fi, no credentials;
 *   - no YOLO / camera integration;
 *   - no JSON serial protocol, no incident IDs, no latched alarm state machine
 *     (BOOT / SAFE / ALARM_LATCHED / SILENCED / ACKNOWLEDGED / READY_TO_RESET /
 *     FAULT). SAFE / ALARM / SILENCE here are bring-up presets that drive the
 *     pins, not a latched state machine.
 *
 * ABSOLUTE RULE: nothing here is a claim that the hardware works. Every output
 * is UNVERIFIED until a human connects the board, runs the tests in
 * docs/hardware_bringup.md, and reports what was physically observed.
 * ---------------------------------------------------------------------------
 * Timing discipline: loop() never blocks. The self-test is driven by millis()
 * and serial input is polled. There is no delay() in the runtime path.
 * ===========================================================================
 */

#include <Arduino.h>

/*
 * The incident lifecycle lives in a plain-C++ header that has no Arduino
 * dependency, so the same code the board runs is also exercised on the host by
 * tests/test_incident_core.py. Behaviour that only exists in this .ino is
 * behaviour nobody can regression-test without plugging in a board.
 */
#include "incident_core.h"

/* The core lives in its own namespace so its names can never collide with an
 * Arduino or core-library symbol. A short alias keeps this sketch readable. */
using eye_shield::CoreOutputs;
using eye_shield::FwCommand;
using eye_shield::FwState;
using eye_shield::IncidentCore;
using eye_shield::ParsedCommand;
using eye_shield::fwCommandName;
using eye_shield::parseFwCommand;
namespace reply = eye_shield::reply;

/*
 * config.h is machine-specific and git-ignored. If absent, the committed
 * defaults in config.example.h are used, so a fresh clone always compiles.
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

/* ===========================================================================
 * Output abstraction
 *
 * Every pin write goes through writeActiveLevel() so polarity is applied in
 * exactly one place. The g_*_on flags track the LOGICAL state (true =
 * energised), not the pin level, which is what STATUS reports and what makes an
 * inverted module obvious during bring-up.
 * =========================================================================== */

static bool g_green_on = false;
static bool g_red_on = false;
static bool g_buzzer_on = false;

/* The single authority on incident state. Declared here rather than next to its
 * command handlers because STATUS and the pin helpers below need to read it. */
static IncidentCore g_core(HEARTBEAT_TIMEOUT_MS);

static inline void writeActiveLevel(uint8_t pin, bool active_high, bool on) {
  digitalWrite(pin, (on == active_high) ? HIGH : LOW);
}

/* ---------------------------------------------------------------------------
 * Buzzer primitives.
 *
 * A passive piezo has no internal oscillator, so it needs a square wave, not a
 * DC level. An active buzzer has its own oscillator and needs only a DC level.
 * BUZZER_IS_PASSIVE selects between them at compile time.
 *
 * The Arduino-ESP32 core changed the LEDC API between 2.x and 3.x, so both are
 * supported: 3.x attaches by pin, 2.x by channel then maps the pin to it.
 * ------------------------------------------------------------------------ */

#if BUZZER_IS_PASSIVE
/* True only while the LEDC peripheral is actually generating the tone, so that
 * stopBuzzerTone() detaches exactly once and never touches a pin it does not
 * own. Unused in active-buzzer builds, hence the guard. */
static bool g_buzzer_pwm_ready = false;
#endif

static void startBuzzerTone() {
#if BUZZER_IS_PASSIVE
#  if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  ledcAttach(BUZZER_PIN, BUZZER_FREQUENCY_HZ, BUZZER_PWM_RESOLUTION_BITS);
  const uint32_t max_duty = (1u << BUZZER_PWM_RESOLUTION_BITS) - 1u;
  ledcWrite(BUZZER_PIN, (max_duty * BUZZER_DUTY_PERCENT) / 100u);
#  else
  ledcSetup(BUZZER_LEDC_CHANNEL, BUZZER_FREQUENCY_HZ, BUZZER_PWM_RESOLUTION_BITS);
  ledcAttachPin(BUZZER_PIN, BUZZER_LEDC_CHANNEL);
  const uint32_t max_duty = (1u << BUZZER_PWM_RESOLUTION_BITS) - 1u;
  ledcWrite(BUZZER_LEDC_CHANNEL, (max_duty * BUZZER_DUTY_PERCENT) / 100u);
#  endif
  g_buzzer_pwm_ready = true;
#else
  /* Active buzzer: a static level is enough. */
  writeActiveLevel(BUZZER_PIN, BUZZER_ACTIVE_HIGH, true);
#endif
}

/*
 * Silence the buzzer.
 *
 * For a passive piezo, duty 0 alone is enough to stop the tone, but the pin must
 * then be released back to plain GPIO before being parked at its inactive
 * level: digitalWrite() on a pin still routed to the LEDC peripheral does not do
 * what it looks like, because the LEDC mux owns the pin. Detach, then park.
 */
static void stopBuzzerTone() {
#if BUZZER_IS_PASSIVE
  if (g_buzzer_pwm_ready) {
#  if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    ledcWrite(BUZZER_PIN, 0);
    ledcDetach(BUZZER_PIN);
#  else
    ledcWrite(BUZZER_LEDC_CHANNEL, 0);
    ledcDetachPin(BUZZER_PIN);
#  endif
    g_buzzer_pwm_ready = false;
  }
#endif
  /* Park at the inactive level so a miswired driver stage cannot hum while the
   * buzzer is supposed to be silent. */
  writeActiveLevel(BUZZER_PIN, BUZZER_ACTIVE_HIGH, false);
}

static void setGreen(bool enabled) {
  g_green_on = enabled;
  writeActiveLevel(GREEN_LED_PIN, GREEN_LED_ACTIVE_HIGH, enabled);
}

static void setRed(bool enabled) {
  g_red_on = enabled;
  writeActiveLevel(RED_LED_PIN, RED_LED_ACTIVE_HIGH, enabled);
}

static void setBuzzer(bool enabled) {
  g_buzzer_on = enabled;
  if (enabled) {
    startBuzzerTone();
  } else {
    stopBuzzerTone();
  }
}

static void allOutputsOff() {
  setGreen(false);
  setRed(false);
  setBuzzer(false);
}

/*
 * Single entry point for every state preset and every self-test step.
 *
 * Enforces the invariant that green (safe) and red (alarm) are never lit
 * together. They mean opposite things, so lighting both would be ambiguous to
 * an operator and would misrepresent the system state. No caller in this sketch
 * requests both; the guard exists so a future edit cannot silently violate it.
 */
static void applyOutputs(bool green, bool red, bool buzzer) {
  if (green && red) {
    Serial.println(F("FAULT: green and red requested together; red forced OFF."));
    red = false;
  }
  setGreen(green);
  setRed(red);
  setBuzzer(buzzer);
}

/* ===========================================================================
 * Non-blocking SELF_TEST sequencer
 *
 * The sequence is a flat table: each entry states the complete desired output
 * combination, so a step can never leave a stale output on and reordering is a
 * one-line change. It advances only when SELF_TEST_STEP_MS has elapsed, and
 * loop() returns between steps, so Serial stays responsive throughout.
 * =========================================================================== */

struct SelfTestStep {
  const char *label;
  bool green;
  bool red;
  bool buzzer;
};

static const SelfTestStep SELF_TEST_SEQUENCE[] = {
  { "GREEN ON    - expect GREEN lit, red dark, silent",       true,  false, false },
  { "GREEN OFF   - expect all dark, silent",                  false, false, false },
  { "RED ON      - expect RED lit, green dark, silent",       false, true,  false },
  { "RED OFF     - expect all dark, silent",                  false, false, false },
  { "BUZZER ON   - expect tone, both LEDs dark",              false, false, true  },
  { "BUZZER OFF  - expect silence, both LEDs dark",           false, false, false },
  { "RED+BUZZER  - expect RED lit AND tone together",         false, true,  true  },
  { "ALL OFF     - expect all dark, silent (sequence ends)",  false, false, false },
};

static const uint8_t SELF_TEST_STEPS =
    sizeof(SELF_TEST_SEQUENCE) / sizeof(SELF_TEST_SEQUENCE[0]);

/* 0xFF = not running. */
static const uint8_t SELF_TEST_IDLE = 0xFF;
static uint8_t g_self_test_index = SELF_TEST_IDLE;
static uint32_t g_self_test_mark = 0;

static void selfTestAnnounce(uint8_t index) {
  Serial.print(F("STEP "));
  Serial.print((uint16_t)index + 1);
  Serial.print(F("/"));
  Serial.print(SELF_TEST_STEPS);
  Serial.print(F(": "));
  Serial.println(SELF_TEST_SEQUENCE[index].label);
}

static void startSelfTest() {
  allOutputsOff();
  g_self_test_index = 0;
  g_self_test_mark = millis();
  Serial.println(F("SELF_TEST started. Watch and listen; report anything unexpected."));
  Serial.println(F("Type STOP_TEST to abort immediately."));
  selfTestAnnounce(0);
  const SelfTestStep &s = SELF_TEST_SEQUENCE[0];
  applyOutputs(s.green, s.red, s.buzzer);
}

/*
 * Immediate cancellation. STOP_TEST, ALL_OFF and any manual output command route
 * through here, so a running sequence can never fight a human command.
 */
static void stopSelfTest(const __FlashStringHelper *why) {
  if (g_self_test_index == SELF_TEST_IDLE) return;
  g_self_test_index = SELF_TEST_IDLE;
  allOutputsOff();
  Serial.print(F("SELF_TEST stopped ("));
  Serial.print(why);
  Serial.println(F("). All outputs OFF."));
}

/* Advance the sequence when the dwell time has elapsed. Never blocks. */
static void updateSelfTest(unsigned long nowMs) {
  if (g_self_test_index == SELF_TEST_IDLE) return;
  if ((uint32_t)(nowMs - g_self_test_mark) < SELF_TEST_STEP_MS) return;

  g_self_test_mark = nowMs;
  const uint8_t next = (uint8_t)(g_self_test_index + 1);

  if (next >= SELF_TEST_STEPS) {
    g_self_test_index = SELF_TEST_IDLE;
    allOutputsOff();
    Serial.println(F("SELF_TEST complete. All outputs OFF."));
    Serial.println(F("If any step was inverted, set the matching *_ACTIVE_HIGH to 0 in config.h."));
    return;
  }

  g_self_test_index = next;
  selfTestAnnounce(next);
  const SelfTestStep &s = SELF_TEST_SEQUENCE[next];
  applyOutputs(s.green, s.red, s.buzzer);
}

/* ===========================================================================
 * Status and help
 * =========================================================================== */

static const char *yellowModeName(IncidentCore::YellowMode mode) {
  switch (mode) {
    case IncidentCore::YellowMode::Off:       return "OFF";
    case IncidentCore::YellowMode::Steady:    return "STEADY";
    case IncidentCore::YellowMode::SlowBlink: return "SLOW_BLINK";
    case IncidentCore::YellowMode::FastBlink: return "FAST_BLINK";
  }
  return "UNKNOWN";
}

static void printStatus() {
  Serial.println(F("---------------- STATUS ----------------"));
  Serial.println(F("firmware        : Phase 4A incident lifecycle + bring-up presets"));
  Serial.print(F("config          : "));
  Serial.println(USING_LOCAL_CONFIG ? F("local config.h in use") : F("defaults from config.example.h"));
  Serial.println(F("output          : pin      polarity      state"));
  Serial.print(F("  green         : GPIO"));
  Serial.print(GREEN_LED_PIN);
  Serial.print(F("    "));
  Serial.print(GREEN_LED_ACTIVE_HIGH ? F("active-HIGH ") : F("active-LOW  "));
  Serial.print(F("  "));
  Serial.println(g_green_on ? F("ON") : F("OFF"));
  Serial.print(F("  red           : GPIO"));
  Serial.print(RED_LED_PIN);
  Serial.print(F("    "));
  Serial.print(RED_LED_ACTIVE_HIGH ? F("active-HIGH ") : F("active-LOW  "));
  Serial.print(F("  "));
  Serial.println(g_red_on ? F("ON") : F("OFF"));
  Serial.print(F("  buzzer        : GPIO"));
  Serial.print(BUZZER_PIN);
  Serial.print(F("    "));
  Serial.print(BUZZER_ACTIVE_HIGH ? F("active-HIGH ") : F("active-LOW  "));
  Serial.print(F("  "));
  Serial.println(g_buzzer_on ? F("ON") : F("OFF"));
  Serial.print(F("buzzer mode     : "));
  Serial.print(BUZZER_IS_PASSIVE ? F("PASSIVE (PWM tone)") : F("ACTIVE (DC level)"));
  Serial.print(F("  frequency "));
  Serial.print(BUZZER_FREQUENCY_HZ);
  Serial.println(F(" Hz"));
  Serial.print(F("self test       : "));
  if (g_self_test_index == SELF_TEST_IDLE) {
    Serial.println(F("inactive"));
  } else {
    Serial.print(F("ACTIVE at step "));
    Serial.print((uint16_t)g_self_test_index + 1);
    Serial.print(F("/"));
    Serial.println(SELF_TEST_STEPS);
  }
  Serial.print(F("lifecycle       : "));
  Serial.print(fwStateName(g_core.state()));
  Serial.print(F("  incident="));
  Serial.print(g_core.incident_id());
  Serial.print(F("  acked="));
  Serial.print(g_core.acknowledged() ? F("yes") : F("no"));
  Serial.print(F("  link="));
  Serial.println(g_core.link_alive(millis()) ? F("alive") : F("stale"));
  Serial.print(F("yellow          : "));
  if (!g_core.yellowEnabled()) {
    Serial.println(F("DISABLED (input not wired)"));
  } else {
    Serial.println(yellowModeName(g_core.yellowMode()));
  }
  Serial.print(F("uptime_ms       : "));
  Serial.println(millis());
  Serial.println(F("----------------------------------------"));
}

static void printHelp() {
  Serial.println(F("--------------------------------------------------------------"));
  Serial.println(F("Commands (Serial Monitor, line ending Newline or CRLF):"));
  Serial.println(F("  SAFE        output preset: green on,  red off, buzzer off"));
  Serial.println(F("  ALARM       output preset: green off, red on,  buzzer on"));
  Serial.println(F("  SILENCE     output preset: green off, red on,  buzzer off"));
  Serial.println(F("  GREEN_ON    green on,  red off (mutually exclusive)"));
  Serial.println(F("  GREEN_OFF   green off, red unchanged"));
  Serial.println(F("  RED_ON      red on,    green off (mutually exclusive)"));
  Serial.println(F("  RED_OFF     red off,   green unchanged"));
  Serial.println(F("  BUZZER_ON   buzzer on, LEDs unchanged"));
  Serial.println(F("  BUZZER_OFF  buzzer off, LEDs unchanged"));
  Serial.println(F("  ALL_OFF     every output off (refused during an incident)"));
  Serial.println(F("  SELF_TEST   run the 8-step non-blocking sequence"));
  Serial.println(F("  STOP_TEST   abort the sequence, all outputs off"));
  Serial.println(F("  STATUS      pins, polarity, buzzer mode, state, uptime"));
  Serial.println(F("  HELP        this list"));
  Serial.println(F("--------------------------------------------------------------"));
  Serial.println(F("Incident lifecycle (driven by the laptop):"));
  Serial.println(F("  HEARTBEAT              proves the link is alive"));
  Serial.println(F("  NO_INCIDENT <id>       clear: no incident is in progress"));
  Serial.println(F("  INCIDENT <id>          violate: latch the alarm"));
  Serial.println(F("  ACKNOWLEDGE <id>       operator has seen the alarm"));
  Serial.println(F("  RESET_READY <id>       laptop asserts recovery is proven"));
  Serial.println(F("  RESET                  clear, only after RESET_READY <id>"));
  Serial.println(F("Yellow is advisory and DISABLED on this build (input not wired)."));
  Serial.println(F("--------------------------------------------------------------"));
}

/* ===========================================================================
 * Command handling
 *
 * Two kinds of command arrive on the same line protocol:
 *
 *   - output presets (SAFE / ALARM / GREEN_ON / ...), which write the pins
 *     directly and exist for bring-up and for the SELF_TEST path;
 *   - incident-lifecycle commands (NO_INCIDENT / INCIDENT / ACKNOWLEDGE /
 *     RESET_READY / RESET / HEARTBEAT), which are delegated to IncidentCore.
 *
 * Only the core may clear a latched alarm, and it only does so through the
 * ACKNOWLEDGE -> RESET_READY -> RESET handshake. A preset can force the pins but
 * deliberately reports that it overrode the lifecycle, so an operator watching
 * the log can tell the difference between "the system cleared this" and "a human
 * typed a preset".
 * =========================================================================== */

/* Output presets. Kept identical to the verified Phase 2A behaviour so the
 * bring-up procedure in docs/hardware_bringup.md still applies unchanged. */
struct PresetEntry {
  const char *name;
  const char *reply;
  void (*handler)();
};

static void cmdSafe()     { applyOutputs(true,  false, false); }
static void cmdAlarm()    { applyOutputs(false, true,  true);  }
static void cmdSilence()  { applyOutputs(false, true,  false); }
static void cmdGreenOn()  { applyOutputs(true,  false, g_buzzer_on); }
static void cmdGreenOff() { setGreen(false); }
static void cmdRedOn()    { applyOutputs(false, true,  g_buzzer_on); }
static void cmdRedOff()   { setRed(false); }
static void cmdBuzzerOn() { setBuzzer(true);  }
static void cmdBuzzerOff(){ setBuzzer(false); }
static void cmdAllOff()   { allOutputsOff();  }

static const PresetEntry PRESETS[] = {
  { "SAFE",       "OK SAFE",       cmdSafe      },
  { "ALARM",      "OK ALARM",      cmdAlarm     },
  { "SILENCE",    "OK SILENCE",    cmdSilence   },
  { "GREEN_ON",   "OK GREEN_ON",   cmdGreenOn   },
  { "GREEN_OFF",  "OK GREEN_OFF",  cmdGreenOff  },
  { "RED_ON",     "OK RED_ON",     cmdRedOn     },
  { "RED_OFF",    "OK RED_OFF",    cmdRedOff    },
  { "BUZZER_ON",  "OK BUZZER_ON",  cmdBuzzerOn  },
  { "BUZZER_OFF", "OK BUZZER_OFF", cmdBuzzerOff },
  { "ALL_OFF",    "OK ALL_OFF",    cmdAllOff    },
};

static const uint8_t PRESET_COUNT = sizeof(PRESETS) / sizeof(PRESETS[0]);
static const uint8_t PRESET_NOT_FOUND = 0xFF;

static uint8_t findPreset(const String &cmd) {
  for (uint8_t i = 0; i < PRESET_COUNT; i++) {
    if (cmd.equals(PRESETS[i].name)) return i;
  }
  return PRESET_NOT_FOUND;
}

static const char *fwStateName(FwState state) {
  switch (state) {
    case FwState::Safe:         return "SAFE";
    case FwState::AlarmLatched: return "ALARM_LATCHED";
    case FwState::Silenced:     return "SILENCED";
    case FwState::Acknowledged: return "ACKNOWLEDGED";
    case FwState::ReadyToReset: return "READY_TO_RESET";
    case FwState::Fault:        return "FAULT";
  }
  return "UNKNOWN";
}

static void handleCommand(String raw) {
  raw.trim();
  raw.toUpperCase();
  if (raw.length() == 0) return;

  /* Read-only commands never disturb a running self-test. */
  if (raw.equals("STATUS")) { printStatus(); return; }
  if (raw.equals("HELP") || raw.equals("?")) { printHelp(); return; }

  if (raw.equals("SELF_TEST")) { startSelfTest(); return; }

  if (raw.equals("STOP_TEST")) {
    if (g_self_test_index == SELF_TEST_IDLE) {
      allOutputsOff();
      Serial.println(F("OK STOP_TEST (no sequence was running; all outputs OFF)"));
    } else {
      stopSelfTest(F("STOP_TEST"));
      Serial.println(F("OK STOP_TEST"));
    }
    return;
  }

  /* ALL_OFF during an incident would extinguish a live safety indication, so it
   * is refused by the core rather than silently obeyed. */
  if (raw.equals("ALL_OFF") && g_core.incident_active()) {
    Serial.println(reply::kErrAllOffIncidentActive);
    return;
  }

  const uint8_t preset = findPreset(raw);
  if (preset != PRESET_NOT_FOUND) {
    stopSelfTest(F("manual command"));
    PRESETS[preset].handler();

    Serial.println(PRESETS[preset].reply);

    /* A preset is a diagnostic override, not a lifecycle transition. Say so, so
     * the log cannot be mistaken for an accepted RESET. */
    if (g_core.incident_active()) {
      Serial.print(F("WARN PRESET_OVERRIDE state="));
      Serial.println(fwStateName(g_core.state()));
    }
    return;
  }

  const ParsedCommand parsed = parseFwCommand(raw.c_str(), raw.length());

  if (parsed.command == FwCommand::Unknown) {
    Serial.print(F("ERROR unknown command: "));
    Serial.println(raw);
    Serial.println(F("Type HELP for the command list. Outputs left unchanged."));
    return;
  }

  /* Numeric arguments are mandatory for the id-carrying commands and must parse
   * cleanly; "INCIDENT abc" must not silently latch incident 0. */
  const bool needs_id =
      parsed.command == FwCommand::Alarm ||
      parsed.command == FwCommand::Silence ||
      parsed.command == FwCommand::Acknowledge ||
      parsed.command == FwCommand::ResetReady ||
      parsed.command == FwCommand::Safe;

  if (needs_id && !parsed.argument_valid) {
    Serial.print(F("ERR BAD_ARGUMENT "));
    Serial.println(fwCommandName(parsed.command));
    return;
  }

  stopSelfTest(F("lifecycle command"));

  CoreOutputs outputs;
  outputs.green = g_green_on;
  outputs.red = g_red_on;
  outputs.buzzer = g_buzzer_on;

  const char *reply_text = g_core.handle(parsed, millis(), &outputs);

  if (reply_text == nullptr) {
    /* STATUS is formatted here because only the sketch can report real pins. */
    printStatus();
    return;
  }

  Serial.println(reply_text);

  /* Apply exactly what the core decided, not what we guessed. */
  applyOutputs(outputs.green, outputs.red, outputs.buzzer);
}

/* ===========================================================================
 * setup() / loop()
 * =========================================================================== */

static String g_line;
static bool g_skip_next_lf = false;

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);

  /* Native USB CDC boards enumerate after setup() starts; wait briefly so the
   * banner is not lost. Bounded, millis()-based, and it is a startup wait, not
   * a runtime delay. */
  const uint32_t wait_start = millis();
  while (!Serial && (uint32_t)(millis() - wait_start) < 2000) {
    /* spin */
  }

  /* Configure the three outputs and park them OFF before anything else, so no
   * output glitches on during boot. */
  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  allOutputsOff();

  Serial.println();
  Serial.println(F("=============================================================="));
  Serial.println(F(" ESP32-S3 EYE-SHIELD MONITOR - PHASE 2A HARDWARE BRING-UP"));
  Serial.println(F(" Working self-test firmware. Not the final product firmware:"));
  Serial.println(F(" no Blynk, no Wi-Fi, no YOLO, no JSON protocol yet."));
  Serial.println(F("=============================================================="));
  Serial.print(F("config          : "));
  Serial.println(USING_LOCAL_CONFIG ? F("local config.h in use") : F("defaults from config.example.h"));
  Serial.println(F("pin mapping     :"));
  Serial.print(F("  green LED     : GPIO"));
  Serial.println(GREEN_LED_PIN);
  Serial.print(F("  red LED       : GPIO"));
  Serial.println(RED_LED_PIN);
  Serial.print(F("  buzzer        : GPIO"));
  Serial.println(BUZZER_PIN);
  Serial.print(F("polarity        : green "));
  Serial.print(GREEN_LED_ACTIVE_HIGH ? F("active-HIGH") : F("active-LOW"));
  Serial.print(F(", red "));
  Serial.print(RED_LED_ACTIVE_HIGH ? F("active-HIGH") : F("active-LOW"));
  Serial.print(F(", buzzer "));
  Serial.println(BUZZER_ACTIVE_HIGH ? F("active-HIGH") : F("active-LOW"));
  Serial.print(F("buzzer mode     : "));
  Serial.print(BUZZER_IS_PASSIVE ? F("PASSIVE (PWM tone)") : F("ACTIVE (DC level)"));
  Serial.print(F(" @ "));
  Serial.print(BUZZER_FREQUENCY_HZ);
  Serial.println(F(" Hz"));
  Serial.print(F("serial baud     : "));
  Serial.println(SERIAL_BAUD_RATE);
  Serial.println(F("--------------------------------------------------------------"));
  Serial.println(F("!! UNVERIFIED HARDWARE !!"));
  Serial.println(F("Every pin, polarity and buzzer assumption above is a default."));
  Serial.println(F("Nothing is confirmed until you run the tests in"));
  Serial.println(F("docs/hardware_bringup.md and report what you observed."));
  Serial.println(F("All outputs are currently OFF."));
  Serial.println(F("--------------------------------------------------------------"));
  printHelp();
  Serial.println(F("Ready. Type a command and press Enter."));

  /* Yellow is advisory and its module input is recorded as NOT CONNECTED in
   * docs/hardware_bringup.md. It is compiled out unless YELLOW_LED_ENABLED is
   * set, which must only happen after the pin is wired and measured. */
#if YELLOW_LED_ENABLED
  pinMode(YELLOW_LED_PIN, OUTPUT);
  writeActiveLevel(YELLOW_LED_PIN, YELLOW_LED_ACTIVE_HIGH, false);
  g_core.setYellowEnabled(true);
  Serial.print(F("yellow          : GPIO"));
  Serial.println(YELLOW_LED_PIN);
#else
  g_core.setYellowEnabled(false);
  Serial.println(F("yellow          : DISABLED (input not wired)"));
#endif

  Serial.print(F("heartbeat tmo   : "));
  Serial.print(HEARTBEAT_TIMEOUT_MS);
  Serial.println(F(" ms"));

  g_line.reserve(64);
}

void loop() {
  /* Non-blocking serial read: accumulate a line, act on the terminator.
   * Both LF and CRLF are accepted. A lone CR also terminates, and the LF that
   * may follow it is swallowed, so CRLF cannot fire the command twice. */
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();

    if (c == '\r') {
      handleCommand(g_line);
      g_line = "";
      g_skip_next_lf = true;
      continue;
    }

    if (c == '\n') {
      if (g_skip_next_lf) {
        g_skip_next_lf = false;
        continue;
      }
      handleCommand(g_line);
      g_line = "";
      continue;
    }

    g_skip_next_lf = false;
    if (g_line.length() < 63) {
      g_line += c;
    } else {
      /* Overflow guard: discard rather than grow without bound. */
      g_line = "";
      Serial.println(F("ERROR line too long (max 63 chars). Discarded."));
    }
  }

  /* Non-blocking self-test advance. */
  updateSelfTest(millis());

#if YELLOW_LED_ENABLED
  /* Blinking is computed from millis() rather than delay()'d, so the serial
   * poll above never stalls behind a cosmetic light. */
  writeActiveLevel(YELLOW_LED_PIN, YELLOW_LED_ACTIVE_HIGH,
                   g_core.yellowLevel(millis()));
#endif
}
