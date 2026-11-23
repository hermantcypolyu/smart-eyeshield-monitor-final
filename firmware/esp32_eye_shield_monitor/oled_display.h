/*
 * oled_display.h — SSD1306 I2C status panel driver
 * ===========================================================================
 * The only file in this feature that touches Adafruit_SSD1306, Wire or the I2C
 * bus. Everything it draws comes from oled_status_model.h, which is pure data,
 * so this layer has exactly one job: format a snapshot onto glass.
 *
 * DESIGN RULES, in order of importance
 * ------------------------------------
 *  1. NEVER BLOCK THE SAFETY PATH. A full 128x64 frame is 1 KB. Pushed at
 *     400 kHz that is ~23 ms of bus time. It is rate-limited, and an absent or
 *     failing panel is abandoned rather than retried forever, because loop()
 *     also carries the serial poll and the non-blocking self-test.
 *  2. NEVER LIE. The three indicator glyphs are drawn from the OUTPUTS THE CORE
 *     ASKED FOR (CoreOutputs), not from a second opinion computed here. If the
 *     red pin is low, the red glyph is hollow, full stop.
 *  3. NEVER AFFECT A PIN. This file only drives the display. It cannot silence
 *     a buzzer, clear a latch or change a transition - it has no reference to
 *     IncidentCore and cannot reach it.
 *  4. FAIL SOFT. If the panel is missing at boot (unplugged, miswired, dead) the
 *     sketch prints one warning, keeps running, and retries a bounded number of
 *     times. Nothing else in the firmware changes behaviour.
 *
 * WHY begin() IS NOT ENOUGH TO DETECT A PANEL
 * -------------------------------------------
 * Adafruit_SSD1306::begin() unconditionally returns true - it issues the init
 * sequence and never checks that anything acknowledged. Taking it at its word
 * would mean a missing panel looks healthy. So this driver probes the address
 * over Wire first and only then calls begin(), which is what makes the
 * "auto-detect 0x3C or 0x3D, degrade gracefully" requirement real.
 * ===========================================================================
 */

#ifndef OLED_DISPLAY_H
#define OLED_DISPLAY_H

/* The OLED_* macros come from the sketch's include chain (config.h when present,
 * config.example.h otherwise), so this header must be included AFTER that. It
 * deliberately does not include a config file itself: the sketch owns the
 * fallback, and duplicating it here would fork the defaults. */

#if OLED_ENABLED

/* Suppress Adafruit's own splash logo: begin() draws it before we get a
 * chance to paint, and the operator should see our boot screen, not theirs. */
#ifndef SSD1306_NO_SPLASH
#  define SSD1306_NO_SPLASH
#endif
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Arduino.h>
#include <Wire.h>

#include "oled_status_model.h"

namespace eye_shield {

class OledDisplay {
 public:
  /* Probe, initialise, and paint the boot screen. Returns false when no panel
   * answered at either address; every later call is then a no-op. Never blocks
   * for more than a single I2C probe. */
  bool begin(uint32_t now_ms) {
    now_ms_ = now_ms;
    last_probe_ms_ = now_ms;

    if (probeAndInit()) {
      present_ = true;
      showBootScreen();
      boot_ms_ = now_ms;
      return true;
    }

    present_ = false;
    attempts_ = 1;
    return false;
  }

  /* One-line description for the serial STATUS block, so a laptop can tell
   * whether the panel is actually up. */
  const char *statusText() const {
    if (present_) {
      return address_ == OLED_PRIMARY_I2C_ADDRESS ? "ONLINE @0x3C" : "ONLINE @0x3D";
    }
    if (attempts_ >= OLED_MAX_INIT_ATTEMPTS) return "ABSENT (gave up)";
    if (attempts_ == 0) return "ABSENT (not probed)";
    return "ABSENT (retrying)";
  }

  bool present() const { return present_; }
  uint8_t address() const { return address_; }

