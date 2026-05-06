import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2


ROOT_DIR = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT_DIR / ".matplotlib_cache"))

import mediapipe as mp
from mediapipe.tasks.python.vision import drawing_styles
from mediapipe.tasks.python.vision import drawing_utils
from mediapipe.tasks.python.vision import face_landmarker
from mediapipe.tasks.python.vision import hand_landmarker
from mediapipe.tasks.python.vision import pose_landmarker


CAMERA_INDEX = 0
OUTPUT_FILE = "output_tracking3.mp4"
FPS = 20.0
MODEL_PATH = ROOT_DIR / "models" / "holistic_landmarker.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "holistic_landmarker/holistic_landmarker/float16/latest/"
    "holistic_landmarker.task"
)
MIN_MODEL_SIZE_BYTES = 1_000_000
FINGER_TIP_IDS = {
    "index": 8,
    "middle": 12,
    "ring": 16,
    "pinky": 20,
}
FINGER_PIP_IDS = {
    "index": 6,
    "middle": 10,
    "ring": 14,
    "pinky": 18,
}
FINGER_MCP_IDS = {
    "index": 5,
    "middle": 9,
    "ring": 13,
    "pinky": 17,
}
REQUIRED_GESTURE_FRAMES = 5
FINGER_LIFT_Z_RATIO = 0.35
INDEX_CONTROL_LABELS = {
    None: "NONE",
    "left_index": "LEFT",
    "right_index": "RIGHT",
    "both_index": "BOTH",
}


def landmark_vector(landmark):
    return (landmark.x, landmark.y, landmark.z)


def subtract_vectors(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot_product(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vector_length(vector):
    return dot_product(vector, vector) ** 0.5


def distance_between_landmarks(first, second):
    return vector_length(
        subtract_vectors(landmark_vector(first), landmark_vector(second))
    )


def palm_width(hand_landmarks):
    return distance_between_landmarks(
        hand_landmarks[FINGER_MCP_IDS["index"]],
        hand_landmarks[FINGER_MCP_IDS["pinky"]],
    )


def ensure_model_file():
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size >= MIN_MODEL_SIZE_BYTES:
        return

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading MediaPipe model to {MODEL_PATH} ...")
    download_path = MODEL_PATH.with_suffix(".task.download")

    try:
        urllib.request.urlretrieve(MODEL_URL, download_path)
        download_path.replace(MODEL_PATH)
    except (OSError, urllib.error.URLError) as error:
        if download_path.exists():
            download_path.unlink()
        raise RuntimeError(
            "Could not download the MediaPipe model. "
            f"Download it manually from {MODEL_URL} and save it as {MODEL_PATH}."
        ) from error


def draw_holistic_landmarks(image, results):
    face_connections = face_landmarker.FaceLandmarksConnections.FACE_LANDMARKS_CONTOURS
    pose_connections = pose_landmarker.PoseLandmarksConnections.POSE_LANDMARKS
    hand_connections = hand_landmarker.HandLandmarksConnections.HAND_CONNECTIONS

    if results.face_landmarks:
        drawing_utils.draw_landmarks(
            image,
            results.face_landmarks,
            face_connections,
            landmark_drawing_spec=None,
            connection_drawing_spec=(
                drawing_styles.get_default_face_mesh_contours_style()
            ),
            is_drawing_landmarks=False,
        )

    if results.pose_landmarks:
        drawing_utils.draw_landmarks(
            image,
            results.pose_landmarks,
            pose_connections,
            landmark_drawing_spec=drawing_styles.get_default_pose_landmarks_style(),
            connection_drawing_spec=drawing_utils.DrawingSpec(
                color=(255, 255, 255),
                thickness=2,
            ),
        )

    for hand_landmarks in (results.left_hand_landmarks, results.right_hand_landmarks):
        if hand_landmarks:
            drawing_utils.draw_landmarks(
                image,
                hand_landmarks,
                hand_connections,
                landmark_drawing_spec=drawing_styles.get_default_hand_landmarks_style(),
                connection_drawing_spec=(
                    drawing_styles.get_default_hand_connections_style()
                ),
            )


def is_finger_lifted(hand_landmarks, finger_name):
    tip = hand_landmarks[FINGER_TIP_IDS[finger_name]]
    pip = hand_landmarks[FINGER_PIP_IDS[finger_name]]
    mcp = hand_landmarks[FINGER_MCP_IDS[finger_name]]

    width = palm_width(hand_landmarks)
    if width < 1e-6:
        return False

    tip_lift = mcp.z - tip.z
    pip_lift = mcp.z - pip.z
    return (
        tip_lift > width * FINGER_LIFT_Z_RATIO
        and pip_lift > width * FINGER_LIFT_Z_RATIO * 0.45
    )


def is_index_finger_lifted(hand_landmarks):
    return is_finger_lifted(hand_landmarks, "index")


def detect_index_control_gesture(results):
    left_index_lifted = False
    right_index_lifted = False

    if results.left_hand_landmarks:
        left_index_lifted = is_index_finger_lifted(results.left_hand_landmarks)
    if results.right_hand_landmarks:
        right_index_lifted = is_index_finger_lifted(results.right_hand_landmarks)

    if left_index_lifted and right_index_lifted:
        return "both_index"
    if left_index_lifted:
        return "left_index"
    if right_index_lifted:
        return "right_index"
    return None


class GestureStabilizer:
    def __init__(self, required_frames=REQUIRED_GESTURE_FRAMES):
        self.required_frames = required_frames
        self.current_gesture = None
        self.frame_count = 0

    def update(self, gesture):
        if gesture is None:
            self.current_gesture = None
            self.frame_count = 0
            return None

        if gesture == self.current_gesture:
            self.frame_count += 1
        else:
            self.current_gesture = gesture
            self.frame_count = 1

        if self.frame_count >= self.required_frames:
            return self.current_gesture

        return None


def put_hand_gesture_status(image, results, stabilizer):
    y = 110
    raw_gesture = detect_index_control_gesture(results)
    stable_gesture = stabilizer.update(raw_gesture)
    stable_label = INDEX_CONTROL_LABELS[stable_gesture]
    raw_label = INDEX_CONTROL_LABELS[raw_gesture]

    cv2.putText(
        image,
        f"INDEX CONTROL: {stable_label}",
        (20, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255) if stable_gesture else (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    y += 35

    cv2.putText(
        image,
        f"RAW INDEX: {raw_label}",
        (20, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )


def create_holistic_landmarker():
    base_options = mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH))
    options = mp.tasks.vision.HolisticLandmarkerOptions(
        base_options=base_options,
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        min_face_detection_confidence=0.5,
        min_face_landmarks_confidence=0.5,
        min_pose_detection_confidence=0.5,
        min_pose_landmarks_confidence=0.5,
        min_hand_landmarks_confidence=0.5,
    )
    return mp.tasks.vision.HolisticLandmarker.create_from_options(options)


def main():
    ensure_model_file()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {CAMERA_INDEX}. "
            "Try changing CAMERA_INDEX to 1 if you use an external webcam."
        )

    writer = None
    start_time = time.perf_counter()
    gesture_stabilizer = GestureStabilizer()

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
                draw_holistic_landmarks(image, results)
                put_hand_gesture_status(image, results, gesture_stabilizer)

                writer.write(image)
                cv2.imshow("Holistic Tracking", image)

                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
