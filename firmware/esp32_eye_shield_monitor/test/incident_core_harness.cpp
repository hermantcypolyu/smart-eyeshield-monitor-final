// incident_core_harness.cpp — line-oriented host driver for incident_core.h
//
// Compiled by tests/test_incident_core.py with the host compiler and driven
// over stdin/stdout so the firmware's own parsing and reset gating can be
// tested without a board. Reads one command per line; writes one reply per
// line, suffixed with the resulting state so tests can check both at once.
//
// Directives (@ prefix) drive the things the sketch would get from hardware:
//   @TICK <ms>       set the injected millisecond clock
//   @YELLOW <0|1>    tell the core whether a verified yellow pin exists
//   @STATE           print state and desired outputs, no command
//   @QUIT            stop reading

#include "incident_core.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

using eye_shield::CoreOutputs;
using eye_shield::IncidentCore;
using eye_shield::ParsedCommand;

int main() {
  IncidentCore core(3000);
  unsigned long now_ms = 0;

  char line[256];
  while (fgets(line, sizeof(line), stdin) != nullptr) {
    // Strip the trailing newline the same way the sketch's line reader does.
    size_t length = strlen(line);
    while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
      line[--length] = '\0';
    }

    if (strncmp(line, "@TICK ", 6) == 0) {
      now_ms = strtoul(line + 6, nullptr, 10);
      printf("TICK %lu\n", now_ms);
      fflush(stdout);
      continue;
    }

    if (strncmp(line, "@YELLOW ", 8) == 0) {
      core.setYellowEnabled(strtoul(line + 8, nullptr, 10) != 0);
      printf("YELLOW %d\n", core.yellowEnabled() ? 1 : 0);
      fflush(stdout);
      continue;
    }

    if (strcmp(line, "@STATE") == 0) {
      CoreOutputs out = core.outputs();
      printf("STATE %s incident=%d ack=%d green=%d red=%d buzzer=%d\n",
                  IncidentCore::stateName(core.state()), core.incident_id(),
                  core.acknowledged() ? 1 : 0, out.green ? 1 : 0,
                  out.red ? 1 : 0, out.buzzer ? 1 : 0);
      fflush(stdout);
      continue;
    }

    if (strcmp(line, "@QUIT") == 0) break;

    ParsedCommand parsed = eye_shield::parseFwCommand(line, length);
    CoreOutputs outputs;
    const char *reply = core.handle(parsed, (uint32_t)now_ms, &outputs);

    if (reply == nullptr) {
      printf("NONE\n");
      fflush(stdout);
      continue;
    }

    // "<reply> | state=<s> incident=<n> green=<g> red=<r> buzzer=<b>"
    printf("%s | state=%s incident=%d green=%d red=%d buzzer=%d\n", reply,
                IncidentCore::stateName(core.state()), core.incident_id(),
                outputs.green ? 1 : 0, outputs.red ? 1 : 0,
                outputs.buzzer ? 1 : 0);
    fflush(stdout);
  }

  return 0;
}
