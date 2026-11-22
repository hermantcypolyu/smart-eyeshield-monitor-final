/*
 * incident_core.h — text-protocol incident lifecycle core
 * ===========================================================================
 * Pure logic. No Arduino, no pins, no Serial, no millis(). Everything here is
 * plain C++ that compiles both for the ESP32-S3 and for the host, which is what
 * makes the firmware's command parsing and reset gating testable without a
 * board. tests/test_incident_core.py compiles this header with clang++ and
 * drives it directly, so the tested code IS the shipped code.
 *
 * Why the incident lifecycle lives in the firmware at all
 * -------------------------------------------------------
 * If the laptop alone decided when the alarm may be cleared, a crashed or
 * buggy laptop process could clear an active incident. The firmware therefore
 * owns the latch and refuses every shortcut:
 *
 *   - ALARM always latches. Nothing clears it implicitly.
 *   - SAFE is rejected while an incident is active. It cannot bypass the latch.
 *   - ACKNOWLEDGE records review but never clears the red lamp.
 *   - RESET is rejected unless the incident is acknowledged, a matching
 *     RESET_READY token was received for THAT incident, and the heartbeat is
 *     fresh.
 *   - ALL_OFF is diagnostic and is refused while an incident is active, so it
 *     can never stand in for a valid reset.
 *
 * Interim handshake, and why JSON is still required
 * -------------------------------------------------
 * The firmware cannot see the camera, so it cannot itself verify "goggles were
 * continuously present for the clearance period". The text protocol therefore
 * splits the proof: the laptop sends RESET_READY <incident_id> only after its
 * own state machine actually reached SAFE_RECOVERED, and the firmware gates
 * RESET on having received that token for the currently open incident.
 *
 * That is a real improvement over trusting a bare RESET, because a dropped,
 * stale or mis-sequenced RESET now fails safe. It is still only as trustworthy
 * as the laptop process, because plain text carries no structure: no incident
 * id in the RESET itself, no sequencing, no integrity check, and no way to
 * distinguish a stale token from a fresh one except by the id compare. The
 * later newline-delimited JSON protocol fixes this by putting incident_id,
 * an explicit intent, a monotonic sequence number and a heartbeat counter in
 * one structured message that the firmware can validate atomically.
 * ===========================================================================
 */

#ifndef INCIDENT_CORE_H
#define INCIDENT_CORE_H

