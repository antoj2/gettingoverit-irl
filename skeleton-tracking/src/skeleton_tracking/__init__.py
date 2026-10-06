# pyright: basic
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np

# Initialize PoseLandmarker
base_options = python.BaseOptions(
    model_asset_path="pose_landmarker_full.task",
)
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.IMAGE,
)


def main() -> None:
    detector = vision.PoseLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(1)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        detection_result = detector.detect(mp_image)

        h, w, _ = frame.shape

        if detection_result.pose_landmarks:
            for pose_landmarks in detection_result.pose_landmarks:
                for landmark in pose_landmarks:
                    cx, cy = int(landmark.x * w), int(landmark.y * h)

                    cv2.circle(frame, (cx, cy), 5, (255, 0, 0), cv2.FILLED)

        flipped = cv2.flip(frame, 1)
        cv2.imshow("Capture", flipped)
        if cv2.waitKey(1) == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
