# pyright: basic
from dataclasses import dataclass
import json
import os
from typing import Any, Self
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import math


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


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float | None

    @classmethod
    def from_landmark(cls, lm: Any) -> Self:
        return cls(lm.x, lm.y, lm.z)

    @classmethod
    def from_tuple(cls, tup: Any) -> Self:
        return cls(tup[0], tup[1], tup[2])

    @classmethod
    def from_xyz(cls, x: float, y: float, z: float | None = None) -> Self:
        return cls(x, y, z)

    def midpoint(self, rhs: Self) -> Self:
        return type(self).from_xyz((self.x + rhs.x) / 2, (self.y + rhs.y) / 2, None)

    def correct(self, w: int, h: int) -> Self:
        return type(self)(self.x * w, self.y * h, None)

    def as_tuple(self) -> tuple[float, float] | tuple[float, float, float]:
        if self.z is not None:
            return (self.x, self.y, self.z)
        else:
            return (self.x, self.y)

    def as_tuple_int(self) -> tuple[int, int] | tuple[int, int, int]:
        if self.z is not None:
            return (int(self.x), int(self.y), int(self.z))
        else:
            return (int(self.x), int(self.y))

    def dist(self, rhs: Self) -> float:
        return math.dist(self.as_tuple(), rhs.as_tuple())


def average_hand(a: Point, b: Point, c: Point):
    x = (a.x + b.x + c.x) / 3
    y = (a.y + b.y + c.y) / 3
    z = None
    if a.z is not None and b.z is not None and c.z is not None:
        z = (a.z + b.z + c.z) / 3

    return Point.from_xyz(x, y, z)


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
            print("Det er ikke et rigtigt tal")
            continue

        if validate_camera(idx):
            return idx

        print("Det kamera eksisterer ikke")


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
                    Point.from_landmark(pose_landmarks[15]).correct(w, h),
                    Point.from_landmark(pose_landmarks[17]).correct(w, h),
                    Point.from_landmark(pose_landmarks[19]).correct(w, h),
                )
                right = average_hand(
                    Point.from_landmark(pose_landmarks[16]).correct(w, h),
                    Point.from_landmark(pose_landmarks[18]).correct(w, h),
                    Point.from_landmark(pose_landmarks[20]).correct(w, h),
                )

                if (
                    left.x < 0
                    or left.x > w
                    or left.y < 0
                    or left.y > h
                    or right.x < 0
                    or right.x > w
                    or right.y < 0
                    or right.y > h
                ):
                    frame = cv2.flip(frame, 1)
                    cv2.putText(
                        frame,
                        "Dine hænder skal være inden for billedet",
                        (100, 100),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1,
                        (255, 0, 0),
                        2,
                    )
                    frame = cv2.flip(frame, 1)
                    break

                left_shoulder = Point.from_landmark(pose_landmarks[12]).correct(w, h)
                right_shoulder = Point.from_landmark(pose_landmarks[11]).correct(w, h)

                if (
                    left_shoulder.x < 0
                    or left_shoulder.x > w
                    or left_shoulder.y < 0
                    or left_shoulder.y > h
                    or right_shoulder.x < 0
                    or right_shoulder.x > w
                    or right_shoulder.y < 0
                    or right_shoulder.y > h
                ):
                    frame = cv2.flip(frame, 1)
                    cv2.putText(
                        frame,
                        "Dine skuldre skal være inden for billedet",
                        (100, 100),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1,
                        (255, 0, 0),
                        2,
                    )
                    frame = cv2.flip(frame, 1)
                    break

                shoulder_dist = left_shoulder.dist(right_shoulder)

                hand_dist = left.dist(right)

                if hand_dist > shoulder_dist:
                    frame = cv2.flip(frame, 1)
                    cv2.putText(
                        frame,
                        "Dine hænder må ikke være mere end 1 skuldrelængde fra hinanden",
                        (100, 100),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1,
                        (255, 0, 0),
                        2,
                    )
                    frame = cv2.flip(frame, 1)
                    break

                print(
                    json.dumps({"left": {"x": left.x, "y": left.y}}),
                    flush=True,
                )
                print(
                    json.dumps({"right": {"x": right.x, "y": right.y}}),
                    flush=True,
                )

                mid_hands = left.midpoint(right)
                cv2.circle(frame, mid_hands.as_tuple_int(), 5, (0, 255, 0), cv2.FILLED)

                cv2.circle(
                    frame,
                    right.as_tuple_int(),
                    5,
                    (0, 0, 255),
                    cv2.FILLED,
                )
                cv2.circle(
                    frame,
                    left.as_tuple_int(),
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

# Midterpunkt mellem hænder
# Afstand mellem chest midte og middle hænder
# ^ Divider med w
