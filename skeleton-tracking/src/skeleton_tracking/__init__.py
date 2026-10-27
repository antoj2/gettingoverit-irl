# pyright: basic
import json
import os
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np


# Initialize PoseLandmarker
base_options = python.BaseOptions(
    model_asset_path="pose_landmarker_full.task",
)
# 1. FIX: Changed running_mode to VIDEO to activate continuous temporal tracking
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    min_pose_detection_confidence=0.8,
    min_tracking_confidence=0.8,
)


def average_hand(a, b, c):
    x1, y1, z1 = a.x, a.y, a.z
    x2, y2, z2 = b.x, b.y, b.z
    x3, y3, z3 = c.x, c.y, c.z

    x = (x1 + x2 + x3) / 3
    y = (y1 + y2 + y3) / 3
    z = (z1 + z2 + z3) / 3

    return (x, y, z)


def get_camera_index() -> int:
    def validate_camera(index: int) -> bool:
        cap = cv2.VideoCapture(index)
        try:
            return cap.isOpened()
        finally:
            cap.release()

    while True:
        print("Hvilket kamera index vil du gerne bruge?")
        try:
            idx = int(input())
        except ValueError:
            print("Indtast venligst et gyldigt tal!")
            continue

        if validate_camera(idx):
            return idx

        print("Det var ikke et rigtigt kamera!")


def main() -> None:
    cam_index = get_camera_index()

    detector = vision.PoseLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(cam_index)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        timestamp_ms = int((cv2.getTickCount() / cv2.getTickFrequency()) * 1000)

        detection_result = detector.detect_for_video(mp_image, timestamp_ms)

        h, w, _ = frame.shape

        if detection_result.pose_landmarks:
            for pose_landmarks in detection_result.pose_landmarks:
                if "DEBUG" in os.environ and os.environ.get("DEBUG") == "true":
                    for landmark in pose_landmarks:
                        cx, cy = int(landmark.x * w), int(landmark.y * h)

                        cv2.circle(frame, (cx, cy), 5, (255, 0, 0), cv2.FILLED)

                left = average_hand(
                    pose_landmarks[15], pose_landmarks[17], pose_landmarks[19]
                )
                right = average_hand(
                    pose_landmarks[16], pose_landmarks[18], pose_landmarks[20]
                )
                print(
                    json.dumps({"left": {"x": left[0] * w, "y": left[1] * h}}),
                    flush=True,
                )
                print(
                    json.dumps({"right": {"x": right[0] * w, "y": right[1] * h}}),
                    flush=True,
                )

                cv2.circle(
                    frame,
                    (int(right[0] * w), int(right[1] * h)),
                    5,
                    (0, 0, 255),
                    cv2.FILLED,
                )
                cv2.circle(
                    frame,
                    (int(left[0] * w), int(left[1] * h)),
                    5,
                    (0, 0, 255),
                    cv2.FILLED,
                )

        flipped = cv2.flip(frame, 1)

        cv2.imshow("Capture", flipped)
        if cv2.waitKey(1) == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
