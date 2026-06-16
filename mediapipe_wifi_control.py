import argparse
import ipaddress
import os
import sys
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import mediapipe as mp
import requests

from whole_bode import create_holistic_landmarker
from whole_bode import draw_holistic_landmarks
from whole_bode import ensure_model_file


CAMERA_INDEX = 0
ESP32_IP = "auto"
OUTPUT_FILE = "output_wifi_tracking.mp4"
FPS = 20.0

REQUEST_TIMEOUT_SECONDS = 0.5
IP_TEST_TIMEOUT_SECONDS = 2.0
IP_SCAN_TIMEOUT_SECONDS = 0.35
SEND_INTERVAL_SECONDS = 0.2
ESP32_ROOT_TEXT = "Send POST data"
ESP32_STATUS_TEXT = "\"name\":\"trashcar-esp32\""
DISCOVERY_PORT = 4210
DISCOVERY_MESSAGE = b"trashcar-discover"

RIGHT_FORWARD = "0"
RIGHT_BACKWARD = "1"
LEFT_FORWARD = "0"
LEFT_BACKWARD = "1"
RIGHT_SLOW_FORWARD = "3"
LEFT_SLOW_FORWARD = "3"
STOP_COMMAND = "2 0 2 0"

MOVE_TIME_MS = 260
TURN_TIME_MS = 120
HAND_RAISED_MARGIN = 0.05


class OfflineSender:
    def send(self, command):
        pass

    def close(self):
        pass


