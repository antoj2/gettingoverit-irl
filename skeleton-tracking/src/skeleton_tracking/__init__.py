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

# --- Color ----------------------------------------------------------------
def interpolate_color(color1: str, color2: str, factor: float) -> str:
    """
    Interpolates between two hex colors.

    Supports:
        #RRGGBB
        #AARRGGBB

    Args:
        color1: Starting color.
        color2: Ending color.
        factor: Interpolation factor (0.0 = color1, 1.0 = color2).

    Returns:
        Interpolated hex color.
    """
    if not 0.0 <= factor <= 1.0:
        raise ValueError("factor must be between 0.0 and 1.0")

    def parse_color(color: str) -> tuple[int, ...]:
        color = color.lstrip("#")

        if len(color) == 6:
            return (255, *(
                int(color[i:i + 2], 16)
                for i in range(0, 6, 2)
            ))

        if len(color) == 8:
            return tuple(
                int(color[i:i + 2], 16)
                for i in range(0, 8, 2)
            )

        raise ValueError(f"Invalid color format: {color}")

    c1 = parse_color(color1)
    c2 = parse_color(color2)

    result = tuple(
        round(a + (b - a) * factor)
        for a, b in zip(c1, c2)
    )

    return "#" + "".join(f"{channel:02X}" for channel in result)

# --- Point ----------------------------------------------------------------
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

    def inside_bounds(self, x1, y1, x2, y2) -> bool:
        return (self.x >= x1 or self.x <= x2 or self.y >= y1 or self.y <= y2)

    def __str__(self):
        if self.z == None:
            return f"({self.x}, {self.y})"
        return f"({self.x}, {self.y}, {self.z})"

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
TTL_MS = 250  # dots vanish by themselves if tracking stops sending updates
_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def dot(id: str, p: Point, srW: int, srH: int, color: str, r: float = 0.006) -> dict:
    """A circle for the overlay. p is in camera pixels; the overlay wants 0..1 fractions
    of the screen (x by width, y by height, r by height)."""
    x = p.x / srW
    if MIRROR:
        x = 1 - x
    return {
        "id": id,
        "type": "circle",
        "x": x,
        "y": p.y / srH,
        "r": r,
        "width": 0,  # no outline, fill only
        "fill": color,
        "ttl": TTL_MS,
    }

def line(id: str, p1: Point, p2: Point, srW: int, srH: int, color: str, width: float = 0.008) -> dict:
    x1 = p1.x / srW
    x2 = p2.x / srW
    if MIRROR:
        x1 = 1 - x1
        x2 = 1 - x2
    return {
        "id": id,
        "type": "line",
        "x1": x1,
        "y1": p1.y / srH,
        "x2": x2,
        "y2": p2.y / srH,
        "width": width,
        "color": color,
        "ttl": TTL_MS,
    }

def text(id: str, t: str, achor: Point, srW: int, srH: int, color: str, size: float = 0.01, align: str = "center", bg_color: str = "#a00000000", bold: bool = False) -> dict:
    x = achor.x / srW
    if MIRROR:
        x = 1 - x
    return {
        "id": id,
        "type": "text",
        "text": t,
        "x": x,
        "y": achor.y / srH,
        "color": color,
        "size": size,
        "align": align,
        "bg": bg_color,
        "bold": bold,
        "ttl": TTL_MS,
    }

def command(cmd: str, input: dict = {}) -> dict:
    input["cmd"] = cmd
    return input

def json_command(cmd: dict | list[dict] = {}) -> str:
    if isinstance(cmd, dict):
        return json.dumps(cmd, separators=(",", ":"))
    return "\n".join(json.dumps(ting, separators=(",", ":")) for ting in cmd)

def send(commands: list[dict]) -> None:
    if commands:
        _sock.sendto(json_command(commands).encode() + b"\n", OVERLAY_ADDR)

# --- Camera ---------------------------------------------------------------
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