/* Freestanding headers only: this has to compile both for the ESP32 toolchain
 * and for the host compiler that the tests use, so it stays off <cstdint> and
 * friends. */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace eye_shield {

/* --------------------------------------------------------------------------
 * Firmware incident states (text-protocol stage)
 *
 * These are the firmware's own view. They are deliberately smaller than the
 * laptop's operator states: the firmware has no camera, so it has no notion of
 * WAITING_FOR_TARGET or SAFE_CLEAR_PENDING. It only knows what it has been
 * told and what it has latched.
 * ----------------------------------------------------------------------- */

enum class FwState : uint8_t {
  Safe = 0,           /* green, no incident */
  AlarmLatched,       /* red + buzzer, incident open, not acknowledged */
  Silenced,           /* red only, incident open, not acknowledged */
  Acknowledged,       /* red only, incident open, acknowledged */
  ReadyToReset,       /* red only, acknowledged, matching RESET_READY seen */
  Fault               /* outputs parked, refuses normal operation */
};

/* --------------------------------------------------------------------------
 * Commands
 * ----------------------------------------------------------------------- */

enum class FwCommand : uint8_t {
  None = 0,
  Incidents,
  NoIncident,
  AckIncident,
  SilenceIncident,
  Safe,
  Alarm,
  Silence,
  Acknowledge,
  Reset,
  ResetReady,
  Heartbeat,
  Status,
  AllOff,
  YellowOff,
  YellowSteady,
  YellowSlowBlink,
  YellowFastBlink,
  Unknown
};

/* Recognised command names, in FwCommand order starting at Safe. */
inline const char *fwCommandName(FwCommand command) {
  switch (command) {
    case FwCommand::Incidents: return "INCIDENT";
    case FwCommand::NoIncident: return "NO_INCIDENT";
    case FwCommand::AckIncident: return "ACKNOWLEDGE";
    case FwCommand::SilenceIncident: return "SILENCE";
    case FwCommand::Safe: return "SAFE";
    case FwCommand::Alarm: return "ALARM";
    case FwCommand::Silence: return "SILENCE";
    case FwCommand::Acknowledge: return "ACKNOWLEDGE";
    case FwCommand::Reset: return "RESET";
    case FwCommand::ResetReady: return "RESET_READY";
    case FwCommand::Heartbeat: return "HEARTBEAT";
    case FwCommand::Status: return "STATUS";
    case FwCommand::AllOff: return "ALL_OFF";
    case FwCommand::YellowOff: return "YELLOW_OFF";
    case FwCommand::YellowSteady: return "YELLOW_STEADY";
    case FwCommand::YellowSlowBlink: return "YELLOW_SLOW_BLINK";
    case FwCommand::YellowFastBlink: return "YELLOW_FAST_BLINK";
    default: return "";
  }
}

/* --------------------------------------------------------------------------
 * Parsing
 *
 * Case-insensitive, whitespace-trimmed, and explicit about an optional trailing
 * argument so "RESET_READY 3" parses. Returns FwCommand::Unknown for anything
 * unrecognised, which lets the caller answer with a single ERR line instead of
 * silently ignoring input.
 * ----------------------------------------------------------------------- */

struct ParsedCommand {
  FwCommand command = FwCommand::Unknown;
  int argument = -1;   /* -1 = absent; used by RESET_READY <id> */
  bool argument_valid = true;
};

inline ParsedCommand parseFwCommand(const char *raw, size_t length) {
  ParsedCommand parsed;
  if (raw == nullptr) return parsed;

  /* Bounded copy: this runs on a microcontroller, so never trust the length. */
  char buffer[40];
  const size_t limit = sizeof(buffer) - 1;
  size_t used = 0;

  for (size_t i = 0; i < length && used < limit; ++i) {
    const char c = raw[i];
    if (c == '\r' || c == '\n' || c == '\0') break;
    buffer[used++] = c;
  }
  buffer[used] = '\0';

  /* Trim both ends. */
  size_t start = 0;
  while (start < used && (buffer[start] == ' ' || buffer[start] == '\t')) {
    ++start;
  }
  size_t end = used;
  while (end > start &&
         (buffer[end - 1] == ' ' || buffer[end - 1] == '\t')) {
    --end;
  }
  buffer[end] = '\0';

  if (start == end) {
    parsed.command = FwCommand::None;
    return parsed;
  }

  /* Split off an optional numeric argument. */
  char *token = buffer + start;
  char *space = strchr(token, ' ');
  int argument = -999;
  bool has_argument = false;

  if (space != nullptr) {
    *space = '\0';
    char *arg_text = space + 1;
    while (*arg_text == ' ') ++arg_text;

    has_argument = (*arg_text != '\0');
    argument = 0;
    bool digits_only = has_argument;

    for (char *p = arg_text; *p != '\0'; ++p) {
      if (*p < '0' || *p > '9') { digits_only = false; break; }
      argument = argument * 10 + (*p - '0');
      if (argument > 9999) { digits_only = false; break; }
    }
    if (!digits_only) parsed.argument_valid = false;
  }

  /* Uppercase in place so comparison is a plain strcmp. */
  for (char *p = token; *p != '\0'; ++p) {
    if (*p >= 'a' && *p <= 'z') *p = (char)(*p - 'a' + 'A');
  }

  if (strcmp(token, "SAFE") == 0) {
    parsed.command = FwCommand::Safe;
  } else if (strcmp(token, "ALARM") == 0) {
    parsed.command = FwCommand::Alarm;
  } else if (strcmp(token, "SILENCE") == 0) {
    /* "SILENCE <id>" is a lifecycle command; bare "SILENCE" stays as the
     * original bring-up form so existing harness scripts keep working. */
    parsed.command = has_argument ? FwCommand::SilenceIncident
                                  : FwCommand::Silence;
  } else if (strcmp(token, "INCIDENT") == 0) {
    parsed.command = FwCommand::Incidents;
  } else if (strcmp(token, "NO_INCIDENT") == 0) {
    parsed.command = FwCommand::NoIncident;
  } else if (strcmp(token, "ACKNOWLEDGE") == 0 ||
             strcmp(token, "ACK") == 0) {
    parsed.command = has_argument ? FwCommand::AckIncident
                                  : FwCommand::Acknowledge;
  } else if (strcmp(token, "RESET") == 0) {
    parsed.command = FwCommand::Reset;
  } else if (strcmp(token, "RESET_READY") == 0) {
    parsed.command = FwCommand::ResetReady;
  } else if (strcmp(token, "HEARTBEAT") == 0) {
    parsed.command = FwCommand::Heartbeat;
  } else if (strcmp(token, "STATUS") == 0) {
    parsed.command = FwCommand::Status;
  } else if (strcmp(token, "ALL_OFF") == 0) {
    parsed.command = FwCommand::AllOff;
  } else if (strcmp(token, "YELLOW_OFF") == 0) {
    parsed.command = FwCommand::YellowOff;
  } else if (strcmp(token, "YELLOW_STEADY") == 0) {
    parsed.command = FwCommand::YellowSteady;
  } else if (strcmp(token, "YELLOW_SLOW_BLINK") == 0) {
    parsed.command = FwCommand::YellowSlowBlink;
  } else if (strcmp(token, "YELLOW_FAST_BLINK") == 0) {
    parsed.command = FwCommand::YellowFastBlink;
  } else {
    parsed.command = FwCommand::Unknown;
    return parsed;
  }

  if (has_argument && parsed.argument_valid) parsed.argument = argument;

  return parsed;
}

/* --------------------------------------------------------------------------
 * Outputs
 * ----------------------------------------------------------------------- */

struct CoreOutputs {
  bool green = false;
  bool red = false;
  bool buzzer = false;
};

/* --------------------------------------------------------------------------
 * Reply strings — the single source of truth for the wire format.
 *
 * tests/test_incident_state_machine.py asserts against these exact strings, so
 * the laptop parser and the firmware cannot drift apart silently.
 * ----------------------------------------------------------------------- */

namespace reply {

inline constexpr const char *kOkSafe = "OK SAFE";
inline constexpr const char *kOkIncident = "OK INCIDENT";
inline constexpr const char *kOkNoIncident = "OK NO_INCIDENT";
inline constexpr const char *kOkAlarm = "OK ALARM_LATCHED";
inline constexpr const char *kOkSilenced = "OK SILENCED";
inline constexpr const char *kOkAcknowledged = "OK ACKNOWLEDGED";
inline constexpr const char *kOkResetReady = "OK RESET_READY";
inline constexpr const char *kOkResetAccepted = "OK RESET_ACCEPTED";
inline constexpr const char *kOkHeartbeat = "OK HEARTBEAT";
inline constexpr const char *kOkAllOff = "OK ALL_OFF";

inline constexpr const char *kErrSafeIncidentActive =
    "ERR SAFE_REJECTED INCIDENT_ACTIVE";
inline constexpr const char *kErrSilenceNoIncident =
    "ERR SILENCE_REJECTED NO_INCIDENT";
inline constexpr const char *kErrAckNoIncident =
    "ERR ACKNOWLEDGE_REJECTED NO_INCIDENT";
inline constexpr const char *kErrResetNoIncident =
    "ERR RESET_REJECTED NO_INCIDENT";
inline constexpr const char *kErrResetNotAcknowledged =
    "ERR RESET_REJECTED NOT_ACKNOWLEDGED";
inline constexpr const char *kErrResetNotSafeRecovered =
    "ERR RESET_REJECTED NOT_SAFE_RECOVERED";
inline constexpr const char *kErrResetLinkOrHealth =
    "ERR RESET_REJECTED LINK_OR_HEALTH_INVALID";
inline constexpr const char *kErrResetReadyNoIncident =
    "ERR RESET_READY_REJECTED NO_INCIDENT";
inline constexpr const char *kErrResetReadyNotAcknowledged =
    "ERR RESET_READY_REJECTED NOT_ACKNOWLEDGED";
inline constexpr const char *kErrAllOffIncidentActive =
    "ERR ALL_OFF_DIAGNOSTIC_ONLY INCIDENT_ACTIVE";
inline constexpr const char *kErrPresetIncidentActive =
    "ERR PRESET_DIAGNOSTIC_ONLY INCIDENT_ACTIVE";
inline constexpr const char *kOkYellowOff = "OK YELLOW_OFF";
inline constexpr const char *kOkYellowSteady = "OK YELLOW_STEADY";
inline constexpr const char *kOkYellowSlowBlink = "OK YELLOW_SLOW_BLINK";
inline constexpr const char *kOkYellowFastBlink = "OK YELLOW_FAST_BLINK";
inline constexpr const char *kErrYellowDisabled = "ERR YELLOW_DISABLED";
inline constexpr const char *kErrUnknownCommand = "ERR UNKNOWN_COMMAND";
inline constexpr const char *kErrIncidentBadArgument =
    "ERR INCIDENT_REJECTED BAD_ARGUMENT";
inline constexpr const char *kErrIncidentAlreadyOpen =
    "ERR INCIDENT_REJECTED ALREADY_OPEN";
inline constexpr const char *kErrNoIncidentNothingToClose =
    "ERR NO_INCIDENT_REJECTED NO_INCIDENT";
inline constexpr const char *kErrNoIncidentIdMismatch =
    "ERR NO_INCIDENT_REJECTED ID_MISMATCH";
inline constexpr const char *kErrNoIncidentStillOpen =
    "ERR NO_INCIDENT_REJECTED INCIDENT_STILL_OPEN";
inline constexpr const char *kErrIncidentIdMismatch =
    "ERR INCIDENT_ID_MISMATCH";

}  // namespace reply

/* --------------------------------------------------------------------------
 * IncidentCore
 *
 * One instance per board. Timing is injected as a millisecond counter so the
 * host tests can advance time deterministically without sleeping.
 * ----------------------------------------------------------------------- */

class IncidentCore {
 public:
  /* heartbeat_timeout_ms: a RESET is refused when the laptop has not been heard
   * from within this window, which is how "serial link is healthy" is proven on
   * the firmware side. 0 disables the check (host tests that do not exercise
   * the link). */
  explicit IncidentCore(uint32_t heartbeat_timeout_ms = 3000)
      : heartbeat_timeout_ms_(heartbeat_timeout_ms) {}

  /* ---- state inspection ------------------------------------------------ */

  FwState state() const { return state_; }
  int incident_id() const { return incident_id_; }
  bool incident_active() const { return incident_id_ > 0; }
  bool acknowledged() const { return acknowledged_; }
  uint32_t rearm_count() const { return rearm_count_; }

  /* True while the laptop has spoken recently enough to be trusted. Checked
   * before a RESET, so a crashed or unplugged laptop cannot clear the alarm. */
  bool link_alive(uint32_t now_ms) const { return linkHealthy(now_ms); }
  uint32_t rejected_reset_count() const { return rejected_reset_count_; }

  static const char *stateName(FwState state) {
    switch (state) {
      case FwState::Safe: return "SAFE";
      case FwState::AlarmLatched: return "ALARM_LATCHED";
      case FwState::Silenced: return "SILENCED";
      case FwState::Acknowledged: return "ACKNOWLEDGED";
      case FwState::ReadyToReset: return "READY_TO_RESET";
      case FwState::Fault: return "FAULT";
    }
    return "UNKNOWN";
  }

  /* Desired physical outputs for the current state, ignoring yellow. */
  CoreOutputs outputs() const {
    CoreOutputs out;
    switch (state_) {
      case FwState::Safe:
        out.green = true;
        break;
      case FwState::AlarmLatched:
        out.red = true;
        out.buzzer = true;
        break;
      case FwState::Silenced:
      case FwState::Acknowledged:
      case FwState::ReadyToReset:
        out.red = true;
        break;
      case FwState::Fault:
        break;
    }
    return out;
  }

  /* ---- link health ----------------------------------------------------- */

  void noteHeartbeat(uint32_t now_ms) {
    last_heartbeat_ms_ = now_ms;
    heartbeat_seen_ = true;
  }

  bool linkHealthy(uint32_t now_ms) const {
    if (heartbeat_timeout_ms_ == 0) return true;
    if (!heartbeat_seen_) return false;
    return (uint32_t)(now_ms - last_heartbeat_ms_) <= heartbeat_timeout_ms_;
  }

  /* Called by the sketch's loop(): a fault condition parks the outputs and is
   * cleared only by an explicit RESET of the fault, never by time alone. */
  void raiseFault() { state_ = FwState::Fault; }

  /* ---- command handling ------------------------------------------------ */

  /* Handles one parsed command. `outputs` is filled with the desired physical
   * output combination AFTER the command. Returns the reply text.
   *
   * Note that the reply is a pointer to a string literal, so it stays valid
   * forever and the caller never has to copy it. */
  const char *handle(const ParsedCommand &parsed, uint32_t now_ms,
                     CoreOutputs *outputs) {
    if (outputs != nullptr) *outputs = this->outputs();

    switch (parsed.command) {
      case FwCommand::None:
        return nullptr;   /* blank line: stay silent */

      case FwCommand::Heartbeat:
        noteHeartbeat(now_ms);
        return reply::kOkHeartbeat;

      case FwCommand::Unknown:
        return reply::kErrUnknownCommand;

      case FwCommand::Safe:
        /* The whole point of the latch: an ordinary SAFE must never clear an
         * active incident, however it arrived. */
        if (incident_active()) {
          return reply::kErrSafeIncidentActive;
        }
        state_ = FwState::Safe;
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkSafe;

      case FwCommand::Alarm: {
        /* One incident per latch. A repeated ALARM for the same episode is
         * ignored rather than creating a second incident id. */
        if (!incident_active()) {
          incident_id_ = next_incident_id_++;
          acknowledged_ = false;
          reset_ready_incident_ = 0;
          state_ = FwState::AlarmLatched;
        } else if (state_ == FwState::Safe) {
          state_ = FwState::AlarmLatched;
        } else if (state_ == FwState::Silenced ||
                   state_ == FwState::Acknowledged ||
                   state_ == FwState::ReadyToReset) {
          /* A renewed confirmed violation after recovery re-arms the audible
           * warning. Same incident id: this is the same episode, still open. */
          acknowledged_ = false;
          reset_ready_incident_ = 0;
          state_ = FwState::AlarmLatched;
          ++rearm_count_;
        }
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkAlarm;
      }

      /* -----------------------------------------------------------------
       * Production lifecycle vocabulary.
       *
       * The laptop speaks only these. They carry the incident id so a stale
       * or mismatched token can be refused instead of applied to the wrong
       * episode.
       * -------------------------------------------------------------- */

      case FwCommand::Incidents: {
        if (!parsed.argument_valid || parsed.argument <= 0) {
          return reply::kErrIncidentBadArgument;
        }
        if (incident_active() && incident_id_ != parsed.argument) {
          /* An incident is already latched under a different id. Applying
           * this one would silently adopt the wrong episode. */
          return reply::kErrIncidentAlreadyOpen;
        }
        if (!incident_active()) {
          incident_id_ = parsed.argument;
          if (next_incident_id_ <= parsed.argument) {
            next_incident_id_ = parsed.argument + 1;
          }
          acknowledged_ = false;
          reset_ready_incident_ = 0;
          state_ = FwState::AlarmLatched;
        } else if (state_ == FwState::Silenced ||
                   state_ == FwState::Acknowledged ||
                   state_ == FwState::ReadyToReset) {
          /* Renewed violation inside the same open episode: re-arm the
           * audible warning, keep the id, keep the episode open. */
          acknowledged_ = false;
          reset_ready_incident_ = 0;
          state_ = FwState::AlarmLatched;
          ++rearm_count_;
        }
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkIncident;
      }

      case FwCommand::NoIncident: {
        /* The laptop's clearance verdict for a known episode. This is NOT a
         * reset: it never clears red and never closes the incident. It exists
         * so the firmware can refuse a clearance that arrives while the id is
         * still wrong, and so the laptop has a real reply to correlate. */
        if (!incident_active()) return reply::kErrNoIncidentNothingToClose;
        if (parsed.argument_valid && parsed.argument > 0 &&
            parsed.argument != incident_id_) {
          return reply::kErrNoIncidentIdMismatch;
        }
        /* While latched the incident is still open, so a bare clearance may
         * not silence anything. Report it so the laptop can see the mismatch
         * between its own verdict and the board's latch. */
        if (!acknowledged_) return reply::kErrNoIncidentStillOpen;
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkNoIncident;
      }

      case FwCommand::SilenceIncident:
        if (!incident_active()) return reply::kErrSilenceNoIncident;
        if (parsed.argument_valid && parsed.argument > 0 &&
            parsed.argument != incident_id_) {
          return reply::kErrIncidentIdMismatch;
        }
        if (state_ == FwState::AlarmLatched) state_ = FwState::Silenced;
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkSilenced;

      case FwCommand::AckIncident:
        if (!incident_active()) return reply::kErrAckNoIncident;
        if (parsed.argument_valid && parsed.argument > 0 &&
            parsed.argument != incident_id_) {
          return reply::kErrIncidentIdMismatch;
        }
        acknowledged_ = true;
        if (state_ == FwState::AlarmLatched || state_ == FwState::Silenced ||
            state_ == FwState::ReadyToReset) {
          state_ = FwState::Acknowledged;
        }
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkAcknowledged;

      case FwCommand::Silence:
        if (!incident_active()) return reply::kErrSilenceNoIncident;
        if (state_ == FwState::AlarmLatched) state_ = FwState::Silenced;
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkSilenced;

      case FwCommand::Acknowledge:
        if (!incident_active()) return reply::kErrAckNoIncident;
        acknowledged_ = true;
        if (state_ == FwState::AlarmLatched || state_ == FwState::Silenced) {
          state_ = FwState::Acknowledged;
        } else if (state_ == FwState::ReadyToReset) {
          state_ = FwState::Acknowledged;
        }
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkAcknowledged;

      case FwCommand::ResetReady:
        if (!incident_active()) return reply::kErrResetReadyNoIncident;
        if (!acknowledged_) return reply::kErrResetReadyNotAcknowledged;
        if (parsed.argument_valid && parsed.argument > 0 &&
            parsed.argument != incident_id_) {
          /* Token for a different episode: stale, refuse. */
          return reply::kErrResetNotSafeRecovered;
        }
        reset_ready_incident_ = incident_id_;
        state_ = FwState::ReadyToReset;
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkResetReady;

      case FwCommand::Reset: {
        /* Ordered checks so the operator is told the first real blocker. */
        if (!incident_active()) return reply::kErrResetNoIncident;
        if (!acknowledged_) {
          ++rejected_reset_count_;
          return reply::kErrResetNotAcknowledged;
        }
        if (reset_ready_incident_ != incident_id_) {
          ++rejected_reset_count_;
          return reply::kErrResetNotSafeRecovered;
        }
        if (!linkHealthy(now_ms)) {
          ++rejected_reset_count_;
          return reply::kErrResetLinkOrHealth;
        }
        /* Accepted: close the incident and return to a clean SAFE. */
        incident_id_ = 0;
        acknowledged_ = false;
        reset_ready_incident_ = 0;
        yellow_mode_ = YellowMode::Off;
        state_ = FwState::Safe;
        if (outputs != nullptr) *outputs = this->outputs();
        return reply::kOkResetAccepted;
      }

      case FwCommand::AllOff:
        /* Diagnostic only. Refused while an incident is open so it can never
         * be used as a substitute for acknowledge + conditional reset. */
        if (incident_active()) return reply::kErrAllOffIncidentActive;
        state_ = FwState::Safe;
        if (outputs != nullptr) {
          *outputs = CoreOutputs{};
        }
        return reply::kOkAllOff;

      case FwCommand::Status:
        return nullptr;   /* handled by the sketch, which can format pins */

      case FwCommand::YellowOff:
        if (!yellow_enabled_) return reply::kErrYellowDisabled;
        yellow_mode_ = YellowMode::Off;
        return reply::kOkYellowOff;

      case FwCommand::YellowSteady:
        if (!yellow_enabled_) return reply::kErrYellowDisabled;
        yellow_mode_ = YellowMode::Steady;
        return reply::kOkYellowSteady;

      case FwCommand::YellowSlowBlink:
        if (!yellow_enabled_) return reply::kErrYellowDisabled;
        yellow_mode_ = YellowMode::SlowBlink;
        return reply::kOkYellowSlowBlink;

      case FwCommand::YellowFastBlink:
        if (!yellow_enabled_) return reply::kErrYellowDisabled;
        yellow_mode_ = YellowMode::FastBlink;
        return reply::kOkYellowFastBlink;
    }

    return reply::kErrUnknownCommand;
  }

  /* The sketch tells the core whether a physically verified yellow pin exists.
   * Default false: docs/hardware_bringup.md records the yellow module input as
   * NOT CONNECTED, so yellow stays off until someone wires and measures it. */
  void setYellowEnabled(bool enabled) { yellow_enabled_ = enabled; }
  bool yellowEnabled() const { return yellow_enabled_; }

  /* Yellow is advisory only. The sketch calls yellowLevel() each loop pass and
   * writes the pin, so blinking stays non-blocking and the core keeps no timers
   * of its own beyond the heartbeat. */
  enum class YellowMode { Off, Steady, SlowBlink, FastBlink };
  YellowMode yellowMode() const { return yellow_mode_; }

  bool yellowLevel(uint32_t now_ms) const {
    switch (yellow_mode_) {
      case YellowMode::Steady:
        return true;
      case YellowMode::SlowBlink:
        return ((now_ms / kYellowSlowBlinkMs) % 2u) == 1u;
      case YellowMode::FastBlink:
        return ((now_ms / kYellowFastBlinkMs) % 2u) == 1u;
      case YellowMode::Off:
      default:
        return false;
    }
  }

  /* Blink half-periods in milliseconds. Fixed here rather than injected because
   * they are cosmetic and must not vary between builds. */
  static constexpr uint32_t kYellowSlowBlinkMs = 500;
  static constexpr uint32_t kYellowFastBlinkMs = 150;

  /* Formats a human-readable status block. Returns the number of bytes that
   * would have been written (like snprintf). */
  int formatStatus(char *buffer, size_t size, uint32_t now_ms) const {
    if (buffer == nullptr || size == 0) return 0;

    CoreOutputs out = outputs();
    return snprintf(
        buffer, size,
        "STATE=%s INCIDENT=%d ACK=%d RESET_READY=%d REARM=%u "
        "GREEN=%d RED=%d BUZZER=%d LINK=%d",
        stateName(state_), incident_id_, acknowledged_ ? 1 : 0,
        (reset_ready_incident_ == incident_id_ && incident_id_ > 0) ? 1 : 0,
        (unsigned)rearm_count_, out.green ? 1 : 0, out.red ? 1 : 0,
        out.buzzer ? 1 : 0, linkHealthy(now_ms) ? 1 : 0);
  }

 private:
  FwState state_ = FwState::Safe;
  int incident_id_ = 0;
  int next_incident_id_ = 1;
  int reset_ready_incident_ = 0;
  bool acknowledged_ = false;
  bool yellow_enabled_ = false;
  YellowMode yellow_mode_ = YellowMode::Off;
  bool heartbeat_seen_ = false;
  uint32_t last_heartbeat_ms_ = 0;
  uint32_t heartbeat_timeout_ms_ = 3000;
  uint32_t rearm_count_ = 0;
  uint32_t rejected_reset_count_ = 0;
};

}  // namespace eye_shield

#endif /* INCIDENT_CORE_H */