class Esp32Sender:
    def __init__(self, url, timeout_seconds):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.trust_env = False
        self.latest_command = None
        self.lock = threading.Lock()
        self.command_event = threading.Event()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def send(self, command):
        with self.lock:
            self.latest_command = command
        self.command_event.set()

    def close(self):
        self.stop_event.set()
        self.command_event.set()
        self.thread.join(timeout=1.0)
        self._post(STOP_COMMAND)

    def _run(self):
        while not self.stop_event.is_set():
            self.command_event.wait(timeout=0.1)
            self.command_event.clear()

            with self.lock:
                command = self.latest_command
                self.latest_command = None

            if command is not None:
                self._post(command)

    def _post(self, command):
        try:
            print(f"POST {self.url}: {command}")
            self.session.post(
                self.url,
                data=command.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.RequestException as error:
            print(f"ESP32 request failed: {error}")


def esp32_urls(ip):
    return f"http://{ip}/", f"http://{ip}/data"


def http_session():
    session = requests.Session()
    session.trust_env = False
    return session


def test_esp32_ip(ip, timeout_seconds=IP_TEST_TIMEOUT_SECONDS, require_esp32=False):
    health_url = f"http://{ip}/status"
    session = http_session()
    try:
        response = session.get(health_url, timeout=timeout_seconds)
        is_esp32 = ESP32_STATUS_TEXT in response.text
        if require_esp32 and not is_esp32:
            return False, f"HTTP online but not ESP32 receiver: {health_url}"
        label = "ESP32 online" if is_esp32 else "HTTP online"
        return True, f"{label}: {health_url} -> HTTP {response.status_code}"
    except requests.exceptions.RequestException as error:
        return False, f"ESP32 offline: {health_url} -> {error}"


def local_ipv4_addresses():
    addresses = set()
    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return addresses

    for match in re.finditer(r"IPv4 Address[^\n:]*:\s*([0-9.]+)", result.stdout):
        addresses.add(match.group(1))
    return addresses


def private_ipv4_networks():
    networks = {ipaddress.ip_network("192.168.137.0/24")}

    for address in local_ipv4_addresses():
        ip = ipaddress.ip_address(address)
        if ip.is_private:
            networks.add(ipaddress.ip_network(f"{ip}/24", strict=False))

    return networks


def discover_esp32_udp(timeout_seconds=1.5):
    targets = {"255.255.255.255"}
    for network in private_ipv4_networks():
        targets.add(str(network.broadcast_address))

    print("Trying ESP32 UDP discovery...")
    found = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.settimeout(timeout_seconds)

        for target in targets:
            try:
                udp.sendto(DISCOVERY_MESSAGE, (target, DISCOVERY_PORT))
            except OSError:
                continue

        deadline = time.perf_counter() + timeout_seconds
        while time.perf_counter() < deadline:
            try:
                data, address = udp.recvfrom(256)
            except socket.timeout:
                break
            except OSError:
                break

            message = data.decode("utf-8", errors="replace").strip()
            if message.startswith("trashcar-esp32"):
                ip = address[0]
                found.add(ip)
                print(f"UDP discovery response: {message} from {ip}")

    for ip in found:
        is_online, message = test_esp32_ip(ip, require_esp32=True)
        print(message)
        if is_online:
            return ip

    return None


def discover_esp32_ip():
    ip = discover_esp32_udp()
    if ip is not None:
      return ip

    candidates = []
    own_ips = local_ipv4_addresses()
    candidates.append("192.168.4.1")
    for network in private_ipv4_networks():
        for ip in network.hosts():
            ip_text = str(ip)
            if ip_text not in own_ips:
                candidates.append(ip_text)

    print("Searching for ESP32 on local private networks...")
    with ThreadPoolExecutor(max_workers=80) as executor:
        futures = {
            executor.submit(
                test_esp32_ip,
                ip,
                IP_SCAN_TIMEOUT_SECONDS,
                True,
            ): ip
            for ip in candidates
        }
        for future in as_completed(futures):
            is_online, message = future.result()
            if is_online:
                ip = futures[future]
                print(message)
                return ip

    print("No ESP32 receiver found.")
    return None


def create_sender(ip, offline=False):
    if offline:
        return OfflineSender(), "ESP32 OFFLINE MODE"

    if ip.lower() == "auto":
        ip = discover_esp32_ip()
        if ip is None:
            print("Using offline mode. MediaPipe will still run without ESP32.")
            return OfflineSender(), "ESP32 OFFLINE"

    is_online, message = test_esp32_ip(ip)
    print(message)
    if not is_online:
        print("Using offline mode. MediaPipe will still run without ESP32.")
        return OfflineSender(), "ESP32 OFFLINE"

    _, data_url = esp32_urls(ip)
    return Esp32Sender(data_url, REQUEST_TIMEOUT_SECONDS), f"ESP32 ONLINE {ip}"


def camera_backend_id(name):
    normalized = name.lower()
    if normalized == "any":
        return cv2.CAP_ANY
    if normalized == "msmf":
        return cv2.CAP_MSMF
    if normalized == "dshow":
        return cv2.CAP_DSHOW
    raise ValueError(f"Unknown camera backend: {name}")


def default_camera_backend():
    if sys.platform.startswith("win"):
        return "msmf"
    return "any"


def is_visible(landmark):
    visibility_ok = landmark.visibility is None or landmark.visibility >= 0.5
    presence_ok = landmark.presence is None or landmark.presence >= 0.5
    return visibility_ok and presence_ok


def turn_left_command():
    return f"{RIGHT_SLOW_FORWARD} {TURN_TIME_MS} 2 0"


def turn_right_command():
    return f"2 0 {LEFT_SLOW_FORWARD} {TURN_TIME_MS}"


def move_forward_command():
    return f"{RIGHT_SLOW_FORWARD} {MOVE_TIME_MS} {LEFT_SLOW_FORWARD} {MOVE_TIME_MS}"


def build_motor_command(results):
    pose = results.pose_landmarks
    left_hand_raised = False
    right_hand_raised = False

    if pose and len(pose) >= 17:
        # The preview frame is mirrored before MediaPipe sees it, so swap
        # landmark sides back to match the user's real left/right hands.
        left_shoulder = pose[12]
        right_shoulder = pose[11]
        left_wrist = pose[16]
        right_wrist = pose[15]

        if all(
            is_visible(point)
            for point in (left_shoulder, right_shoulder, left_wrist, right_wrist)
        ):
            left_hand_raised = left_wrist.y < left_shoulder.y - HAND_RAISED_MARGIN
            right_hand_raised = right_wrist.y < right_shoulder.y - HAND_RAISED_MARGIN

        shoulder_y = min(left_shoulder.y, right_shoulder.y)
        shoulder_center_x = (left_shoulder.x + right_shoulder.x) / 2.0

        for hand_landmarks in (results.left_hand_landmarks, results.right_hand_landmarks):
            if hand_landmarks:
                wrist = hand_landmarks[0]
                middle_knuckle = hand_landmarks[9]
                hand_is_raised = (
                    wrist.y < shoulder_y - HAND_RAISED_MARGIN or
                    middle_knuckle.y < shoulder_y - HAND_RAISED_MARGIN
                )
                if hand_is_raised:
                    # The image is mirrored before detection. A hand on the left side
                    # of the displayed image is the user's left-hand command.
                    if wrist.x < shoulder_center_x:
                        left_hand_raised = True
                    else:
                        right_hand_raised = True

    if left_hand_raised and right_hand_raised:
        return move_forward_command(), "FORWARD"

    if left_hand_raised:
        return turn_left_command(), "LEFT"

    if right_hand_raised:
        return turn_right_command(), "RIGHT"

    return STOP_COMMAND, "STOP"


def put_status(image, command_label, command, esp32_status):
    cv2.putText(
        image,
        f"{command_label}: {command}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        esp32_status,
        (20, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0) if "ONLINE" in esp32_status else (0, 0, 255),
        2,
        cv2.LINE_AA,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ip",
        default=ESP32_IP,
        help="ESP32 IP address, or auto to search local private networks",
    )
    parser.add_argument(
        "--test-ip",
        action="store_true",
        help="Only test ESP32 HTTP connection and exit",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run MediaPipe without sending commands to ESP32",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=CAMERA_INDEX,
        help="OpenCV camera index",
    )
    parser.add_argument(
        "--camera-backend",
        choices=("any", "msmf", "dshow"),
        default=default_camera_backend(),
        help="OpenCV camera backend",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.test_ip:
        if args.ip.lower() == "auto":
            ip = discover_esp32_ip()
            raise SystemExit(0 if ip is not None else 1)
        is_online, message = test_esp32_ip(args.ip, require_esp32=True)
        print(message)
        raise SystemExit(0 if is_online else 1)

    ensure_model_file()

    cap = cv2.VideoCapture(args.camera_index, camera_backend_id(args.camera_backend))
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {args.camera_index}. "
            "Try --camera-index 1 if this camera is not available."
        )

    sender, esp32_status = create_sender(args.ip, offline=args.offline)
    writer = None
    last_send_time = 0.0
    start_time = time.perf_counter()

    try:
        with create_holistic_landmarker() as holistic:
            while True:
                success, image = cap.read()
                if not success:
                    print("Could not read a frame from the camera.")
                    break

                image = cv2.flip(image, 1)

                if writer is None:
                    frame_height, frame_width = image.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(
                        OUTPUT_FILE,
                        fourcc,
                        FPS,
                        (frame_width, frame_height),
                    )

                    if not writer.isOpened():
                        raise RuntimeError(f"Could not create video file: {OUTPUT_FILE}")

                rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                results = holistic.detect_for_video(mp_image, timestamp_ms)

                command, command_label = build_motor_command(results)
                now = time.perf_counter()
                if now - last_send_time >= SEND_INTERVAL_SECONDS:
                    sender.send(command)
                    last_send_time = now

                draw_holistic_landmarks(image, results)
                put_status(image, command_label, command, esp32_status)

                writer.write(image)
                cv2.imshow("Holistic Tracking + ESP32", image)

                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        sender.close()
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
