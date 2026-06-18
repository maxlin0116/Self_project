import argparse
import math
import os
import threading
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import mediapipe as mp
import requests
import serial

from glove_serial_car_control import BAUD_RATE
from glove_serial_car_control import choose_serial_port
from glove_serial_car_control import format_values
from glove_serial_car_control import list_ports
from glove_serial_car_control import parse_glove_line
from mediapipe_wifi_control import CAMERA_INDEX
from mediapipe_wifi_control import FPS
from mediapipe_wifi_control import HAND_RAISED_MARGIN
from mediapipe_wifi_control import MOVE_TIME_MS
from mediapipe_wifi_control import OUTPUT_FILE
from mediapipe_wifi_control import REQUEST_TIMEOUT_SECONDS
from mediapipe_wifi_control import SEND_INTERVAL_SECONDS
from mediapipe_wifi_control import STOP_COMMAND
from mediapipe_wifi_control import build_motor_command
from mediapipe_wifi_control import camera_backend_id
from mediapipe_wifi_control import default_camera_backend
from mediapipe_wifi_control import discover_esp32_ip
from mediapipe_wifi_control import ensure_model_file
from mediapipe_wifi_control import test_esp32_ip
from whole_bode import create_holistic_landmarker
from whole_bode import draw_holistic_landmarks


INDEX_THRESHOLD = 630
MIDDLE_THRESHOLD = 420
RING_THRESHOLD = 380

DEFAULT_UP_FINGER = "middle"
DEFAULT_DOWN_FINGER = "index"
DEFAULT_UP_JOG_SPEED = 20
DEFAULT_UP_JOG_MS = 100
DEFAULT_DOWN_JOG_SPEED = 120
DEFAULT_DOWN_JOG_MS = 1
STOP_SPEED = 90

STATUS_INTERVAL_SECONDS = 0.5
FINGERS = ("index", "middle", "ring")


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


def clamped_servo_value(value):
    return max(0, min(180, value))


def active_fingers(values, thresholds):
    return {
        "index": values["index"] > thresholds["index"],
        "middle": values["middle"] < thresholds["middle"],
        "ring": values["ring"] < thresholds["ring"],
    }


def format_active(active):
    return " ".join(f"{finger}:{'1' if active[finger] else '0'}" for finger in FINGERS)


def landmark_distance(first, second):
    return math.hypot(first.x - second.x, first.y - second.y)


def is_fist(hand_landmarks):
    wrist = hand_landmarks[0]
    folded_fingers = 0
    for tip_index, mcp_index in ((8, 5), (12, 9), (16, 13), (20, 17)):
        tip_distance = landmark_distance(hand_landmarks[tip_index], wrist)
        mcp_distance = landmark_distance(hand_landmarks[mcp_index], wrist)
        if tip_distance < mcp_distance * 1.45:
            folded_fingers += 1
    return folded_fingers >= 3


def raised_fist_count(results):
    pose = results.pose_landmarks
    if not pose or len(pose) < 13:
        return 0

    shoulder_y = min(pose[11].y, pose[12].y)
    count = 0
    for hand_landmarks in (results.left_hand_landmarks, results.right_hand_landmarks):
        if not hand_landmarks:
            continue

        wrist = hand_landmarks[0]
        middle_knuckle = hand_landmarks[9]
        hand_raised = (
            wrist.y < shoulder_y - HAND_RAISED_MARGIN
            or middle_knuckle.y < shoulder_y - HAND_RAISED_MARGIN
        )
        if hand_raised and is_fist(hand_landmarks):
            count += 1

    return count


def build_motor_command_with_back(results):
    if raised_fist_count(results) >= 2:
        return f"4 {MOVE_TIME_MS} 4 {MOVE_TIME_MS}", "BACK"
    return build_motor_command(results)


class HttpCommandSender:
    def __init__(self, ip, timeout_seconds=REQUEST_TIMEOUT_SECONDS):
        self.url = f"http://{ip}/data"
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.trust_env = False
        self.last_error_at = 0.0

    def send(self, command, quiet=True):
        try:
            self.session.post(
                self.url,
                data=command.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=self.timeout_seconds,
            )
            if not quiet:
                print(f"Sent: {command}")
            return True
        except requests.exceptions.RequestException as error:
            now = time.perf_counter()
            if now - self.last_error_at >= 2.0:
                print(f"Command failed: {command} -> {error}")
                self.last_error_at = now
            return False


