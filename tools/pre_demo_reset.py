#!/usr/bin/env python3
"""Prepare the ESP32 for a new demo session.

The live demo leaves the board latched on purpose: an open incident is only
cleared by the acknowledge -> reset-ready -> reset handshake. That is correct
safety behaviour, but it makes starting the *next* demo awkward, because the
operator has to open a serial monitor and type the handshake by hand.

This tool performs exactly that handshake, using only the commands
``docs/protocol.md`` defines. It never touches a GPIO directly, never sends
``ALL_OFF`` or a pin preset, and cannot clear a latch the firmware would
refuse to clear: the board stays the authority throughout.

Sequence it uses, and why:

    STATUS                  read the board's own view; never guess
    HEARTBEAT               RESET is refused unless the link is fresh
    ACKNOWLEDGE <id>        skipped when the board already reports ACK=1
    RESET_READY <id>        arm the gate; refused unless acknowledged
    HEARTBEAT               refresh the link before the gated command
    RESET                   only now legal, and only for this incident
    STATUS                  verify the board really returned to SAFE

Exit codes:
    0   board is clean (either it already was, or recovery succeeded)
    1   recovery failed; the firmware's refusal is printed verbatim
    2   could not talk to the board (port missing, busy, or no STATUS line)
    3   operator declined to recover a latched incident
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass

import serial


DEFAULT_PORT = "/dev/cu.usbserial-10"
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 3.0
DEFAULT_SETTLE = 2.0

#: Reply codes that confirm each command, copied from ``reply::kOk*`` in
#: firmware/esp32_eye_shield_monitor/incident_core.h. A command is only
#: considered executed when the matching code comes back.
CODE_ACKNOWLEDGED = "ACKNOWLEDGED"
CODE_RESET_READY = "RESET_READY"
CODE_RESET_ACCEPTED = "RESET_ACCEPTED"
CODE_SAFE = "SAFE"
CODE_HEARTBEAT = "HEARTBEAT"


class ProtocolError(RuntimeError):
    """The board answered, but not with what the command implies."""


class LinkError(RuntimeError):
    """The serial link itself failed (port missing, busy, or unplugged)."""


@dataclass(frozen=True)
class Reply:
    """One parsed ``OK ...`` or ``ERR ...`` line."""

    raw: str
    ok: bool
    code: str
    detail: str = ""


def parse_reply(line: str) -> Reply | None:
    """Parse a firmware reply, or return None for banners and other noise.

    The prefix match requires a following space (or the whole line) so that the
    sketch's ``ERROR unknown command: ...`` line is not mistaken for an
    ``ERR <code>`` protocol reply.
    """

    text = line.strip()

    if text == "OK" or text.startswith("OK "):
        code = text[2:].strip()
        head, _, detail = code.partition(" ")
        return Reply(raw=text, ok=True, code=head, detail=detail.strip())

    if text == "ERR" or text.startswith("ERR "):
        body = text[3:].strip()
        head, _, detail = body.partition(" ")
        return Reply(raw=text, ok=False, code=head, detail=detail.strip())

    return None


@dataclass(frozen=True)
class BoardStatus:
    """The firmware's machine-readable view of itself.

    Produced by ``IncidentCore::formatStatus()`` and prefixed with ``STATUS``
    on the wire, so the laptop reads the board's state instead of assuming it.
    """

    state: str
    incident: int
    ack: bool
    reset_ready: bool
    rearm: int
    green: bool
    red: bool
    buzzer: bool
    link: bool
    raw: str

    @property
    def latched(self) -> bool:
        return self.incident > 0

    @property
    def clean(self) -> bool:
        """Idle and quiet: no incident, SAFE, both lamps in their idle shape."""

        return (
            self.incident == 0
            and self.state == "SAFE"
            and not self.red
            and not self.buzzer
        )


_REQUIRED_STATUS_FIELDS = ("STATE", "INCIDENT", "ACK", "GREEN", "RED", "BUZZER")


def parse_status(body: str) -> BoardStatus:
    """Parse the ``k=v`` payload that follows the ``STATUS`` prefix."""

    fields: dict[str, str] = {}

    for token in body.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key.upper()] = value

    missing = [name for name in _REQUIRED_STATUS_FIELDS if name not in fields]

    if missing:
        raise ProtocolError(
            f"STATUS line is missing {', '.join(missing)}: {body!r}"
        )

    try:
        return BoardStatus(
            state=fields["STATE"].upper(),
            incident=int(fields["INCIDENT"]),
            ack=fields["ACK"] == "1",
            reset_ready=fields.get("RESET_READY", "0") == "1",
            rearm=int(fields.get("REARM", "0")),
            green=fields["GREEN"] == "1",
            red=fields["RED"] == "1",
            buzzer=fields["BUZZER"] == "1",
            link=fields.get("LINK", "0") == "1",
            raw=body,
        )
    except ValueError as error:
        raise ProtocolError(f"STATUS line has a non-numeric field: {body!r}") from error


def describe(status: BoardStatus) -> str:
    """Render the board status the way an operator reads it at a demo."""

    lines = [
        f"  lifecycle    : {status.state}",
        f"  incident id  : {status.incident}"
        + (" (open)" if status.latched else " (none)"),
        f"  acknowledged : {'yes' if status.ack else 'no'}",
        f"  reset armed  : {'yes' if status.reset_ready else 'no'}",
        f"  green LED    : {'ON' if status.green else 'off'}",
        f"  red LED      : {'ON' if status.red else 'off'}",
        f"  buzzer       : {'ON' if status.buzzer else 'off'}",
        f"  link health  : {'alive' if status.link else 'stale'}",
    ]

    if status.rearm:
        lines.append(f"  re-arms      : {status.rearm}")

    return "\n".join(lines)


class BoardLink:
    """The serial link, with the demo's request/response discipline.

    One command in flight at a time, and a command only counts as executed when
    the reply code the firmware documents for it arrives.
    """

    def __init__(
        self,
        port: str,
        baud: int,
        timeout: float,
        settle: float,
        echo: bool = True,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.settle = settle
        self.echo = echo
        self.steps: list[tuple[str, str]] = []
        self._connection: serial.Serial | None = None

    # -- lifecycle -------------------------------------------------------

    def open(self) -> None:
        try:
            self._connection = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=0.2,
                write_timeout=1.0,
            )
        except serial.SerialException as error:
            raise LinkError(
                f"cannot open {self.port}: {error}"
            ) from error

        # Opening the USB CDC bridge resets the ESP32-S3, so the boot banner
        # and the self-test chatter are consumed here rather than being
        # mistaken for the answer to our first command.
        deadline = time.monotonic() + self.settle
        self._drain(deadline)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "BoardLink":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- raw io ----------------------------------------------------------

    def _drain(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self._connection is None:
                return
            if not self._connection.in_waiting:
                time.sleep(0.02)
                continue
            self._readline(deadline)

    def _readline(self, deadline: float) -> str | None:
        """Return the next non-empty line, or None once the deadline passes."""

        while time.monotonic() < deadline:
            if self._connection is None:
                raise LinkError("serial connection is closed")

            raw = self._connection.readline()

            if not raw:
                continue

            line = raw.decode("utf-8", errors="replace").strip()

            if not line:
                continue

            if self.echo:
                print(f"  ESP32 < {line}")

            return line

        return None

    def _write(self, command: str) -> None:
        if self._connection is None:
            raise LinkError("serial connection is closed")

        try:
            self._connection.write(f"{command.strip()}\n".encode("utf-8"))
            self._connection.flush()
        except serial.SerialException as error:
            raise LinkError(f"write failed for {command!r}: {error}") from error

        print(f"  MAC   > {command}")

    # -- protocol --------------------------------------------------------

    def command(self, command: str, expected: str) -> Reply:
        """Send ``command`` and require ``expected`` as its reply code."""

        self._write(command)
        deadline = time.monotonic() + self.timeout

        while True:
            line = self._readline(deadline)

            if line is None:
                raise ProtocolError(
                    f"no reply to {command!r} within {self.timeout:.1f}s "
                    f"(expected OK {expected})"
                )

            reply = parse_reply(line)

            if reply is None:
                continue

            if not reply.ok:
                raise ProtocolError(f"{command!r} refused: {reply.raw}")

            if reply.code == expected:
                self.steps.append((command, reply.raw))
                return reply

            # An unrelated OK (a heartbeat in flight, say) does not answer this
            # command, so it is reported and the wait continues.
            print(
                f"  [note] ignoring OK {reply.code}; "
                f"waiting for OK {expected}"
            )

    def heartbeat(self) -> None:
        """Refresh the link clock.

        ``RESET`` is refused with ``LINK_OR_HEALTH_INVALID`` unless a heartbeat
        arrived within the firmware's 3000 ms window, so this is sent
        immediately before the gated command rather than once at startup.
        """

        self.command("HEARTBEAT", CODE_HEARTBEAT)

    def status(self) -> BoardStatus:
        """Ask for STATUS and parse the single machine-readable line."""

        self._write("STATUS")
        deadline = time.monotonic() + self.timeout

        while True:
            line = self._readline(deadline)

            if line is None:
                raise ProtocolError(
                    "no STATUS line from the board; check the firmware build"
                )

            if line.startswith("STATUS "):
                status = parse_status(line[len("STATUS ") :])
                self.steps.append(("STATUS", line))
                return status


def confirm(message: str) -> bool:
    try:
        answer = input(f"{message} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def normalise_idle_board(link: BoardLink, status: BoardStatus) -> BoardStatus:
    """Bring a board with no open incident back to a quiet SAFE.

    ``SAFE`` is legal whenever no incident is open, so this covers a board left
    in FAULT or holding stale lamp state without touching any GPIO.
    """

    if status.clean:
        print("Board already clean")
        return status

    print(
        f"\nBoard has no open incident but is not idle "
        f"(STATE={status.state}, RED={int(status.red)}, "
        f"BUZZER={int(status.buzzer)})."
    )
    print("Sending SAFE to return it to the idle green state.")

    link.command("SAFE", CODE_SAFE)
    verify = link.status()

    if not verify.clean:
        raise ProtocolError(
            "board did not return to SAFE after SAFE was accepted: "
            f"{verify.raw}"
        )

    return verify


def recover_latched_board(link: BoardLink, status: BoardStatus) -> BoardStatus:
    """Drive the acknowledge -> reset-ready -> reset handshake.

    The order is fixed by the firmware. Each step below names the refusal it
    can produce, so a failure reports the real blocker instead of a generic
    error.
    """

    incident = status.incident

    if status.ack:
        print(
            f"\nIncident {incident} is already acknowledged on the board; "
            "skipping ACKNOWLEDGE."
        )
    else:
        print(f"\nAcknowledging incident {incident}.")
        link.command(f"ACKNOWLEDGE {incident}", CODE_ACKNOWLEDGED)

    print(f"Asserting recovery for incident {incident}.")
    link.command(f"RESET_READY {incident}", CODE_RESET_READY)

    # The gate checks link health, and the operator prompt above may have
    # consumed the window, so a fresh heartbeat is sent here.
    link.heartbeat()

    print(f"Clearing incident {incident}.")
    link.command("RESET", CODE_RESET_ACCEPTED)

    verify = link.status()

    if verify.incident != 0:
        raise ProtocolError(
            f"board still reports incident {verify.incident} after RESET: "
            f"{verify.raw}"
        )

    if verify.state != "SAFE":
        raise ProtocolError(
            f"board left the reset in STATE={verify.state}, expected SAFE: "
            f"{verify.raw}"
        )

    return verify


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clear a latched incident and confirm the ESP32 is ready for a "
            "new demo session. Uses only the documented lifecycle handshake."
        )
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help="serial port")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds to wait for each reply",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE,
        help="seconds to drain the boot banner after opening the port",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="recover without asking for confirmation",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not echo raw serial lines",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    print(f"Opening {args.port} at {args.baud} baud")

    link = BoardLink(
        port=args.port,
        baud=args.baud,
        timeout=args.timeout,
        settle=args.settle,
        echo=not args.quiet,
    )

    try:
        link.open()
    except LinkError as error:
        print(f"\n[FAIL] {error}")
        print(
            "       If the port is busy, the live demo or a serial monitor "
            "still holds it:"
        )
        print(f"       lsof {args.port}")
        return 2

    try:
        with link:
            status = link.status()

            print("\nCurrent board state")
            print(describe(status))

            if not status.latched:
                status = normalise_idle_board(link, status)
            else:
                if not args.auto and not confirm(
                    f"\nIncident {status.incident} is open. Recover it now?"
                ):
                    print("\nAborted at operator request; board left unchanged.")
                    return 3

                status = recover_latched_board(link, status)

            if not status.clean:
                raise ProtocolError(
                    f"board is not idle after recovery: {status.raw}"
                )

            print("\nFinal board state")
            print(describe(status))

            if link.steps:
                print("\nCommand trace")
                for command, reply in link.steps:
                    print(f"  {command:<18} -> {reply}")

            print("\nREADY FOR NEW DEMO")
            return 0

    except ProtocolError as error:
        print(f"\n[FAIL] {error}")
        print("       Board recovered nothing; it was left as it answered.")
        return 1

    except LinkError as error:
        print(f"\n[FAIL] serial link lost: {error}")
        return 2

    except serial.SerialException:
        print("\n[FAIL] serial error")
        traceback.print_exc()
        return 2

    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 3


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
