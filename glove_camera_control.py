import argparse
import json
import os
import queue
import socket
import threading
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import mediapipe as mp
import requests

from mediapipe_wifi_control import FPS
from mediapipe_wifi_control import OUTPUT_FILE
from mediapipe_wifi_control import REQUEST_TIMEOUT_SECONDS
from mediapipe_wifi_control import SEND_INTERVAL_SECONDS
from mediapipe_wifi_control import STOP_COMMAND
from mediapipe_wifi_control import build_motor_command
from mediapipe_wifi_control import camera_backend_id
from mediapipe_wifi_control import create_sender
from mediapipe_wifi_control import default_camera_backend
from mediapipe_wifi_control import discover_esp32_ip
from mediapipe_wifi_control import ensure_model_file
from mediapipe_wifi_control import test_esp32_ip
from whole_bode import create_holistic_landmarker
from whole_bode import draw_holistic_landmarks


GLOVE_PORT = 4211
GLOVE_TIMEOUT_SECONDS = 1.0
GLOVE_CALIBRATION_SECONDS = 2.0
GLOVE_ACTIVE_DELTA = 350
ARM_INTERVAL_SECONDS = 0.35

FINGERS = ("index", "middle", "ring")


class CommandPoster:
    def __init__(self, ip):
        self.url = f"http://{ip}/data"
        self.session = requests.Session()
        self.session.trust_env = False
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def send(self, command):
        self.commands.put(command)

    def close(self):
        self.stop_event.set()
        self.commands.put(STOP_COMMAND)
        self.thread.join(timeout=1.0)

    def _run(self):
        while not self.stop_event.is_set() or not self.commands.empty():
            try:
                command = self.commands.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                print(f"POST {self.url}: {command}")
                self.session.post(
                    self.url,
                    data=command.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.exceptions.RequestException as error:
                print(f"ESP32 request failed: {error}")


class GloveReceiver:
    def __init__(self, port):
        self.port = port
        self.latest = None
        self.latest_from = None
        self.last_seen = 0.0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def snapshot(self):
        with self.lock:
            return self.latest, self.latest_from, self.last_seen

    def _run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind(("", self.port))
            udp.settimeout(0.2)

            while not self.stop_event.is_set():
                try:
                    data, address = udp.recvfrom(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break

                try:
                    payload = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                if payload.get("name") != "trashcar-glove":
                    continue

                values = {}
                for finger in FINGERS:
                    try:
                        values[finger] = int(payload[finger])
                    except (KeyError, TypeError, ValueError):
                        values[finger] = 0

                with self.lock:
                    self.latest = values
                    self.latest_from = address[0]
                    self.last_seen = time.perf_counter()


class GloveMapper:
    def __init__(self, active_delta):
        self.active_delta = active_delta
        self.baseline = None
        self.samples = []
        self.calibration_started = time.perf_counter()
        self.base_angle = 90
        self.gripper_closed = False
        self.last_command = ""

    def update(self, values):
        if values is None:
            return None, "NO GLOVE"

        now = time.perf_counter()
        if self.baseline is None:
            self.samples.append(values.copy())
            if now - self.calibration_started < GLOVE_CALIBRATION_SECONDS:
                return None, "CALIBRATING"
            self.baseline = {
                finger: sum(sample[finger] for sample in self.samples) / len(self.samples)
                for finger in FINGERS
            }
            return None, "READY"

        active = {
            finger: values[finger] - self.baseline[finger] > self.active_delta
            for finger in FINGERS
        }

        command = None
        label = "IDLE"

        if active["index"] and active["middle"]:
            command = "move shoulder 500 10"
            label = "ARM UP"
        elif active["index"]:
            command = "reach forward 250"
            label = "ARM FORWARD"
        elif active["middle"]:
            command = "reach down 250"
            label = "ARM DOWN"
        elif active["ring"]:
            command = "close" if not self.gripper_closed else "open"
            label = "GRIP CLOSE" if not self.gripper_closed else "GRIP OPEN"
            self.gripper_closed = not self.gripper_closed

        if command == self.last_command:
            return None, label

        self.last_command = command or ""
        return command, label


def put_overlay(image, wheel_label, wheel_command, arm_label, glove_status, esp32_status):
    rows = [
        f"WHEEL {wheel_label}: {wheel_command}",
        f"ARM {arm_label}",
        glove_status,
        esp32_status,
    ]
    for index, text in enumerate(rows):
        color = (0, 255, 255) if index < 2 else (0, 255, 0)
        cv2.putText(
            image,
            text,
            (20, 40 + index * 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )


def resolve_car_ip(ip):
    if ip.lower() == "auto":
        found = discover_esp32_ip()
        if found is None:
            raise RuntimeError("Could not find car ESP32.")
        return found

    is_online, message = test_esp32_ip(ip, require_esp32=True)
    print(message)
    if not is_online:
        raise RuntimeError(f"Car ESP32 is offline: {ip}")
    return ip


def parse_args():
    parser = argparse.ArgumentParser(description="Glove arm control plus camera wheel control.")
    parser.add_argument("--car-ip", default="auto", help="Car ESP32 IP, or auto")
    parser.add_argument("--glove-port", type=int, default=GLOVE_PORT)
    parser.add_argument("--active-delta", type=int, default=GLOVE_ACTIVE_DELTA)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument(
        "--camera-backend",
        choices=("any", "msmf", "dshow"),
        default=default_camera_backend(),
    )
    parser.add_argument("--no-camera", action="store_true", help="Only test glove to arm.")
    return parser.parse_args()


def main():
    args = parse_args()
    car_ip = resolve_car_ip(args.car_ip)
    arm_sender = CommandPoster(car_ip)
    wheel_sender, esp32_status = create_sender(car_ip, offline=args.no_camera)

    glove = GloveReceiver(args.glove_port)
    mapper = GloveMapper(args.active_delta)
    glove.start()

    last_wheel_send = 0.0
    last_arm_send = 0.0
    arm_label = "IDLE"
    start_time = time.perf_counter()
    writer = None
    cap = None

    try:
        ensure_model_file()
        if not args.no_camera:
            cap = cv2.VideoCapture(args.camera_index, camera_backend_id(args.camera_backend))
            if not cap.isOpened():
                raise RuntimeError(f"Could not open camera index {args.camera_index}.")

        with create_holistic_landmarker() as holistic:
            while True:
                values, glove_ip, glove_seen = glove.snapshot()
                command, arm_label = mapper.update(values)
                now = time.perf_counter()
                if command and now - last_arm_send >= ARM_INTERVAL_SECONDS:
                    arm_sender.send(command)
                    last_arm_send = now

                if values is None or now - glove_seen > GLOVE_TIMEOUT_SECONDS:
                    glove_status = "GLOVE offline"
                else:
                    compact = " ".join(f"{key}:{values[key]}" for key in FINGERS)
                    glove_status = f"GLOVE {glove_ip} {compact}"

                if args.no_camera:
                    print(f"{glove_status} ARM {arm_label}")
                    time.sleep(0.1)
                    continue

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

                rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                results = holistic.detect_for_video(mp_image, timestamp_ms)

                wheel_command, wheel_label = build_motor_command(results)
                if now - last_wheel_send >= SEND_INTERVAL_SECONDS:
                    wheel_sender.send(wheel_command)
                    last_wheel_send = now

                draw_holistic_landmarks(image, results)
                put_overlay(image, wheel_label, wheel_command, arm_label, glove_status, esp32_status)
                writer.write(image)
                cv2.imshow("Glove Arm + Camera Car Control", image)

                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    finally:
        glove.close()
        arm_sender.send("stop all")
        arm_sender.close()
        wheel_sender.close()
        if cap is not None:
            cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