class QuietWheelSender:
    def __init__(self, ip):
        self.sender = HttpCommandSender(ip, timeout_seconds=REQUEST_TIMEOUT_SECONDS)
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
        self.sender.send(STOP_COMMAND)

    def _run(self):
        while not self.stop_event.is_set():
            self.command_event.wait(timeout=0.1)
            self.command_event.clear()

            with self.lock:
                command = self.latest_command
                self.latest_command = None

            if command is not None:
                self.sender.send(command)


class GloveArmController:
    def __init__(self, args, esp32_ip):
        self.args = args
        self.serial_port = choose_serial_port(args.serial_port)
        self.thresholds = {
            "index": args.index_threshold,
            "middle": args.middle_threshold,
            "ring": args.ring_threshold,
        }
        self.sender = HttpCommandSender(esp32_ip, timeout_seconds=3.0)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.latest_status = "GLOVE starting"
        self.latest_arm_label = "ARM IDLE"
        self.last_grip_command = None

    def start(self):
        print(f"Glove serial: {self.serial_port} @ {self.args.baud}")
        print(
            "Glove thresholds: "
            f"index>{self.thresholds['index']} "
            f"middle<{self.thresholds['middle']} "
            f"ring<{self.thresholds['ring']}"
        )
        print(
            f"Glove arm: {self.args.up_finger}=jog shoulder "
            f"{self.args.up_jog_speed} {self.args.up_jog_ms}, "
            f"{self.args.down_finger}=jog shoulder "
            f"{self.args.down_jog_speed} {self.args.down_jog_ms}"
        )
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1.0)
        self.sender.send(f"set shoulder {self.args.stop_speed}")
        if not self.args.no_gripper:
            self.sender.send("open")

    def snapshot(self):
        with self.lock:
            return self.latest_status, self.latest_arm_label

    def _set_status(self, status, arm_label):
        with self.lock:
            self.latest_status = status
            self.latest_arm_label = arm_label

    def _jog_shoulder(self, speed, duration_ms):
        print(f"ARM: jog shoulder {speed} {duration_ms}")
        self.sender.send(f"set shoulder {speed}")
        time.sleep(max(0, duration_ms) / 1000.0)
        self.sender.send(f"set shoulder {self.args.stop_speed}")

    def _set_gripper(self, active):
        if self.args.no_gripper:
            return "GRIP OFF"

        command = "close" if active["ring"] else "open"
        if command != self.last_grip_command:
            self.sender.send(command)
            self.last_grip_command = command
        return "GRIP CLOSE" if active["ring"] else "GRIP OPEN"

    def _run(self):
        previous_active = {"index": False, "middle": False, "ring": False}
        last_status_at = 0.0

        try:
            with serial.Serial(self.serial_port, self.args.baud, timeout=0.2) as arduino:
                arduino.reset_input_buffer()
                while not self.stop_event.is_set():
                    raw_line = arduino.readline().decode("utf-8", errors="replace").strip()
                    if not raw_line:
                        continue

                    values = parse_glove_line(raw_line)
                    now = time.perf_counter()
                    if values is None:
                        if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                            self._set_status(f"GLOVE serial: {raw_line}", "ARM IDLE")
                            last_status_at = now
                        continue

                    active = active_fingers(values, self.thresholds)
                    arm_label = "ARM IDLE"

                    up_rising = active[self.args.up_finger] and not previous_active[self.args.up_finger]
                    down_rising = active[self.args.down_finger] and not previous_active[self.args.down_finger]
                    both_active = active[self.args.up_finger] and active[self.args.down_finger]

                    if both_active:
                        arm_label = "ARM SKIP BOTH"
                    elif up_rising:
                        self._jog_shoulder(self.args.up_jog_speed, self.args.up_jog_ms)
                        arm_label = "ARM UP JOG"
                    elif down_rising:
                        self._jog_shoulder(self.args.down_jog_speed, self.args.down_jog_ms)
                        arm_label = "ARM DOWN JOG"

                    grip_label = self._set_gripper(active)
                    previous_active = active

                    if now - last_status_at >= STATUS_INTERVAL_SECONDS or arm_label != "ARM IDLE":
                        self._set_status(
                            f"GLOVE {format_values(values)} active {format_active(active)} {grip_label}",
                            arm_label,
                        )
                        last_status_at = now
        except serial.SerialException as error:
            self._set_status(f"GLOVE serial failed: {error}", "ARM ERROR")
            print(f"Glove serial failed: {error}")


