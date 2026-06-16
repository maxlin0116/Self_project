import argparse
import ipaddress
import json
import socket
import time

import requests


GLOVE_PORT = 4211
DISCOVERY_PORT = 4210
DISCOVERY_MESSAGE = b"trashcar-discover"
CAR_STATUS_TEXT = '"name":"trashcar-esp32"'

REQUEST_TIMEOUT_SECONDS = 3.0
DISCOVERY_TIMEOUT_SECONDS = 2.0
GLOVE_TIMEOUT_SECONDS = 1.0
CALIBRATION_SECONDS = 2.0
ACTIVE_DELTA = 350
DEFAULT_INDEX_THRESHOLD = 630
DEFAULT_MIDDLE_THRESHOLD = 400
DEFAULT_RING_THRESHOLD = 380
SEND_INTERVAL_SECONDS = 0.2
STATUS_INTERVAL_SECONDS = 0.5

FINGERS = ("index", "middle", "ring")
DEFAULT_FIXED_DIRECTIONS = {
    "index": "increase",
    "middle": "decrease",
    "ring": "decrease",
}
STOP_COMMAND = "2 0 2 0"


def private_ipv4_broadcasts():
    broadcasts = {"255.255.255.255"}
    try:
        host_name = socket.gethostname()
        for info in socket.getaddrinfo(host_name, None, family=socket.AF_INET):
            ip_text = info[4][0]
            ip = ipaddress.ip_address(ip_text)
            if ip.is_private and not ip.is_loopback:
                network = ipaddress.ip_network(f"{ip_text}/24", strict=False)
                broadcasts.add(str(network.broadcast_address))
    except OSError:
        pass
    return broadcasts


def discover_car_ip(timeout_seconds=DISCOVERY_TIMEOUT_SECONDS):
    found = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.settimeout(0.2)

        for target in private_ipv4_broadcasts():
            try:
                udp.sendto(DISCOVERY_MESSAGE, (target, DISCOVERY_PORT))
            except OSError:
                continue

        deadline = time.perf_counter() + timeout_seconds
        while time.perf_counter() < deadline:
            try:
                data, address = udp.recvfrom(1024)
            except socket.timeout:
                continue

            message = data.decode("utf-8", errors="replace").strip()
            if message.startswith("trashcar-esp32"):
                found.add(address[0])
                print(f"Car discovery: {message} from {address[0]}")

    for ip in found:
        if test_car_ip(ip):
            return ip
    return None


