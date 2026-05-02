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