def put_overlay(image, wheel_label, wheel_command, glove_status, arm_label, esp32_status):
    rows = [
        f"WHEEL {wheel_label}: {wheel_command}",
        arm_label,
        glove_status,
        esp32_status,
    ]
    colors = [
        (0, 255, 255),
        (255, 200, 0),
        (0, 255, 0),
        (0, 255, 0),
    ]
    for index, text in enumerate(rows):
        cv2.putText(
            image,
            text,
            (20, 40 + index * 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            colors[index],
            2,
            cv2.LINE_AA,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Camera wheel control plus serial glove arm control.")
    parser.add_argument("--ip", default="auto", help="ESP32 IP address, or auto")
    parser.add_argument("--serial-port", default="auto", help="Arduino glove serial port, or auto")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--camera-index", type=int, default=CAMERA_INDEX)
    parser.add_argument(
        "--camera-backend",
        choices=("any", "msmf", "dshow"),
        default=default_camera_backend(),
    )
    parser.add_argument("--index-threshold", type=int, default=INDEX_THRESHOLD)
    parser.add_argument("--middle-threshold", type=int, default=MIDDLE_THRESHOLD)
    parser.add_argument("--ring-threshold", type=int, default=RING_THRESHOLD)
    parser.add_argument("--up-finger", choices=FINGERS, default=DEFAULT_UP_FINGER)
    parser.add_argument("--down-finger", choices=FINGERS, default=DEFAULT_DOWN_FINGER)
    parser.add_argument("--up-jog-speed", type=int, default=DEFAULT_UP_JOG_SPEED)
    parser.add_argument("--up-jog-ms", type=int, default=DEFAULT_UP_JOG_MS)
    parser.add_argument("--down-jog-speed", type=int, default=DEFAULT_DOWN_JOG_SPEED)
    parser.add_argument("--down-jog-ms", type=int, default=DEFAULT_DOWN_JOG_MS)
    parser.add_argument("--stop-speed", type=int, default=STOP_SPEED)
    parser.add_argument("--no-gripper", action="store_true", help="Do not control gripper from ring finger")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_ports:
        for port in list_ports():
            print(f"{port.device}: {port.description}")
        return

    args.up_jog_speed = clamped_servo_value(args.up_jog_speed)
    args.down_jog_speed = clamped_servo_value(args.down_jog_speed)
    args.stop_speed = clamped_servo_value(args.stop_speed)
    args.up_jog_ms = max(0, args.up_jog_ms)
    args.down_jog_ms = max(0, args.down_jog_ms)

    esp32_ip = resolve_esp32_ip(args.ip)
    esp32_status = f"ESP32 ONLINE {esp32_ip}"

    ensure_model_file()
    cap = cv2.VideoCapture(args.camera_index, camera_backend_id(args.camera_backend))
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {args.camera_index}. "
            "Try --camera-index 1 or --camera-backend dshow."
        )

    wheel_sender = QuietWheelSender(esp32_ip)
    glove = GloveArmController(args, esp32_ip)
    glove.start()

    writer = None
    last_wheel_send = 0.0
    last_wheel_command = None
    start_time = time.perf_counter()

    print(f"ESP32: http://{esp32_ip}")
    print("Camera: left hand=LEFT, right hand=RIGHT, both hands=FORWARD, both raised fists=BACK, no hands=STOP.")
    print("Glove: middle bends once=arm up jog, index bends once=arm down jog.")
    print("Press q/ESC in the camera window to stop.")

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

                wheel_command, wheel_label = build_motor_command_with_back(results)
                now = time.perf_counter()
                should_send = now - last_wheel_send >= SEND_INTERVAL_SECONDS
                if wheel_command == STOP_COMMAND and last_wheel_command == STOP_COMMAND:
                    should_send = False
                if should_send:
                    wheel_sender.send(wheel_command)
                    last_wheel_send = now
                    last_wheel_command = wheel_command

                glove_status, arm_label = glove.snapshot()
                draw_holistic_landmarks(image, results)
                put_overlay(image, wheel_label, wheel_command, glove_status, arm_label, esp32_status)

                if writer is not None and writer.isOpened():
                    writer.write(image)

                cv2.imshow("Camera Wheel + Glove Arm Control", image)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    finally:
        glove.close()
        wheel_sender.send(STOP_COMMAND)
        wheel_sender.close()
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
