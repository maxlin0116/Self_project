import argparse
import time

import serial

from glove_serial_car_control import BAUD_RATE
from glove_serial_car_control import choose_serial_port
from glove_serial_car_control import list_ports
from glove_serial_car_control import parse_glove_line


STATUS_INTERVAL_SECONDS = 0.2
CHANNELS = (
    ("A0", "index"),
    ("A1", "middle"),
    ("A2", "ring"),
)


def parse_args():
    parser = argparse.ArgumentParser(description="Show glove A0/A1/A2 raw values and voltages.")
    parser.add_argument("--serial-port", default="auto", help="Arduino serial port, or auto")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--vref", type=float, default=5.0, help="ADC reference voltage")
    parser.add_argument("--interval", type=float, default=STATUS_INTERVAL_SECONDS)
    parser.add_argument(
        "--channel",
        choices=("all", "index", "middle", "ring"),
        default="all",
        help="Show one finger or all fingers",
    )
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    return parser.parse_args()


def format_reading(values, vref, channel):
    parts = []
    for analog_name, finger in CHANNELS:
        if channel != "all" and finger != channel:
            continue
        raw_value = values[finger]
        voltage = raw_value * vref / 1023.0
        parts.append(f"{analog_name}/{finger}={raw_value:4d} {voltage:.3f}V")
    return "  ".join(parts)


def main():
    args = parse_args()

    if args.list_ports:
        for port in list_ports():
            print(f"{port.device}: {port.description}")
        return

    serial_port = choose_serial_port(args.serial_port)
    print(f"Serial: {serial_port} @ {args.baud}")
    print(f"Voltage: raw * {args.vref} / 1023")
    print("Channels: A0=index, A1=middle, A2=ring")
    print("Press Ctrl+C to stop.")

    last_status_at = 0.0
    with serial.Serial(serial_port, args.baud, timeout=0.2) as arduino:
        arduino.reset_input_buffer()
        try:
            while True:
                raw_line = arduino.readline().decode("utf-8", errors="replace").strip()
                if not raw_line:
                    continue

                values = parse_glove_line(raw_line)
                now = time.perf_counter()
                if values is None:
                    if now - last_status_at >= args.interval:
                        print(f"serial: {raw_line}")
                        last_status_at = now
                    continue

                if now - last_status_at >= args.interval:
                    print(format_reading(values, args.vref, args.channel))
                    last_status_at = now
        except KeyboardInterrupt:
            print()
            print("Stopped.")


if __name__ == "__main__":
    main()