# --- Find Points ----------------------------------------------------------
def findPoints(cap: cv2.VideoCapture, detector: Any) -> tuple[Point, Point, Point, Point] | None:
    """Returns (leftHand, rightHand, leftShoulder, rightShoulder, *centerOfBody)"""
    ret, frame = cap.read()
    if not ret:
        return
    h, w, _ = frame.shape

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

    timestamp_ms = int((cv2.getTickCount() / cv2.getTickFrequency()) * 1000)

    detection_result = detector.detect_for_video(mp_image, timestamp_ms)

    if detection_result.pose_landmarks:
        for pose_landmarks in detection_result.pose_landmarks:
            # if "DEBUG" in os.environ and os.environ.get("DEBUG") == "true":
            #     for i, landmark in enumerate(pose_landmarks):
            #         lm = Point(landmark.x * w, landmark.y * h)
            #         items.append(dot(f"lm{i}", lm, w, h, "#0000ff", 0.004))

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

            left_shoulder = Point.from_landmark(pose_landmarks[12]).correct(w, h)
            right_shoulder = Point.from_landmark(pose_landmarks[11]).correct(w, h)

            return (left, right, left_shoulder, right_shoulder)

# --- Main -----------------------------------------------------------------
def construct(left_hand: Point, right_hand: Point, left_shoulder: Point, right_shoulder: Point, w: int, h: int) -> list[dict]:
    items: list[dict] = []

    # Checks if hands are outside of bounds
    if (not left_hand.inside_bounds(0,0,w,h) or not right_hand.inside_bounds(0,0,w,h)):
        items.append(text("hands_outside", "Dine hænder skal være inden for billedet", Point(w/2,0), w, h, "#FFFFFF", bg_color="#a20000000", size = 0.04))
        return items
    
    # Checks if shoulders are outside of bounds
    if (not left_shoulder.inside_bounds(0,0,w,h) or not right_shoulder.inside_bounds(0,0,w,h)):
        items.append(text("shoulders_outside", "Dine skuldre skal være inden for billedet", Point(w/2,0), w, h, "#FFFFFF", bg_color="#a20000000", size = 0.04))
        return items
    
    shoulder_dist = left_shoulder.dist(right_shoulder)
    hand_dist = left_hand.dist(right_hand)
    
    # Checks if hands are further apart than shoulders
    if hand_dist > shoulder_dist:
        items.append(text("hands_together", "Dine hænder må ikke være mere end 1 skuldrelængde fra hinanden", Point(w/2,0), w, h, "#FFFFFF", bg_color="#a20000000", size = 0.04))
        items.append(dot("right_hand", right_hand, w, h, "#ff0000", r = 0.01))
        items.append(dot("left_hand", left_hand, w, h, "#ff0000", r = 0.01))
        return items
    
    items.append(line("shoulders", left_shoulder, right_shoulder, w, h, "#B0310874"))
    items.append(dot("shoulder1", left_shoulder, w, h, "#3b2fdd"))
    items.append(dot("shoulder2", right_shoulder, w, h, "#3b2fdd"))
    
    shoulder_mid = left_shoulder.midpoint(right_shoulder)
    
    # How much to move it down by
    hmtmidb = shoulder_dist / 2
    
    perp = Point(
        -(right_shoulder.y - left_shoulder.y),
        right_shoulder.x - left_shoulder.x,
    )
    perp_unit = perp / perp.length()
    
    chest_mid = shoulder_mid + (perp_unit * hmtmidb)
    items.append(dot("chest_mid", chest_mid, w, h, "#ffffff"))
    
    mid_hands = left_hand.midpoint(right_hand)
    items.append(dot("mid_hands", mid_hands, w, h, interpolate_color("#00FF00", "#FF0000", hand_dist/shoulder_dist)))
    
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

    items.append(text("angle", f"Vinkel af hammer: {angle}", Point(w,0.03 * h), w, h, "#FFFFFF", bg_color="#a20000000", size = 0.02, align="left"))
    items.append(text("shoulder_angle", f"Vinkel af skuldre: {shoulder_angle}", Point(w,0), w, h, "#FFFFFF", bg_color="#a20000000", size = 0.02, align="left"))
    
    return items

def main() -> None:
    # Optains Camera
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

    # Optains frame
    detector = vision.PoseLandmarker.create_from_options(options)
    cap = cv2.VideoCapture(cam_index)
    ret, frame = cap.read()
    if not ret:
        return
    h, w, _ = frame.shape

    # Main Loop
    while cap.isOpened():
        # Optains Points from frame
        points = findPoints(cap, detector)
        if (not points):
            continue
        left_hand, right_hand, left_shoulder, right_shoulder = points
        # print(left_hand, right_hand, left_shoulder, right_shoulder)

        # Sends commands to overlay
        send([command("clear"), command("batch", {"items": construct(left_hand, right_hand, left_shoulder, right_shoulder, w, h)})])

        if cv2.waitKey(1) == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
