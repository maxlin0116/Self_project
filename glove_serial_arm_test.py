import argparse
import time

import requests
import serial

from glove_serial_car_control import choose_serial_port
from glove_serial_car_control import format_values
from glove_serial_car_control import parse_glove_line
from glove_serial_car_control import resolve_car_ip


BAUD_RATE = 115200
REQUEST_TIMEOUT_SECONDS = 3.0
STATUS_INTERVAL_SECONDS = 0.5

INDEX_THRESHOLD = 630
MIDDLE_THRESHOLD = 420
RING_THRESHOLD = 380

DEFAULT_INDEX_JOG_SPEED = 110
DEFAULT_INDEX_JOG_MS = 80
DEFAULT_MIDDLE_JOG_SPEED = 20
DEFAULT_MIDDLE_JOG_MS = 100
STOP_SPEED = 90


class ArmSender:
    def __init__(self, ip, dry_run=False):
        self.url = f"http://{ip}/data"
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.trust_env = False
        self.last_arm_command = None
        self.last_grip_command = None

    def send(self, command):
        if self.dry_run:
            print(f"DRY RUN arm command: {command}")
            return

        try:
            response = self.session.post(
                self.url,
                data=command.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            print(f"POST {command} -> HTTP {response.status_code}")
        except requests.exceptions.RequestException as error:
            print(f"Arm command failed: {error}")

    def set_arm_motion(self, command, force=False):
        if not force and command == self.last_arm_command:
            return
        self.last_arm_command = command
        self.send(command)

    def jog_shoulder(self, speed, duration_ms, stop_speed):
        self.last_arm_command = None
        self.send(f"set shoulder {speed}")
        time.sleep(max(0, duration_ms) / 1000.0)
        self.send(f"set shoulder {stop_speed}")

    def set_gripper(self, command):
        if command == self.last_grip_command:
            return
        self.last_grip_command = command
        self.send(command)


def active_fingers(values, thresholds):
    return {
        "index": values["index"] > thresholds["index"],
        "middle": values["middle"] < thresholds["middle"],
        "ring": values["ring"] < thresholds["ring"],
    }


def gripper_command(active):
    if active["ring"]:
        return "close", "GRIP CLOSE"
    return "open", "GRIP OPEN"


def format_active(active):
    return " ".join(f"{finger}:{'1' if active[finger] else '0'}" for finger in ("index", "middle", "ring"))


def clamped_servo_value(value):
    return max(0, min(180, value))


def parse_args():
    parser = argparse.ArgumentParser(description="Read glove serial values and test arm/gripper control.")
    parser.add_argument("--serial-port", default="auto", help="Arduino serial port, or auto")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--car-ip", default="auto", help="Car ESP32 IP, or auto")
    parser.add_argument("--index-threshold", type=int, default=INDEX_THRESHOLD)
    parser.add_argument("--middle-threshold", type=int, default=MIDDLE_THRESHOLD)
    parser.add_argument("--ring-threshold", type=int, default=RING_THRESHOLD)
    parser.add_argument("--thresholds", help="Thresholds as index,middle,ring")
    parser.add_argument("--index-jog-speed", type=int, default=DEFAULT_INDEX_JOG_SPEED)
    parser.add_argument("--index-jog-ms", type=int, default=DEFAULT_INDEX_JOG_MS)
    parser.add_argument("--middle-jog-speed", type=int, default=DEFAULT_MIDDLE_JOG_SPEED)
    parser.add_argument("--middle-jog-ms", type=int, default=DEFAULT_MIDDLE_JOG_MS)
    parser.add_argument("--stop-speed", type=int, default=STOP_SPEED)
    parser.add_argument("--dry-run", action="store_true", help="Print commands without sending to the ESP32")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.thresholds:
        parts = [part.strip() for part in args.thresholds.split(",")]
        if len(parts) != 3:
            raise SystemExit("--thresholds must be index,middle,ring")
        args.index_threshold, args.middle_threshold, args.ring_threshold = (
            int(parts[0]),
            int(parts[1]),
            int(parts[2]),
        )

    thresholds = {
        "index": args.index_threshold,
        "middle": args.middle_threshold,
        "ring": args.ring_threshold,
    }

    serial_port = choose_serial_port(args.serial_port)
    car_ip = "dry-run"
    if not args.dry_run:
        car_ip = resolve_car_ip(args.car_ip)

    sender = ArmSender(car_ip, args.dry_run)
    last_status_at = 0.0
    previous_active = {"index": False, "middle": False, "ring": False}
    args.index_jog_speed = clamped_servo_value(args.index_jog_speed)
    args.middle_jog_speed = clamped_servo_value(args.middle_jog_speed)
    args.stop_speed = clamped_servo_value(args.stop_speed)
    args.index_jog_ms = max(0, args.index_jog_ms)
    args.middle_jog_ms = max(0, args.middle_jog_ms)

    print(f"Serial: {serial_port} @ {args.baud}")
    print(
        "Thresholds: "
        f"index>{thresholds['index']} middle<{thresholds['middle']} ring<{thresholds['ring']}"
    )
    print(
        f"Mapping: index bend edge=jog shoulder {args.index_jog_speed} {args.index_jog_ms}, "
        f"middle bend edge=jog shoulder {args.middle_jog_speed} {args.middle_jog_ms}, "
        "ring bent=close, ring relaxed=open."
    )
    print(
        f"Shoulder jogs: index={args.index_jog_speed} for {args.index_jog_ms}ms, "
        f"middle={args.middle_jog_speed} for {args.middle_jog_ms}ms, stop={args.stop_speed}"
    )
    print("Press Ctrl+C to stop.")

    with serial.Serial(serial_port, args.baud, timeout=0.2) as arduino:
        arduino.reset_input_buffer()
        try:
            while True:
                raw_line = arduino.readline().decode("utf-8", errors="replace").strip()
                if not raw_line:
                    continue

                values = parse_glove_line(raw_line)
                if values is None:
                    now = time.perf_counter()
                    if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                        print(f"serial: {raw_line}")
                        last_status_at = now
                    continue

                active = active_fingers(values, thresholds)
                grip_command, grip_label = gripper_command(active)
                arm_label = "ARM IDLE"

                if active["index"] and not previous_active["index"] and not active["middle"]:
                    sender.jog_shoulder(args.index_jog_speed, args.index_jog_ms, args.stop_speed)
                    arm_label = "INDEX JOG"
                elif active["middle"] and not previous_active["middle"] and not active["index"]:
                    sender.jog_shoulder(args.middle_jog_speed, args.middle_jog_ms, args.stop_speed)
                    arm_label = "MIDDLE JOG"
                elif active["index"] and active["middle"]:
                    arm_label = "ARM SKIP BOTH"

                sender.set_gripper(grip_command)
                previous_active = active

                now = time.perf_counter()
                if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                    print(
                        f"{format_values(values)} active {format_active(active)} "
                        f"-> {arm_label}, {grip_label}"
                    )
                    last_status_at = now

        except KeyboardInterrupt:
            print()
        finally:
            sender.send(f"set shoulder {args.stop_speed}")
            sender.send("open")
            print("Stopped.")


if __name__ == "__main__":
    main()