  /* Boot splash. Only called from begin(), so the ~23 ms push happens once at
   * startup rather than in a live frame. */
  void showBootScreen() {
    if (!present_) return;
    display_.clearDisplay();
    display_.setTextColor(SSD1306_WHITE);

    display_.setTextSize(1);
    display_.setCursor(0, 0);
    display_.print(F("SMART EYE-SHIELD"));
    display_.drawFastHLine(0, 11, OLED_WIDTH, SSD1306_WHITE);

    display_.setTextSize(2);
    display_.setCursor(0, 20);
    display_.print(F("MONITOR"));

    display_.setTextSize(1);
    display_.setCursor(0, 42);
    display_.print(F("Initializing..."));

    display_.setCursor(0, 54);
    display_.print(F("lifecycle + serial"));
    display_.display();
  }

  /* Draw the snapshot if it changed, or if the periodic safety refresh is due.
   * Non-blocking in the sense that matters: it owns no loop and no delay, and
   * returns immediately when there is nothing to do. */
  void update(const OledStatusSnapshot &snapshot, uint32_t now_ms) {
    now_ms_ = now_ms;

    if (!present_) {
      maybeRetryInit(now_ms);
      return;
    }

    /* Hold the boot splash for OLED_BOOT_HOLD_MS so it is actually readable
     * next to a laptop that takes longer than the ESP32 to come up. This is a
     * comparison, not a delay: the main loop keeps running. */
    if (!has_drawn_ && (uint32_t)(now_ms - boot_ms_) < OLED_BOOT_HOLD_MS) return;

    const bool dirty = !has_drawn_ || !oledSnapshotEquals(snapshot, last_);
    const uint32_t since_draw = now_ms - last_draw_ms_;
    const bool periodic = has_drawn_ && since_draw >= OLED_REFRESH_MS;

    if (!dirty && !periodic) return;

    /* A burst of transitions must not become a burst of I2C traffic. A changed
     * state that arrives inside the floor interval waits for the next pass,
     * which still lands within OLED_REFRESH_MS. */
    if (has_drawn_ && since_draw < OLED_MIN_DRAW_INTERVAL_MS && !periodic) return;

    render(snapshot);
    last_ = snapshot;
    last_draw_ms_ = now_ms;
    has_drawn_ = true;
  }

  /* Called when the panel was missing at boot. Bounded: after
   * OLED_MAX_INIT_ATTEMPTS the driver stops touching the bus for good. */
  void maybeRetryInit(uint32_t now_ms) {
    if (present_) return;
    if (attempts_ >= OLED_MAX_INIT_ATTEMPTS) return;
    if ((uint32_t)(now_ms - last_probe_ms_) < OLED_RETRY_MS) return;

    last_probe_ms_ = now_ms;
    attempts_++;

    if (probeAndInit()) {
      present_ = true;
      showBootScreen();
    }
  }

 private:
  /* Ask the bus whether anything acknowledges at each candidate address. This
   * is the real presence test; begin() alone would lie. */
  bool probeAndInit() {
    Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
    Wire.setClock(OLED_I2C_CLOCK_HZ);

    uint8_t found = 0;
    if (i2cResponds(OLED_PRIMARY_I2C_ADDRESS)) {
      found = OLED_PRIMARY_I2C_ADDRESS;
    } else if (i2cResponds(OLED_ALTERNATE_I2C_ADDRESS)) {
      found = OLED_ALTERNATE_I2C_ADDRESS;
    }

    if (found == 0) return false;

    /* periphBegin = false: Wire is already up on our pins, and letting the
     * library call begin() again would re-init it with the default pins. */
    const bool ok = display_.begin(SSD1306_SWITCHCAPVCC, found, false, false);
    if (!ok) return false;

    address_ = found;
    return true;
  }

  static bool i2cResponds(uint8_t address) {
    Wire.beginTransmission(address);
    return Wire.endTransmission() == 0;
  }

  /* Fixed layout, 128x64, computed in constants so a geometry change is one
   * edit. Sizes are in pixels; text size 1 is 6x8, size 2 is 12x16. */
  static const int16_t kBannerTop = 12;
  static const int16_t kBannerHeight = 20;
  static const int16_t kDetailY = 36;
  static const int16_t kIndicatorY = 50;
  static const int16_t kFooterY = 57;

