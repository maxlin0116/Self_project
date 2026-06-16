import argparse
import math
import os
import queue
import threading
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import mediapipe as mp
import requests

from mediapipe_wifi_control import STOP_COMMAND
from mediapipe_wifi_control import camera_backend_id
from mediapipe_wifi_control import default_camera_backend
from mediapipe_wifi_control import discover_esp32_ip
from mediapipe_wifi_control import test_esp32_ip
from whole_bode import create_holistic_landmarker
from whole_bode import draw_holistic_landmarks
from whole_bode import ensure_model_file


CAMERA_INDEX = 0
FPS = 20.0
OUTPUT_FILE = "output_holistic_gesture_vision.mp4"
REQUEST_TIMEOUT_SECONDS = 0.5
HAND_RAISED_MARGIN = 0.05

CONTROL_LABELS = {
    "left": "LEFT",
    "right": "RIGHT",
    "forward": "FORWARD",
    "back": "BACK",
    "stop": "STOP",
}


def is_visible(landmark):
    visibility_ok = landmark.visibility is None or landmark.visibility >= 0.5
    presence_ok = landmark.presence is None or landmark.presence >= 0.5
    return visibility_ok and presence_ok


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


def hand_is_raised(hand_landmarks, shoulder_y):
    wrist = hand_landmarks[0]
    middle_knuckle = hand_landmarks[9]
    return (
        wrist.y < shoulder_y - HAND_RAISED_MARGIN
        or middle_knuckle.y < shoulder_y - HAND_RAISED_MARGIN
    )


def detect_raised_hand_control(results):
    pose = results.pose_landmarks
    left_hand_raised = False
    right_hand_raised = False
    raised_fists = 0

    if pose and len(pose) >= 17:
        # The frame is mirrored before detection, so swap sides back to match
        # the user's real left and right hands.
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
                if hand_is_raised(hand_landmarks, shoulder_y):
                    if is_fist(hand_landmarks):
                        raised_fists += 1
                    if wrist.x < shoulder_center_x:
                        left_hand_raised = True
                    else:
                        right_hand_raised = True

    if left_hand_raised and right_hand_raised and raised_fists >= 2:
        return "back"
    if left_hand_raised and right_hand_raised:
        return "forward"
    if left_hand_raised:
        return "left"
    if right_hand_raised:
        return "right"
    return "stop"


def detect_lowered_hand_controls(results, wheel_control):
    if wheel_control != "stop":
        return None, None

    pose = results.pose_landmarks
    if not pose or len(pose) < 13:
        return None, None

    shoulder_y = min(pose[11].y, pose[12].y)
    shoulder_center_x = (pose[11].x + pose[12].x) / 2.0
    gripper_command = None
    arm_action = None

    for hand_landmarks in (results.left_hand_landmarks, results.right_hand_landmarks):
        if not hand_landmarks:
            continue
        if hand_is_raised(hand_landmarks, shoulder_y):
            continue

        wrist = hand_landmarks[0]
        side = "left" if wrist.x < shoulder_center_x else "right"
        if is_fist(hand_landmarks):
            if side == "right":
                gripper_command = "close"
            else:
                arm_action = "up"
        else:
            if side == "right":
                gripper_command = "open"
            else:
                arm_action = "down"

    return gripper_command, arm_action


def wheel_command(control, move_ms, turn_ms):
    if control == "left":
        return f"3 {turn_ms} 2 0"
    if control == "right":
        return f"2 0 3 {turn_ms}"
    if control == "forward":
        return f"3 {move_ms} 3 {move_ms}"
    if control == "back":
        return f"4 {move_ms} 4 {move_ms}"
    return STOP_COMMAND


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


