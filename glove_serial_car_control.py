import argparse
import ipaddress
import json
import re
import socket
import time

import requests
import serial
import serial.tools.list_ports


DISCOVERY_PORT = 4210
DISCOVERY_MESSAGE = b"trashcar-discover"
CAR_STATUS_TEXT = '"name":"trashcar-esp32"'

BAUD_RATE = 115200
REQUEST_TIMEOUT_SECONDS = 3.0
DISCOVERY_TIMEOUT_SECONDS = 2.0
SEND_INTERVAL_SECONDS = 0.2
STATUS_INTERVAL_SECONDS = 0.5

INDEX_THRESHOLD = 630
MIDDLE_THRESHOLD = 420
RING_THRESHOLD = 380

FINGERS = ("index", "middle", "ring")
STOP_COMMAND = "2 0 2 0"
RAW_RE = re.compile(r"\bA([0-2])\s*=\s*(\d+)\b")


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


def list_ports():
    return list(serial.tools.list_ports.comports())


def choose_serial_port(port):
    if port.lower() != "auto":
        return port

    ports = list_ports()
    preferred = [
        item
        for item in ports
        if "arduino" in item.description.lower() or "mega" in item.description.lower()
    ]
    if preferred:
        return preferred[0].device
    if len(ports) == 1:
        return ports[0].device
    raise RuntimeError(
        "Could not auto-select a serial port. Use --serial-port COMx. "
        + "Ports: "
        + ", ".join(f"{item.device} ({item.description})" for item in ports)
    )


def parse_json_line(line):
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    if payload.get("name") != "trashcar-glove":
        return None

    values = {}
    for finger in FINGERS:
        try:
            values[finger] = int(payload[finger])
        except (KeyError, TypeError, ValueError):
            return None
    return values


def parse_raw_line(line):
    matches = {int(index): int(value) for index, value in RAW_RE.findall(line)}
    if not all(index in matches for index in (0, 1, 2)):
        return None
    return {
        "index": matches[0],
        "middle": matches[1],
        "ring": matches[2],
    }


def parse_glove_line(line):
    return parse_json_line(line) or parse_raw_line(line)


def active_fingers(values, thresholds):
    return {
        "index": values["index"] > thresholds["index"],
        "middle": values["middle"] < thresholds["middle"],
        "ring": values["ring"] < thresholds["ring"],
    }


def car_command(active, move_ms, turn_ms, slow):
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


class CarSender:
    def __init__(self, ip, dry_run):
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
        except requests.exceptions.RequestException as error:
            print(f"Car command failed: {error}")


def parse_args():
    parser = argparse.ArgumentParser(description="Read glove values from PC serial and control the car ESP32.")
    parser.add_argument("--serial-port", default="auto", help="Arduino serial port, or auto")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--car-ip", default="auto", help="Car ESP32 IP, or auto")
    parser.add_argument("--index-threshold", type=int, default=INDEX_THRESHOLD)
    parser.add_argument("--middle-threshold", type=int, default=MIDDLE_THRESHOLD)
    parser.add_argument("--ring-threshold", type=int, default=RING_THRESHOLD)
    parser.add_argument("--thresholds", help="Thresholds as index,middle,ring")
    parser.add_argument("--move-ms", type=int, default=180)
    parser.add_argument("--turn-ms", type=int, default=250)
    parser.add_argument("--send-interval", type=float, default=SEND_INTERVAL_SECONDS)
    parser.add_argument("--fast", action="store_true", help="Use full speed instead of slow forward")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without sending to the car")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_ports:
        for port in list_ports():
            print(f"{port.device}: {port.description}")
        return

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

    sender = CarSender(car_ip, args.dry_run)
    last_send_at = 0.0
    last_status_at = 0.0
    last_label = "STOP"

    print(f"Serial: {serial_port} @ {args.baud}")
    print(
        "Thresholds: "
        f"index>{thresholds['index']} middle<{thresholds['middle']} ring<{thresholds['ring']}"
    )
    print("Mapping: index=FORWARD, middle=LEFT, ring=RIGHT, multiple/none=STOP.")
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
                    if time.perf_counter() - last_status_at >= STATUS_INTERVAL_SECONDS:
                        print(f"serial: {raw_line}")
                        last_status_at = time.perf_counter()
                    continue

                active = active_fingers(values, thresholds)
                command, label = car_command(active, args.move_ms, args.turn_ms, not args.fast)
                now = time.perf_counter()

                if now - last_send_at >= args.send_interval:
                    sender.send(command)
                    last_send_at = now
                    last_label = label

                if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                    print(f"{format_values(values)} active {format_active(active)} -> {last_label}")
                    last_status_at = now

        except KeyboardInterrupt:
            print()
        finally:
            sender.send(STOP_COMMAND)
            print("Stopped.")


if __name__ == "__main__":
    main()