  void render(const OledStatusSnapshot &snapshot) {
    display_.clearDisplay();
    display_.setTextColor(SSD1306_WHITE);

    drawHeader(snapshot);
    drawBanner(snapshot);
    drawIndicators(snapshot);
    drawFooter(snapshot);

    /* One buffer flush per frame. Nothing was drawn to the panel before this
     * call, so there is no intermediate state to flicker through. */
    display_.display();
  }

  void drawHeader(const OledStatusSnapshot &snapshot) {
    display_.setTextSize(1);
    display_.setCursor(0, 0);
    display_.print(F("EYE-SHIELD"));

    /* The firmware's own lifecycle word, right-aligned. Drawn small and
     * secondary because the banner is the operator's headline. */
    const int16_t width = 6 * (int16_t)strlen(snapshot.state_name);
    display_.setCursor(OLED_WIDTH - width, 0);
    display_.print(snapshot.state_name);

    display_.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);
  }

  void drawBanner(const OledStatusSnapshot &snapshot) {
    /* Alert gets a solid bar behind the text so a violation is visible from
     * across the room, before a single word is read. Every other severity uses
     * plain text, so the inverted bar itself carries meaning. */
    const bool inverted = snapshot.severity == OledSeverity::Alert ||
                          snapshot.severity == OledSeverity::Fault;

    if (inverted) {
      display_.fillRect(0, kBannerTop, OLED_WIDTH, kBannerHeight, SSD1306_WHITE);
      display_.setTextColor(SSD1306_BLACK);
    } else {
      display_.setTextColor(SSD1306_WHITE);
    }

    display_.setTextSize(2);
    /* Vertically centre a 16 px glyph inside the 20 px banner. */
    display_.setCursor(2, kBannerTop + 2);
    display_.print(snapshot.headline);
    display_.setTextColor(SSD1306_WHITE);

    display_.setTextSize(1);
    display_.setCursor(0, kDetailY);
    display_.print(snapshot.detail);
  }

  /* Three glyphs: green lamp, red lamp, buzzer. Filled = energised. Drawn from
   * the outputs the core asked for, so the panel cannot contradict the pins. */
  void drawIndicators(const OledStatusSnapshot &snapshot) {
    drawIndicator(14, snapshot.green_on, "G");
    drawIndicator(60, snapshot.red_on, "R");
    drawIndicator(106, snapshot.buzzer_on, "B");
  }

  void drawIndicator(int16_t x, bool on, const char *label) {
    const int16_t radius = 5;
    if (on) {
      display_.fillCircle(x, kIndicatorY, radius, SSD1306_WHITE);
    } else {
      display_.drawCircle(x, kIndicatorY, radius, SSD1306_WHITE);
    }
    display_.setTextSize(1);
    display_.setCursor(x - 3, kFooterY);
    display_.print(label);
  }

  void drawFooter(const OledStatusSnapshot &snapshot) {
    display_.setTextSize(1);
    display_.setCursor(24, kFooterY);

    if (snapshot.incident_id > 0) {
      display_.print(F("inc "));
      display_.print(snapshot.incident_id);
      if (snapshot.acknowledged) display_.print(F(" ACK"));
    } else if (snapshot.link_stale) {
      display_.print(F("link stale"));
    } else {
      display_.print(F("link ok"));
    }
  }

  Adafruit_SSD1306 display_{OLED_WIDTH, OLED_HEIGHT, &Wire, -1};

  bool present_ = false;
  bool has_drawn_ = false;
  uint8_t address_ = 0;
  uint8_t attempts_ = 0;
  uint32_t now_ms_ = 0;
  uint32_t boot_ms_ = 0;
  uint32_t last_probe_ms_ = 0;
  uint32_t last_draw_ms_ = 0;
  OledStatusSnapshot last_ = {};
};

}  // namespace eye_shield

#endif /* OLED_ENABLED */

#endif /* OLED_DISPLAY_H */
