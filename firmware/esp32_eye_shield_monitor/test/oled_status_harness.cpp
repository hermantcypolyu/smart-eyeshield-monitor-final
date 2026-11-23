// oled_status_harness.cpp — line-oriented host driver for oled_status_model.h
//
// Compiled by tests/test_oled_status_model.py. The model is pure C++ with no
// Arduino dependency precisely so it can be exercised here: the exact header
// that decides what the panel says on the board is the one under test, not a
// Python re-implementation that could drift.
//
// Directives (@ prefix):
//   @TICK <ms>        set the injected millisecond clock
//   @OUT <g> <r> <b>  override the reported lamp states (0/1 each)
//   @HEARTBEAT        send HEARTBEAT so the link counts as alive
//   @RENDER           print the snapshot the panel would draw
//   @QUIT             stop reading
//
// Anything else is fed to the incident core as a protocol command.

#include "oled_status_model.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

using eye_shield::CoreOutputs;
using eye_shield::IncidentCore;
using eye_shield::OledSeverity;
using eye_shield::OledStatusSnapshot;
using eye_shield::ParsedCommand;

namespace {

IncidentCore g_core(3000);
uint32_t g_now_ms = 0;

// The lamps as the sketch believes them to be. Defaults to whatever the core
// asked for, but @OUT can force a disagreement so the tests can prove the model
// reports the pins rather than the core's wishes.
CoreOutputs g_forced{};
bool g_forced_set = false;

CoreOutputs effectiveOutputs() {
  return g_forced_set ? g_forced : g_core.outputs();
}

void printRender() {
  const OledStatusSnapshot snapshot =
      eye_shield::buildOledStatus(g_core, effectiveOutputs(),
                                  g_core.link_alive(g_now_ms));
  printf("RENDER headline=\"%s\" detail=\"%s\" severity=%s green=%d red=%d "
         "buzzer=%d state=%s incident=%d ack=%d link_stale=%d\n",
         snapshot.headline, snapshot.detail,
         eye_shield::oledSeverityName(snapshot.severity),
         snapshot.green_on ? 1 : 0, snapshot.red_on ? 1 : 0,
         snapshot.buzzer_on ? 1 : 0, snapshot.state_name, snapshot.incident_id,
         snapshot.acknowledged ? 1 : 0, snapshot.link_stale ? 1 : 0);
  fflush(stdout);
}

}  // namespace

int main() {
  char line[256];
  while (fgets(line, sizeof(line), stdin) != nullptr) {
    size_t length = strlen(line);
    while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
      line[--length] = '\0';
    }

    if (strncmp(line, "@TICK ", 6) == 0) {
      g_now_ms = (uint32_t)strtoul(line + 6, nullptr, 10);
      printf("TICK %u\n", g_now_ms);
      fflush(stdout);
      continue;
    }

    if (strncmp(line, "@OUT ", 5) == 0) {
      int g = 0, r = 0, b = 0;
      if (sscanf(line + 5, "%d %d %d", &g, &r, &b) == 3) {
        g_forced.green = g != 0;
        g_forced.red = r != 0;
        g_forced.buzzer = b != 0;
        g_forced_set = true;
        printf("OUT green=%d red=%d buzzer=%d\n", g != 0 ? 1 : 0, r != 0 ? 1 : 0,
               b != 0 ? 1 : 0);
      } else {
        printf("OUT error\n");
      }
      fflush(stdout);
      continue;
    }

    if (strcmp(line, "@CLEAROUT") == 0) {
      g_forced_set = false;
      printf("OUT cleared\n");
      fflush(stdout);
      continue;
    }

    if (strcmp(line, "@RENDER") == 0) {
      printRender();
      continue;
    }

    if (strcmp(line, "@QUIT") == 0) break;

    const ParsedCommand parsed = eye_shield::parseFwCommand(line, length);
    CoreOutputs outputs = effectiveOutputs();
    const char *reply = g_core.handle(parsed, g_now_ms, &outputs);

    // Feed the pin state back the way the sketch does: a command that changes
    // the outputs clears any forced override, because the pins really moved.
    if (reply != nullptr) {
      g_forced_set = false;
      printf("%s | state=%s\n", reply, IncidentCore::stateName(g_core.state()));
    } else {
      printf("STATUS | state=%s\n", IncidentCore::stateName(g_core.state()));
    }
    fflush(stdout);
  }

  return 0;
}
