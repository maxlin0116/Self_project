import argparse
import ipaddress
import os
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
from whole_bode import detect_index_control_gesture
from whole_bode import GestureStabilizer
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

RIGHT_FORWARD = "0"
RIGHT_BACKWARD = "1"
LEFT_FORWARD = "0"
LEFT_BACKWARD = "1"
STOP_COMMAND = "2 0 2 0"

MOVE_TIME_MS = 180
TURN_TIME_MS = 160


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
    health_url, _ = esp32_urls(ip)
    session = http_session()
    try:
        response = session.get(health_url, timeout=timeout_seconds)
        is_esp32 = ESP32_ROOT_TEXT in response.text
        if require_esp32 and not is_esp32:
            return False, f"HTTP online but not ESP32 receiver: {health_url}"
        label = "ESP32 online" if is_esp32 else "HTTP online"
        return True, f"{label}: {health_url} -> HTTP {response.status_code}"
    except requests.exceptions.RequestException as error:
        return False, f"ESP32 offline: {health_url} -> {error}"


def private_ipv4_networks():
    networks = {ipaddress.ip_network("192.168.137.0/24")}

    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return networks

    for match in re.finditer(r"IPv4 Address[^\n:]*:\s*([0-9.]+)", result.stdout):
        ip = ipaddress.ip_address(match.group(1))
        if ip.is_private:
            networks.add(ipaddress.ip_network(f"{ip}/24", strict=False))

    return networks


def discover_esp32_ip():
    candidates = []
    own_ips = {socket.gethostbyname(socket.gethostname())}
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


def is_visible(landmark):
    visibility_ok = landmark.visibility is None or landmark.visibility >= 0.5
    presence_ok = landmark.presence is None or landmark.presence >= 0.5
    return visibility_ok and presence_ok


def turn_left_command():
    return f"{RIGHT_FORWARD} {TURN_TIME_MS} {LEFT_BACKWARD} {TURN_TIME_MS}"


def turn_right_command():
    return f"{RIGHT_BACKWARD} {TURN_TIME_MS} {LEFT_FORWARD} {TURN_TIME_MS}"


def move_forward_command():
    return f"{RIGHT_FORWARD} {MOVE_TIME_MS} {LEFT_FORWARD} {MOVE_TIME_MS}"


def build_motor_command(results, finger_stabilizer=None):
    pose = results.pose_landmarks
    left_hand_raised = False
    right_hand_raised = False
    stable_finger = None

    if pose and len(pose) >= 17:
        left_shoulder = pose[11]
        right_shoulder = pose[12]
        left_wrist = pose[15]
        right_wrist = pose[16]

        if all(
            is_visible(point)
            for point in (left_shoulder, right_shoulder, left_wrist, right_wrist)
        ):
            left_hand_raised = left_wrist.y < left_shoulder.y - 0.05
            right_hand_raised = right_wrist.y < right_shoulder.y - 0.05

    if finger_stabilizer is not None:
        stable_finger = finger_stabilizer.update(
            detect_index_control_gesture(results)
        )

    if stable_finger == "both_index":
        return move_forward_command(), "FORWARD BOTH INDEX"

    if stable_finger == "left_index":
        return turn_left_command(), "LEFT INDEX"

    if stable_finger == "right_index":
        return turn_right_command(), "RIGHT INDEX"

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

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {CAMERA_INDEX}. "
            "Try CAMERA_INDEX = 0 if this camera is not available."
        )

    sender, esp32_status = create_sender(args.ip, offline=args.offline)
    writer = None
    last_send_time = 0.0
    start_time = time.perf_counter()
    finger_stabilizer = GestureStabilizer()

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

                command, command_label = build_motor_command(
                    results,
                    finger_stabilizer,
                )
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
