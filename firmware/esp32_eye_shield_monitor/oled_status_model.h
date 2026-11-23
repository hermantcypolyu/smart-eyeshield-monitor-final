/*
 * oled_status_model.h — what the OLED should say, as pure data
 * ===========================================================================
 * Pure logic. No Arduino, no Adafruit, no Wire, no millis(), no pins. This file
 * answers exactly one question: "given the incident core's state and the
 * outputs it asked for, what belongs on the screen?"
 *
 * That question is separated from the drawing code on purpose. The model
 * compiles for the ESP32 AND for the host, so tests/test_oled_status_model.py
 * can assert that every state renders the right headline and the right
 * indicator lamps without a panel attached. The bytes on the glass are then a
 * formatting problem, not a safety-behaviour problem.
 *
 * ADVISORY ONLY
 * -------------
 * Nothing here drives a pin or changes a transition. The model READS the
 * incident core. It never writes to it, and there is no code path by which a
 * display bug can silence an alarm.
 * ===========================================================================
 */

#ifndef OLED_STATUS_MODEL_H
#define OLED_STATUS_MODEL_H

/* Freestanding headers only: has to compile with the ESP32 toolchain and the
 * host compiler that the tests use. */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "incident_core.h"

namespace eye_shield {

/* --------------------------------------------------------------------------
 * Severity bands
 *
 * The renderer needs to know how loudly to draw, not just what the words are.
 * ALERT gets an inverted (white-on-black) banner so an operator glancing at the
 * panel from across the lab sees a violation before reading any text.
 * ----------------------------------------------------------------------- */

enum class OledSeverity : uint8_t {
  Nominal = 0, /* green, everything fine */
  Info,        /* transitional or waiting, no alarm */
  Warning,     /* alarm open but silenced/acknowledged */
  Alert,       /* alarm open and sounding */
  Fault        /* outputs parked, display says so */
};

inline const char *oledSeverityName(OledSeverity severity) {
  switch (severity) {
    case OledSeverity::Nominal: return "NOMINAL";
    case OledSeverity::Info:    return "INFO";
    case OledSeverity::Warning: return "WARNING";
    case OledSeverity::Alert:   return "ALERT";
    case OledSeverity::Fault:   return "FAULT";
  }
  return "UNKNOWN";
}

/* --------------------------------------------------------------------------
 * The snapshot the renderer draws
 *
 * Fixed-size buffers because this runs on a microcontroller: no String, no
 * heap, no allocation in the render path. A frame costs one stack object and
 * one memcpy, so the render can never fail for lack of memory.
 * ----------------------------------------------------------------------- */

/* Long enough for "SAFE CLEAR PENDING" and a full refusal code, with headroom.
 * tests/test_oled_status_model.py asserts that nothing here is ever truncated. */
static const size_t kOledHeadlineChars = 24;
static const size_t kOledDetailChars = 32;

struct OledStatusSnapshot {
  char headline[kOledHeadlineChars];
  char detail[kOledDetailChars];

  OledSeverity severity;

  /* What the LAMPS are doing, read back from the outputs the core asked for -
   * not from a second guess here. These drive the three indicator glyphs so the
   * panel can never claim a lamp is lit when the pin is low. */
  bool green_on;
  bool red_on;
  bool buzzer_on;

  /* The firmware's own lifecycle word, for operators who have been trained on
   * it, and the open episode number (0 = none). */
  const char *state_name;
  int incident_id;
  bool acknowledged;

