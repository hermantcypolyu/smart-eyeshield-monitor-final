/*
 * Command table type for esp32_eye_shield_monitor.
 * ---------------------------------------------------------------------------
 * This lives in a header rather than the .ino on purpose.
 *
 * The Arduino builder generates prototypes for every sketch function and pastes
 * them into a single block near the top of the translation unit, before any
 * sketch definition. `findOutputCommand()` returns a `const CommandEntry *`, so
 * that block needs the type to already exist. A forward declaration inside the
 * .ino is not enough: it lands after the injected prototypes and the compiler
 * reports "'CommandEntry' does not name a type".
 *
 * Anything the injected prototypes may reference must therefore be defined in a
 * header included from the .ino.
 */

#ifndef COMMAND_TABLE_H
#define COMMAND_TABLE_H

#include <Arduino.h>

struct CommandEntry {
  const char *name;   /* matched case-insensitively against trimmed input */
  const char *reply;  /* line printed after the handler returns */
  void (*handler)();
};

#endif /* COMMAND_TABLE_H */
