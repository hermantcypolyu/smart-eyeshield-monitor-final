from __future__ import annotations

import argparse
import time

import serial


def read_available(
    connection: serial.Serial,
    duration_seconds: float = 1.0,
) -> None:
    deadline = time.monotonic() + duration_seconds

    while time.monotonic() < deadline:
        if connection.in_waiting:
            line = connection.readline().decode(
                "utf-8",
                errors="replace",
            ).strip()

            if line:
                print(f"ESP32 > {line}")
        else:
            time.sleep(0.02)


def send_command(
    connection: serial.Serial,
    command: str,
) -> None:
    payload = f"{command.strip()}\n".encode("utf-8")
    connection.write(payload)
    connection.flush()
    print(f"MAC   > {command}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        default="/dev/cu.usbserial-10",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
    )
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud")

    with serial.Serial(
        port=args.port,
        baudrate=args.baud,
        timeout=0.2,
        write_timeout=1.0,
    ) as connection:
        # Opening the UART bridge may reset the ESP32.
        time.sleep(2.0)
        connection.reset_input_buffer()

        print("Connected.")
        print(
            "Commands: STATUS SAFE ALARM SILENCE "
            "ALL_OFF HELP QUIT"
        )

        send_command(connection, "STATUS")
        read_available(connection, 1.0)

        while True:
            command = input("Command > ").strip().upper()

            if not command:
                continue

            if command in {"QUIT", "EXIT"}:
                send_command(connection, "ALL_OFF")
                read_available(connection, 0.5)
                break

            allowed = {
                "STATUS",
                "SAFE",
                "ALARM",
                "SILENCE",
                "ALL_OFF",
                "HELP",
            }

            if command not in allowed:
                print("Unsupported test command.")
                continue

            send_command(connection, command)
            read_available(connection, 1.0)

    print("Serial connection closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