def test_car_ip(ip):
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(f"http://{ip}/status", timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as error:
        print(f"Car offline: http://{ip}/status -> {error}")
        return False

    if CAR_STATUS_TEXT not in response.text:
        print(f"HTTP online but not trashcar ESP32: http://{ip}/status")
        return False

    print(f"Car online: http://{ip}/status -> HTTP {response.status_code}")
    return True


def resolve_car_ip(car_ip):
    if car_ip.lower() != "auto":
        if not test_car_ip(car_ip):
            raise RuntimeError(f"Car ESP32 is offline: {car_ip}")
        return car_ip

    print("Searching for car ESP32...")
    found = discover_car_ip()
    if found is None:
        raise RuntimeError("Could not find trashcar-esp32.")
    return found


class CarSender:
    def __init__(self, ip, dry_run=False):
        self.url = f"http://{ip}/data"
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.trust_env = False

    def send(self, command):
        if self.dry_run:
            print(f"DRY RUN car command: {command}")
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
            print(f"Car command failed: {error}")


class GloveCalibrator:
    def __init__(
        self,
        calibration_seconds,
        active_delta,
        direction,
        fixed_thresholds=None,
        fixed_directions=None,
    ):
        self.calibration_seconds = calibration_seconds
        self.active_delta = active_delta
        self.direction = direction
        self.fixed_thresholds = fixed_thresholds
        self.fixed_directions = fixed_directions or DEFAULT_FIXED_DIRECTIONS
        self.started_at = time.perf_counter()
        self.samples = []
        self.baseline = None

    def update(self, values):
        if self.fixed_thresholds is not None:
            active = {}
            for finger in FINGERS:
                if self.fixed_directions[finger] == "decrease":
                    active[finger] = values[finger] < self.fixed_thresholds[finger]
                else:
                    active[finger] = values[finger] > self.fixed_thresholds[finger]
            return active, "FIXED"

        if self.baseline is None:
            self.samples.append(values.copy())
            elapsed = time.perf_counter() - self.started_at
            if elapsed < self.calibration_seconds:
                return None, f"CALIBRATING {elapsed:.1f}/{self.calibration_seconds:.1f}s"

            self.baseline = {
                finger: sum(sample[finger] for sample in self.samples) / len(self.samples)
                for finger in FINGERS
            }
            print(
                "Baseline: "
                + " ".join(f"{finger}={self.baseline[finger]:.0f}" for finger in FINGERS)
            )

        active = {}
        for finger in FINGERS:
            delta = values[finger] - self.baseline[finger]
            if self.direction == "decrease":
                delta = -delta
            active[finger] = delta > self.active_delta
        return active, "READY"


def parse_glove_packet(data):
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if payload.get("name") != "trashcar-glove":
        return None

    values = {}
    for finger in FINGERS:
        try:
            values[finger] = int(payload[finger])
        except (KeyError, TypeError, ValueError):
            values[finger] = 0
    return values


def glove_command(active, move_ms, turn_ms, slow):
    active_count = sum(1 for value in active.values() if value)
    direction = "3" if slow else "0"

    if active_count != 1:
        return STOP_COMMAND, "STOP"
    if active["index"]:
        return f"{direction} {move_ms} {direction} {move_ms}", "FORWARD"
    if active["middle"]:
        return f"{direction} {turn_ms} 2 0", "LEFT"
    if active["ring"]:
        return f"2 0 {direction} {turn_ms}", "RIGHT"
    return STOP_COMMAND, "STOP"


def format_values(values):
    return " ".join(f"{finger}:{values[finger]}" for finger in FINGERS)


def format_active(active):
    return " ".join(f"{finger}:{'1' if active[finger] else '0'}" for finger in FINGERS)


def parse_args():
    parser = argparse.ArgumentParser(description="Test glove-only car control.")
    parser.add_argument("--car-ip", default="auto", help="Car ESP32 IP, or auto")
    parser.add_argument("--glove-port", type=int, default=GLOVE_PORT)
    parser.add_argument("--active-delta", type=int, default=ACTIVE_DELTA)
    parser.add_argument(
        "--direction",
        choices=("increase", "decrease", "mixed"),
        default="mixed",
        help="Direction for fixed thresholds; mixed means index increases, middle/ring decrease",
    )
    parser.add_argument("--calibration-seconds", type=float, default=CALIBRATION_SECONDS)
    parser.add_argument(
        "--fixed-threshold",
        action="store_true",
        help="Skip calibration and use fixed raw thresholds",
    )
    parser.add_argument("--index-threshold", type=int, default=DEFAULT_INDEX_THRESHOLD)
    parser.add_argument("--middle-threshold", type=int, default=DEFAULT_MIDDLE_THRESHOLD)
    parser.add_argument("--ring-threshold", type=int, default=DEFAULT_RING_THRESHOLD)
    parser.add_argument(
        "--thresholds",
        help="Fixed thresholds as index,middle,ring; overrides individual threshold flags",
    )
    parser.add_argument("--send-interval", type=float, default=SEND_INTERVAL_SECONDS)
    parser.add_argument("--move-ms", type=int, default=180)
    parser.add_argument("--turn-ms", type=int, default=250)
    parser.add_argument("--fast", action="store_true", help="Use full speed instead of slow forward")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without sending to the car")
    return parser.parse_args()


def main():
    args = parse_args()

    car_ip = "dry-run"
    if not args.dry_run:
        car_ip = resolve_car_ip(args.car_ip)

    sender = CarSender(car_ip, dry_run=args.dry_run)
    fixed_thresholds = None
    fixed_directions = None
    if args.fixed_threshold:
        if args.thresholds:
            parts = [part.strip() for part in args.thresholds.split(",")]
            if len(parts) != len(FINGERS):
                raise SystemExit("--thresholds must be index,middle,ring")
            args.index_threshold, args.middle_threshold, args.ring_threshold = (
                int(parts[0]),
                int(parts[1]),
                int(parts[2]),
            )

        fixed_thresholds = {
            "index": args.index_threshold,
            "middle": args.middle_threshold,
            "ring": args.ring_threshold,
        }
        if args.direction == "mixed":
            fixed_directions = DEFAULT_FIXED_DIRECTIONS.copy()
        else:
            fixed_directions = {finger: args.direction for finger in FINGERS}

        print(
            "Fixed thresholds: "
            + " ".join(
                f"{finger}{'<' if fixed_directions[finger] == 'decrease' else '>'}"
                f"{fixed_thresholds[finger]}"
                for finger in FINGERS
            )
        )

    calibrator = GloveCalibrator(
        args.calibration_seconds,
        args.active_delta,
        args.direction,
        fixed_thresholds,
        fixed_directions,
    )

    print(f"Listening for glove UDP packets on port {args.glove_port}.")
    if args.fixed_threshold:
        print("Using fixed thresholds; calibration is skipped.")
    else:
        print("Keep your hand relaxed during calibration.")
    print("Mapping: index=FORWARD, middle=LEFT, ring=RIGHT, multiple/none=STOP.")
    print("Press Ctrl+C to stop.")

    last_packet_at = 0.0
    last_send_at = 0.0
    last_status_at = 0.0
    last_label = "STOP"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("", args.glove_port))
        udp.settimeout(0.2)

        try:
            while True:
                now = time.perf_counter()
                values = None

                try:
                    data, address = udp.recvfrom(1024)
                    values = parse_glove_packet(data)
                    if values is not None:
                        last_packet_at = now
                except socket.timeout:
                    pass

                if values is None:
                    if last_packet_at and now - last_packet_at > GLOVE_TIMEOUT_SECONDS:
                        if now - last_send_at >= args.send_interval:
                            sender.send(STOP_COMMAND)
                            last_send_at = now
                        if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                            print("GLOVE timeout -> STOP")
                            last_status_at = now
                    continue

                active, status = calibrator.update(values)
                if active is None:
                    if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                        print(f"{address[0]} {format_values(values)} {status}")
                        last_status_at = now
                    continue

                command, label = glove_command(active, args.move_ms, args.turn_ms, not args.fast)
                if now - last_send_at >= args.send_interval:
                    sender.send(command)
                    last_send_at = now
                    last_label = label

                if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                    print(
                        f"{address[0]} {format_values(values)} "
                        f"active {format_active(active)} -> {last_label}"
                    )
                    last_status_at = now

        except KeyboardInterrupt:
            print()
        finally:
            sender.send(STOP_COMMAND)
            print("Stopped.")


if __name__ == "__main__":
    main()