  /* True when the link to the laptop is stale. Advisory, but worth showing:
   * a stale link is why a RESET would be refused. */
  bool link_stale;
};

/* Copy a string into a fixed buffer, always NUL-terminating. Guarantees the
 * tests' "never truncated" claim by clamping rather than overflowing. */
inline void oledCopy(char *dest, size_t capacity, const char *source) {
  if (dest == nullptr || capacity == 0) return;
  if (source == nullptr) {
    dest[0] = '\0';
    return;
  }
  size_t i = 0;
  for (; i + 1 < capacity && source[i] != '\0'; ++i) {
    dest[i] = source[i];
  }
  dest[i] = '\0';
}

/* --------------------------------------------------------------------------
 * Derivation
 *
 * The headline is the operator's word for the situation; the detail is the one
 * fact that explains it. Both are chosen from the CORE's state, so the display
 * cannot disagree with the latched lifecycle.
 *
 * Note the deliberate absence of any "clear the alarm" affordance: the panel is
 * output-only. Silencing, acknowledging and resetting all still require a
 * serial command, so a passer-by cannot tap a screen to suppress a real event.
 * ----------------------------------------------------------------------- */

inline OledStatusSnapshot buildOledStatus(const IncidentCore &core,
                                          const CoreOutputs &outputs,
                                          bool link_alive) {
  OledStatusSnapshot snapshot = {};

  snapshot.green_on = outputs.green;
  snapshot.red_on = outputs.red;
  snapshot.buzzer_on = outputs.buzzer;
  snapshot.state_name = IncidentCore::stateName(core.state());
  snapshot.incident_id = core.incident_id();
  snapshot.acknowledged = core.acknowledged();
  snapshot.link_stale = !link_alive;

  switch (core.state()) {
    case FwState::Safe:
      /* Nothing open. The green lamp carries the meaning; the panel repeats it
       * in words for someone too far away to see the lamp. */
      oledCopy(snapshot.headline, sizeof(snapshot.headline), "SAFE");
      oledCopy(snapshot.detail, sizeof(snapshot.detail), "no violation");
      snapshot.severity = OledSeverity::Nominal;
      break;

    case FwState::AlarmLatched:
      /* The alarming case: red AND buzzer. Severity Alert, so the renderer
       * inverts the banner. */
      oledCopy(snapshot.headline, sizeof(snapshot.headline), "ALERT");
      snprintf(snapshot.detail, sizeof(snapshot.detail), "INCIDENT %d",
               snapshot.incident_id);
      snapshot.severity = OledSeverity::Alert;
      break;

    case FwState::Silenced:
      /* Still an open incident - red stays on - but a human muted the buzzer.
       * Warning, not Alert: the hazard has not gone away. */
      oledCopy(snapshot.headline, sizeof(snapshot.headline), "WARNING");
      snprintf(snapshot.detail, sizeof(snapshot.detail), "SILENCED #%d",
               snapshot.incident_id);
      snapshot.severity = OledSeverity::Warning;
      break;

    case FwState::Acknowledged:
      oledCopy(snapshot.headline, sizeof(snapshot.headline), "WARNING");
      snprintf(snapshot.detail, sizeof(snapshot.detail), "ACKED #%d",
               snapshot.incident_id);
      snapshot.severity = OledSeverity::Warning;
      break;

    case FwState::ReadyToReset:
      /* The laptop has proven recovery and the reset gate is armed, but the
       * incident is still open until RESET arrives. Still Warning. */
      oledCopy(snapshot.headline, sizeof(snapshot.headline), "WARNING");
      snprintf(snapshot.detail, sizeof(snapshot.detail), "RESET READY #%d",
               snapshot.incident_id);
      snapshot.severity = OledSeverity::Warning;
      break;

    case FwState::Fault:
      oledCopy(snapshot.headline, sizeof(snapshot.headline), "FAULT");
      oledCopy(snapshot.detail, sizeof(snapshot.detail), "outputs parked");
      snapshot.severity = OledSeverity::Fault;
      break;
  }

  /* A stale link is the reason a RESET would be refused, so it outranks the
   * per-state detail but never changes the headline: it is an annotation, not a
   * different situation. */
  if (snapshot.link_stale && core.state() == FwState::Safe) {
    oledCopy(snapshot.detail, sizeof(snapshot.detail), "no laptop link");
  }

  return snapshot;
}

/* --------------------------------------------------------------------------
 * Change detection
 *
 * The renderer skips the ~1 KB I2C push when the snapshot is unchanged, which
 * is the common case: in the safe state nothing moves for minutes at a time.
 * Comparing every field that is actually drawn keeps this honest - a stale
 * comparison would freeze the screen at an old state.
 * ----------------------------------------------------------------------- */

/* Null-safe strcmp: state_name comes from IncidentCore::stateName(), which
 * never returns nullptr, but a default-constructed snapshot has a null pointer
 * and comparing against it must not fault. */
inline bool oledStreq(const char *a, const char *b) {
  if (a == nullptr) return b == nullptr || b[0] == '\0';
  if (b == nullptr) return a[0] == '\0';
  return strcmp(a, b) == 0;
}

inline bool oledSnapshotEquals(const OledStatusSnapshot &a,
                               const OledStatusSnapshot &b) {
  return a.severity == b.severity && a.green_on == b.green_on &&
         a.red_on == b.red_on && a.buzzer_on == b.buzzer_on &&
         a.incident_id == b.incident_id && a.acknowledged == b.acknowledged &&
         a.link_stale == b.link_stale &&
         strcmp(a.headline, b.headline) == 0 &&
         strcmp(a.detail, b.detail) == 0 &&
         oledStreq(a.state_name, b.state_name);
}

}  // namespace eye_shield

#endif /* OLED_STATUS_MODEL_H */