class AsyncEsp32Sender:
    def __init__(self, ip):
        self.url = f"http://{ip}/data"
        self.session = requests.Session()
        self.session.trust_env = False
        self.commands = queue.Queue()
        self.last_error_at = 0.0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def send(self, command):
        self.commands.put(command)

    def jog_shoulder(self, speed, duration_ms, stop_speed):
        self.commands.put(("jog_shoulder", speed, duration_ms, stop_speed))

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
            if isinstance(command, tuple) and command[0] == "jog_shoulder":
                _, speed, duration_ms, stop_speed = command
                self._post(f"set shoulder {speed}")
                time.sleep(max(0, duration_ms) / 1000.0)
                self._post(f"set shoulder {stop_speed}")
            else:
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
                print(f"ESP32 command failed: {error}")
                self.last_error_at = now


class MouseGestureController:
    def __init__(self, smoothening, click_distance):
        import pyautogui

        self.pyautogui = pyautogui
        self.pyautogui.FAILSAFE = True
        self.pyautogui.PAUSE = 0
        self.screen_w, self.screen_h = self.pyautogui.size()
        self.smoothening = max(1, smoothening)
        self.click_distance = click_distance
        self.prev_x = 0.0
        self.prev_y = 0.0
        self.is_clicked = False

    def process(self, image, results):
        hand_landmarks = results.right_hand_landmarks
        if not hand_landmarks or len(hand_landmarks) < 9:
            self.is_clicked = False
            return

        height, width, _ = image.shape
        thumb = hand_landmarks[4]
        index_finger = hand_landmarks[8]

        x1, y1 = int(thumb.x * width), int(thumb.y * height)
        x2, y2 = int(index_finger.x * width), int(index_finger.y * height)

        cv2.circle(image, (x1, y1), 8, (0, 0, 255), cv2.FILLED)
        cv2.circle(image, (x2, y2), 8, (0, 0, 255), cv2.FILLED)
        cv2.line(image, (x1, y1), (x2, y2), (255, 0, 0), 2)

        margin_w = int(width * 0.1)
        margin_h = int(height * 0.1)
        clamped_x = max(margin_w, min(x2, width - margin_w))
        clamped_y = max(margin_h, min(y2, height - margin_h))

        screen_x = int((clamped_x - margin_w) / (width - 2 * margin_w) * self.screen_w)
        screen_y = int((clamped_y - margin_h) / (height - 2 * margin_h) * self.screen_h)

        curr_x = self.prev_x + (screen_x - self.prev_x) / self.smoothening
        curr_y = self.prev_y + (screen_y - self.prev_y) / self.smoothening
        self.pyautogui.moveTo(int(curr_x), int(curr_y))
        self.prev_x, self.prev_y = curr_x, curr_y

        distance = math.hypot(x2 - x1, y2 - y1)
        center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.putText(
            image,
            f"Pinch: {int(distance)}",
            (center_x - 35, center_y - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        if distance < self.click_distance:
            cv2.circle(image, (center_x, center_y), 12, (0, 255, 0), cv2.FILLED)
            if not self.is_clicked:
                self.pyautogui.click()
                self.is_clicked = True
                print("Mouse clicked")
        else:
            self.is_clicked = False


def put_status(image, control, command, gripper_status, arm_status, esp32_status, mouse_status):
    rows = [
        f"GESTURE: {CONTROL_LABELS[control]}",
        f"WHEEL COMMAND: {command}",
        gripper_status,
        arm_status,
        esp32_status,
        mouse_status,
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Standalone MediaPipe Holistic gesture recognition test."
    )
    parser.add_argument("--camera-index", type=int, default=CAMERA_INDEX)
    parser.add_argument(
        "--camera-backend",
        choices=("any", "msmf", "dshow"),
        default=default_camera_backend(),
    )
    parser.add_argument("--output", default=OUTPUT_FILE)
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--send", action="store_true", help="Send wheel commands to ESP32")
    parser.add_argument("--ip", default="auto", help="ESP32 IP used with --send")
    parser.add_argument("--move-ms", type=int, default=260)
    parser.add_argument("--turn-ms", type=int, default=120)
    parser.add_argument("--send-interval", type=float, default=0.2)
    parser.add_argument("--arm-up-speed", type=int, default=20)
    parser.add_argument("--arm-up-ms", type=int, default=100)
    parser.add_argument("--arm-down-speed", type=int, default=120)
    parser.add_argument("--arm-down-ms", type=int, default=1)
    parser.add_argument("--shoulder-stop", type=int, default=90)
    parser.add_argument("--mouse-control", action="store_true")
    parser.add_argument("--smoothening", type=int, default=5)
    parser.add_argument("--click-distance", type=int, default=35)
    return parser.parse_args()


def main():
    args = parse_args()
    ensure_model_file()

    sender = None
    esp32_status = "ESP32 OFFLINE"
    if args.send:
        esp32_ip = resolve_esp32_ip(args.ip)
        sender = AsyncEsp32Sender(esp32_ip)
        esp32_status = f"ESP32 ONLINE {esp32_ip}"

    mouse = None
    mouse_status = "MOUSE OFF"
    if args.mouse_control:
        mouse = MouseGestureController(args.smoothening, args.click_distance)
        mouse_status = "MOUSE ON"

    cap = cv2.VideoCapture(args.camera_index, camera_backend_id(args.camera_backend))
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {args.camera_index}. "
            "Try --camera-index 1 or --camera-backend dshow."
        )

    writer = None
    last_send_time = 0.0
    last_command = None
    last_gripper_command = None
    last_arm_action = None
    gripper_status = "GRIPPER: IDLE"
    arm_status = "ARM: IDLE"
    start_time = time.perf_counter()

    print("Press q or ESC in the camera window to stop.")
    print(
        "Gesture: left hand=LEFT, right hand=RIGHT, both hands=FORWARD, "
        "both raised fists=BACK, no hands=STOP."
    )
    print(
        "Lowered hands: right fist=CLOSE, right open=OPEN, "
        "left fist=ARM UP, left open=ARM DOWN."
    )

    try:
        with create_holistic_landmarker() as holistic:
            while True:
                success, image = cap.read()
                if not success:
                    print("Could not read a frame from the camera.")
                    break

                image = cv2.flip(image, 1)

                if writer is None and not args.no_record:
                    frame_height, frame_width = image.shape[:2]
                    writer = cv2.VideoWriter(
                        args.output,
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        FPS,
                        (frame_width, frame_height),
                    )
                    if not writer.isOpened():
                        raise RuntimeError(f"Could not create video file: {args.output}")

                rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                results = holistic.detect_for_video(mp_image, timestamp_ms)

                control = detect_raised_hand_control(results)
                command = wheel_command(control, args.move_ms, args.turn_ms)
                gripper_command, arm_action = detect_lowered_hand_controls(results, control)
                now = time.perf_counter()

                if sender is not None:
                    should_send = now - last_send_time >= args.send_interval
                    if command == STOP_COMMAND and last_command == STOP_COMMAND:
                        should_send = False
                    if should_send:
                        sender.send(command)
                        last_send_time = now
                        last_command = command
                    if gripper_command and gripper_command != last_gripper_command:
                        sender.send(gripper_command)
                        last_gripper_command = gripper_command
                    if arm_action and arm_action != last_arm_action:
                        if arm_action == "up":
                            sender.jog_shoulder(
                                args.arm_up_speed,
                                args.arm_up_ms,
                                args.shoulder_stop,
                            )
                        else:
                            sender.jog_shoulder(
                                args.arm_down_speed,
                                args.arm_down_ms,
                                args.shoulder_stop,
                            )
                        last_arm_action = arm_action

                if gripper_command:
                    gripper_status = f"GRIPPER: {gripper_command.upper()}"
                if arm_action:
                    arm_status = f"ARM: {arm_action.upper()}"

                draw_holistic_landmarks(image, results)
                if mouse is not None:
                    mouse.process(image, results)
                put_status(
                    image,
                    control,
                    command,
                    gripper_status,
                    arm_status,
                    esp32_status,
                    mouse_status,
                )

                if writer is not None and writer.isOpened():
                    writer.write(image)

                cv2.imshow("Holistic Gesture Vision", image)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    finally:
        if sender is not None:
            sender.close()
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
