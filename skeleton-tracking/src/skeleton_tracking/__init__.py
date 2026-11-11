# pyright: basic
import json
import os
import socket
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
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    min_pose_detection_confidence=0.8,
    min_tracking_confidence=0.8,
)


class Point:
    x: float
    y: float
    z: float | None

    def __init__(self, x: float, y: float, z: float | None = None) -> None:
        self.x = x
        self.y = y
        self.z = z

    def __truediv__(self, other: float) -> Self:
        return type(self)(self.x / other, self.y / other, None)

    def __add__(self, other: Self) -> Self:
        return type(self)(self.x + other.x, self.y + other.y, None)

    def __mul__(self, other: float) -> Self:
        return type(self)(self.x * other, self.y * other, None)

    @classmethod
    def from_landmark(cls, lm: Any) -> Self:
        return cls(lm.x, lm.y, lm.z)

    @classmethod
    def from_tuple(cls, tup: Any) -> Self:
        return cls(tup[0], tup[1], tup[2])

    def midpoint(self, rhs: Self) -> Self:
        return type(self)((self.x + rhs.x) / 2, (self.y + rhs.y) / 2, None)

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

    def length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2)

    def dot(self, rhs: Self) -> float:
        return self.x * rhs.x + self.y * rhs.y


def average_hand(a: Point, b: Point, c: Point):
    x = (a.x + b.x + c.x) / 3
    y = (a.y + b.y + c.y) / 3
    z = None
    if a.z is not None and b.z is not None and c.z is not None:
        z = (a.z + b.z + c.z) / 3

    return Point(x, y, z)


# --- Overlay output -------------------------------------------------------
# Dots are sent as JSON over UDP to the overlay instead of being drawn on the frame.
OVERLAY_ADDR = ("127.0.0.1", int(os.environ.get("OVERLAY_PORT", "45454")))
MIRROR = True  # the camera view is mirrored for the player, like the old flipped frame
DOT_TTL_MS = 250  # dots vanish by themselves if tracking stops sending updates
_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def dot(id: str, p: Point, w: int, h: int, color: str, r: float = 0.006) -> dict:
    """A circle for the overlay. p is in camera pixels; the overlay wants 0..1 fractions
    of the screen (x by width, y by height, r by height)."""
    x = p.x / w
    if MIRROR:
        x = 1 - x
    return {
        "id": id,
        "type": "circle",
        "x": x,
        "y": p.y / h,
        "r": r,
        "width": 0,  # no outline, fill only
        "fill": color,
        "ttl": DOT_TTL_MS,
    }


def send_dots(dots: list[dict]) -> None:
    if dots:
        msg = json.dumps({"cmd": "batch", "items": dots}, separators=(",", ":"))
        _sock.sendto(msg.encode() + b"\n", OVERLAY_ADDR)


def validate_camera(index: int) -> bool:
    cap = cv2.VideoCapture(index)
    try:
        return cap.isOpened()
    finally:
        cap.release()


def get_camera_index() -> int:
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
    if "CAMERA" in os.environ:
        try:
            camera_env = os.environ.get("CAMERA")
            assert camera_env is not None
            cam_index = int(camera_env)
            if not validate_camera(cam_index):
                print("Det er IKKE et rigtigt kamera!")
                return
        except ValueError:
            print("CAMERA er ikke et rigtigt tal!")
            return
    else:
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
        dots: list[dict] = []

        if detection_result.pose_landmarks:
            for pose_landmarks in detection_result.pose_landmarks:
                if "DEBUG" in os.environ and os.environ.get("DEBUG") == "true":
                    for i, landmark in enumerate(pose_landmarks):
                        lm = Point(landmark.x * w, landmark.y * h)
                        dots.append(dot(f"lm{i}", lm, w, h, "#0000ff", 0.004))

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

                shoulder_mid = left_shoulder.midpoint(right_shoulder)

                # How much to move it down by
                hmtmidb = shoulder_dist / 10

                perp = Point(
                    -(right_shoulder.y - left_shoulder.y),
                    right_shoulder.x - left_shoulder.x,
                )
                perp_unit = perp / perp.length()

                chest_mid = shoulder_mid + (perp_unit * hmtmidb)
                dots.append(dot("chest_mid", chest_mid, w, h, "#ffffff"))

                # print(
                #     json.dumps({"left": {"x": left.x, "y": left.y}}),
                #     flush=True,
                # )
                # print(
                #     json.dumps({"right": {"x": right.x, "y": right.y}}),
                #     flush=True,
                # )

                mid_hands = left.midpoint(right)
                dots.append(dot("mid_hands", mid_hands, w, h, "#00ff00"))

                shoulder_to_shoulder = Point(
                    right_shoulder.x - left_shoulder.x,
                    right_shoulder.y - left_shoulder.y,
                )

                shoulder_mid_to_mid_hands = Point(
                    shoulder_mid.x - mid_hands.x, shoulder_mid.y - mid_hands.y
                )

                angle = math.acos(
                    shoulder_to_shoulder.dot(shoulder_mid_to_mid_hands)
                    / (
                        shoulder_to_shoulder.length()
                        * shoulder_mid_to_mid_hands.length()
                    )
                )

                shoulder_angle = math.atan2(
                    shoulder_to_shoulder.y, shoulder_to_shoulder.x
                )

                frame = cv2.flip(frame, 1)
                cv2.putText(
                    frame,
                    f"{angle}",
                    (100, 100),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (255, 0, 0),
                    2,
                )

                cv2.putText(
                    frame,
                    f"Shoulder angle: {shoulder_angle}",
                    (100, 120),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (255, 0, 0),
                    2,
                )
                frame = cv2.flip(frame, 1)

                dots.append(dot("right_hand", right, w, h, "#ff0000"))
                dots.append(dot("left_hand", left, w, h, "#ff0000"))

        send_dots(dots)

        flipped = cv2.flip(frame, 1)

        # cv2.imshow("Capture", flipped)
        if cv2.waitKey(1) == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
