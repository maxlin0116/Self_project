import argparse
import os
import sys
import threading
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import mediapipe as mp
import requests

from arm_wifi_control import create_session
from arm_wifi_control import read_mega_log
from arm_wifi_control import send_command
from mediapipe_wifi_control import CAMERA_INDEX
from mediapipe_wifi_control import FPS
from mediapipe_wifi_control import OUTPUT_FILE
from mediapipe_wifi_control import REQUEST_TIMEOUT_SECONDS
from mediapipe_wifi_control import SEND_INTERVAL_SECONDS
from mediapipe_wifi_control import STOP_COMMAND
from mediapipe_wifi_control import build_motor_command
from mediapipe_wifi_control import camera_backend_id
from mediapipe_wifi_control import default_camera_backend
from mediapipe_wifi_control import discover_esp32_ip
from mediapipe_wifi_control import ensure_model_file
from mediapipe_wifi_control import put_status
from mediapipe_wifi_control import test_esp32_ip
from whole_bode import create_holistic_landmarker
from whole_bode import draw_holistic_landmarks


def resolve_esp32_ip(ip):
    if ip.lower() == "auto":
        found = discover_esp32_ip()
        if found is None:
            raise RuntimeError("Could not find ESP32. Try --ip <address>.")
        return found

    is_online, message = test_esp32_ip(ip, require_esp32=True)
    print(message)
    if not is_online:
        raise RuntimeError(f"ESP32 is offline: {ip}")
    return ip


def print_arm_help():
    print()
    print("Arm commands:")
    print("  jog shoulder 110 80")
    print("  jog shoulder 20 100")
    print("  set shoulder 90")
    print("  open")
    print("  close")
    print("  stop all")
    print("  log")
    print("  help")
    print("  quit")
    print()


class ArmConsole:
    def __init__(self, ip, confirm):
        self.ip = ip
        self.confirm = confirm
        self.session = create_session()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        print_arm_help()
        self.thread.start()

    def stop_requested(self):
        return self.stop_event.is_set()

    def close(self):
        self.stop_event.set()
        try:
            send_command(self.session, self.ip, "stop all", False)
        except requests.exceptions.RequestException as error:
            print(f"Arm stop failed: {error}")

    def _run(self):
        while not self.stop_event.is_set():
            try:
                raw = input("arm> ").strip()
            except EOFError:
                self.stop_event.set()
                break
            except KeyboardInterrupt:
                self.stop_event.set()
                break

            if not raw:
                continue

            action = raw.split()[0].lower()
            if action in ("quit", "exit"):
                self.stop_event.set()
                break
            if action == "help":
                print_arm_help()
                continue
            if action == "log":
                try:
                    print(f"Mega: {read_mega_log(self.session, self.ip)}")
                except requests.exceptions.RequestException as error:
                    print(f"Log failed: {error}")
                continue

            try:
                send_command(self.session, self.ip, raw, self.confirm)
            except ValueError:
                print("Command value must be a number.")
            except requests.exceptions.RequestException as error:
                print(f"Arm command failed: {error}")


class QuietWheelSender:
    def __init__(self, ip):
        self.url = f"http://{ip}/data"
        self.session = requests.Session()
        self.session.trust_env = False
        self.latest_command = None
        self.last_error_at = 0.0
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
            self.session.post(
                self.url,
                data=command.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as error:
            now = time.perf_counter()
            if now - self.last_error_at >= 2.0:
                print(f"Wheel command failed: {error}")
                self.last_error_at = now


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run camera wheel control and type arm commands at the same time."
    )
    parser.add_argument("--ip", default="auto", help="ESP32 IP address, or auto")
    parser.add_argument("--camera-index", type=int, default=CAMERA_INDEX)
    parser.add_argument(
        "--camera-backend",
        choices=("any", "msmf", "dshow"),
        default=default_camera_backend(),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Wait for Mega log confirmation after typed arm commands",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    esp32_ip = resolve_esp32_ip(args.ip)

    ensure_model_file()
    cap = cv2.VideoCapture(args.camera_index, camera_backend_id(args.camera_backend))
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {args.camera_index}. "
            "Try --camera-index 1 or --camera-backend dshow."
        )

    wheel_sender = QuietWheelSender(esp32_ip)
    esp32_status = f"ESP32 ONLINE {esp32_ip}"
    arm_console = ArmConsole(esp32_ip, args.confirm)
    arm_console.start()

    writer = None
    last_send_time = 0.0
    last_wheel_command = None
    start_time = time.perf_counter()

    print(f"ESP32: http://{esp32_ip}")
    print("Camera: left hand=LEFT, right hand=RIGHT, both hands=FORWARD, no hands=STOP.")
    print("Type arm commands in this same PowerShell window. Press q/ESC in camera window to quit.")

    try:
        with create_holistic_landmarker() as holistic:
            while not arm_console.stop_requested():
                success, image = cap.read()
                if not success:
                    print("Could not read a frame from the camera.")
                    break

                image = cv2.flip(image, 1)

                if writer is None:
                    frame_height, frame_width = image.shape[:2]
                    writer = cv2.VideoWriter(
                        OUTPUT_FILE,
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        FPS,
                        (frame_width, frame_height),
                    )

                rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                results = holistic.detect_for_video(mp_image, timestamp_ms)

                command, command_label = build_motor_command(results)
                now = time.perf_counter()
                should_send = now - last_send_time >= SEND_INTERVAL_SECONDS
                if command == STOP_COMMAND and last_wheel_command == STOP_COMMAND:
                    should_send = False
                if should_send:
                    wheel_sender.send(command)
                    last_send_time = now
                    last_wheel_command = command

                draw_holistic_landmarks(image, results)
                put_status(image, command_label, command, esp32_status)

                if writer is not None and writer.isOpened():
                    writer.write(image)

                cv2.imshow("Camera Wheel + Arm Console", image)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    finally:
        arm_console.close()
        wheel_sender.send(STOP_COMMAND)
        wheel_sender.close()
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(0)
