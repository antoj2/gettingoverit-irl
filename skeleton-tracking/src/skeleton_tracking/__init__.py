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
import time

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
    if factor < 0:
        return color1
    if factor > 1:
        return color2

    def parse_color(color: str) -> tuple[int, ...]:
        color = color.lstrip("#")

        if len(color) == 6:
            return (255, *(int(color[i : i + 2], 16) for i in range(0, 6, 2)))

        if len(color) == 8:
            return tuple(int(color[i : i + 2], 16) for i in range(0, 8, 2))

        raise ValueError(f"Invalid color format: {color}")

    c1 = parse_color(color1)
    c2 = parse_color(color2)

    result = tuple(round(a + (b - a) * factor) for a, b in zip(c1, c2))

    return "#" + "".join(f"{channel:02X}" for channel in result)


# --- Point ----------------------------------------------------------------
class Point:
    x: float
    y: float

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def __truediv__(self, other: float) -> Self:
        return type(self)(self.x / other, self.y / other)

    def __add__(self, other: Self) -> Self:
        return type(self)(self.x + other.x, self.y + other.y)

    def __mul__(self, other: float) -> Self:
        return type(self)(self.x * other, self.y * other)

    @classmethod
    def from_landmark(cls, lm: Any) -> Self:
        return cls(lm.x, lm.y)

    @classmethod
    def from_tuple(cls, tup: tuple[float, float]) -> Self:
        return cls(tup[0], tup[1])

    def midpoint(self, rhs: Self) -> Self:
        return type(self)((self.x + rhs.x) / 2, (self.y + rhs.y) / 2)

    def correct(self, w: int, h: int) -> Self:
        return type(self)(self.x * w, self.y * h)

    def as_tuple(self) -> tuple[float, float] | tuple[float, float, float]:
        return (self.x, self.y)

    def as_tuple_int(self) -> tuple[int, int] | tuple[int, int, int]:
        return (int(self.x), int(self.y))

    def dist(self, rhs: Self) -> float:
        return math.dist(self.as_tuple(), rhs.as_tuple())

    def length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2)

    def dot(self, rhs: Self) -> float:
        return self.x * rhs.x + self.y * rhs.y

    def inside_bounds(self, x1, y1, x2, y2) -> bool:
        return self.x >= x1 and self.x <= x2 and self.y >= y1 and self.y <= y2

    def __str__(self):
        return f"({self.x}, {self.y})"


def average_point(points: list[Point]) -> Point:
    if not points:
        raise ValueError("Cannot calculate the average of an empty list")
    n = len(points)
    return Point(x=sum(p.x for p in points) / n, y=sum(p.y for p in points) / n)


# --- Overlay output -------------------------------------------------------
# Commands are sent as JSON over UDP to the overlay instead of being drawn on the frame.
OVERLAY_ADDR = ("127.0.0.1", int(os.environ.get("OVERLAY_PORT", "45454")))
MIRROR = True  # the camera view is mirrored for the player, like the old flipped frame
TTL_MS = 250  # drawn items vanish by themselves if tracking stops sending updates after this time
_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def dot(id: str, p: Point, srW: int, srH: int, color: str, r: float = 0.006) -> dict:
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


def line(
    id: str, p1: Point, p2: Point, srW: int, srH: int, color: str, width: float = 0.008
) -> dict:
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


def text(
    id: str,
    t: str,
    achor: Point,
    srW: int,
    srH: int,
    color: str,
    size: float = 0.01,
    align: str = "center",
    bg_color: str = "#00000000",
    bold: bool = False,
) -> dict:
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
def findPoints(
    frame: cv2.typing.MatLike,
    detector: Any = vision.PoseLandmarker.create_from_options(options),
) -> dict[str, Point] | None:
    """Returns (leftHand, rightHand, leftShoulder, rightShoulder, centerOfBody)"""
    h, w, _ = frame.shape

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

    timestamp_ms = int((cv2.getTickCount() / cv2.getTickFrequency()) * 1000)

    detection_result = detector.detect_for_video(mp_image, timestamp_ms)

    if detection_result.pose_landmarks:
        for pose_landmarks in detection_result.pose_landmarks:
            left_hand = average_point(
                [
                    Point.from_landmark(pose_landmarks[15]),
                    Point.from_landmark(pose_landmarks[17]),
                    Point.from_landmark(pose_landmarks[19]),
                ]
            ).correct(w, h)
            right_hand = average_point(
                [
                    Point.from_landmark(pose_landmarks[16]),
                    Point.from_landmark(pose_landmarks[18]),
                    Point.from_landmark(pose_landmarks[20]),
                ]
            ).correct(w, h)

            left_shoulder = Point.from_landmark(pose_landmarks[12]).correct(w, h)
            right_shoulder = Point.from_landmark(pose_landmarks[11]).correct(w, h)

            body = average_point(
                [
                    Point.from_landmark(pose_landmarks[11]),
                    Point.from_landmark(pose_landmarks[12]),
                    Point.from_landmark(pose_landmarks[23]),
                    Point.from_landmark(pose_landmarks[24]),
                ]
            ).correct(w, h)

            return {
                "left_hand": left_hand,
                "right_hand": right_hand,
                "left_shoulder": left_shoulder,
                "right_shoulder": right_shoulder,
                "body": body,
            }


# --- Overlay construction -------------------------------------------------
def collect_errors(points: dict[str, Point], w: int, h: int, max_arm=2.2) -> list[str]:
    errors: list[str] = []

    def p(s: str) -> Point:
        return points[s]

    if not p("left_hand").inside_bounds(0, 0, w, h) or not p(
        "right_hand"
    ).inside_bounds(0, 0, w, h):
        errors.append("hands_outside")
    if not p("left_shoulder").inside_bounds(0, 0, w, h) or not p(
        "right_shoulder"
    ).inside_bounds(0, 0, w, h):
        errors.append("shoulders_outside")
    shoulder_dist = p("left_shoulder").dist(p("right_shoulder"))
    MAX_ARM = shoulder_dist * max_arm
    if not p("left_shoulder").inside_bounds(
        MAX_ARM, MAX_ARM, w - MAX_ARM, h - MAX_ARM
    ) or not p("right_shoulder").inside_bounds(
        MAX_ARM, MAX_ARM, w - MAX_ARM, h - MAX_ARM
    ):
        if shoulder_dist + MAX_ARM * 2 > w:
            errors.append("too_close")
        else:
            errors.append("not_middle")
    hand_dist = p("left_hand").dist(p("right_hand"))
    if hand_dist > shoulder_dist:
        errors.append("hands_together")

    return errors


def display_errors(
    errors: list[str], points: dict[str, Point], w: int, h: int
) -> list[dict]:
    items: list[dict] = []

    def p(s: str) -> Point:
        return points[s]

    offset = 0.01
    SIZE = 0.04
    if "too_close" in errors:
        items.append(
            text(
                "too_close",
                "Du står for tæt på kameraet",
                Point(w / 2, offset * h),
                w,
                h,
                "#FFFFFF",
                SIZE,
                "center",
                "#20990000",
            )
        )
        offset += SIZE * 1.5
    if "not_middle" in errors:
        items.append(
            text(
                "not_middle",
                "Du står for ikke i centrum",
                Point(w / 2, offset * h),
                w,
                h,
                "#FFFFFF",
                SIZE,
                "center",
                "#20990000",
            )
        )
        offset += SIZE * 1.5
    if "hands_outside" in errors:
        items.append(
            text(
                "hands_outside",
                "Dine hænder skal være inden for billedet",
                Point(w / 2, offset * h),
                w,
                h,
                "#FFFFFF",
                SIZE,
                "center",
                "#20990000",
            )
        )
        offset += SIZE * 1.5
    if "shoulders_outside" in errors:
        items.append(
            text(
                "shoulders_outside",
                "Dine skuldre skal være inden for billedet",
                Point(w / 2, offset * h),
                w,
                h,
                "#FFFFFF",
                SIZE,
                "center",
                "#20990000",
            )
        )
        offset += SIZE * 1.5
    if "hands_together" in errors:
        items.append(
            text(
                "hands_together",
                "Dine hænder må ikke være mere end 1 skuldrelængde fra hinanden",
                Point(w / 2, offset * h),
                w,
                h,
                "#FFFFFF",
                SIZE,
                "center",
                "#20990000",
            )
        )
        items.append(dot("right_hand", p("right_hand"), w, h, "#ff0000", r=0.01))
        items.append(dot("left_hand", p("left_hand"), w, h, "#ff0000", r=0.01))
        offset += SIZE * 1.5

    return items


def construct(points: dict[str, Point], mode: str, w: int, h: int) -> list[dict]:
    items: list[dict] = []

    def p(s: str) -> Point:
        return points[s]

    # - Setup ---------------------
    if mode == "setup":
        items.append(
            text(
                "top_text",
                "Setup",
                Point(w / 2, 0),
                w,
                h,
                "#FFFFFF",
                bg_color="#71000000",
                size=0.05,
                bold=True,
            )
        )
        errors = collect_errors(points, w, h, max_arm=2.3)
        if "shoulders_outside" in errors:
            items.append(
                text(
                    "helper",
                    "Du er ikke tilstrækeligt synlig for kameraet",
                    Point(w / 2, h / 2 - 0.035 * h),
                    w,
                    h,
                    "#FFFFFF",
                    0.07,
                    "center",
                    "#20800000",
                )
            )
        elif "too_close" in errors:
            items.append(
                text(
                    "helper",
                    "Du står for tæt på kameraet",
                    Point(w / 2, h / 2 - 0.035 * h),
                    w,
                    h,
                    "#FFFFFF",
                    0.07,
                    "center",
                    "#20800000",
                )
            )
        elif "not_middle" in errors:
            items.append(
                text(
                    "helper",
                    "Du bør stille dig nærmere midten for kameraet",
                    Point(w / 2, h / 2 - 0.035 * h),
                    w,
                    h,
                    "#FFFFFF",
                    0.07,
                    "center",
                    "#20800000",
                )
            )
        elif "hands_together" in errors or "hands_outside" in errors:
            items.append(
                text(
                    "helper",
                    "Dine hænder skal være inden for\nén skulder længde af hinanden",
                    Point(w / 2, h / 2 - 0.035 * h),
                    w,
                    h,
                    "#FFFFFF",
                    0.07,
                    "center",
                    "#20800000",
                )
            )
        return items
    # - Setup ---------------------
    if mode == "playing":
        items.append(
            text(
                "top_text",
                "Playing",
                Point(w / 2, 0),
                w,
                h,
                "#FFFFFF",
                bg_color="#71000000",
                size=0.04,
                bold=True,
            )
        )
        return items

    # - Any -----------------------
    items.append(
        line("shoulders", p("left_shoulder"), p("right_shoulder"), w, h, "#B0310874")
    )
    items.append(dot("shoulder1", p("left_shoulder"), w, h, "#3b2fdd"))
    items.append(dot("shoulder2", p("right_shoulder"), w, h, "#3b2fdd"))

    SHOULDER_MID = p("left_shoulder").midpoint(p("right_shoulder"))
    SHOULDER_DIST = p("left_shoulder").dist(p("right_shoulder"))
    HAND_DIST = p("left_hand").dist(p("right_hand"))
    # How much to move it down by
    HMTMIDB = SHOULDER_DIST / 2

    PERP = Point(
        -(p("right_shoulder").y - p("left_shoulder").y),
        p("right_shoulder").x - p("left_shoulder").x,
    )
    PERP_UNIT = PERP / PERP.length()

    CHEST_MID = SHOULDER_MID + (PERP_UNIT * HMTMIDB)
    items.append(dot("chest_mid", CHEST_MID, w, h, "#ffffff"))

    mid_hands = p("left_hand").midpoint(p("right_hand"))
    items.append(
        dot(
            "mid_hands",
            mid_hands,
            w,
            h,
            interpolate_color("#00FF00", "#FF0000", HAND_DIST / SHOULDER_DIST),
        )
    )

    shoulder_to_shoulder = Point(
        p("right_shoulder").x - p("left_shoulder").x,
        p("right_shoulder").y - p("left_shoulder").y,
    )

    shoulder_mid_to_mid_hands = Point(
        SHOULDER_MID.x - mid_hands.x, SHOULDER_MID.y - mid_hands.y
    )

    ANGLE = math.acos(
        shoulder_to_shoulder.dot(shoulder_mid_to_mid_hands)
        / (shoulder_to_shoulder.length() * shoulder_mid_to_mid_hands.length())
    )

    SHOULDER_ANGLE = math.atan2(shoulder_to_shoulder.y, shoulder_to_shoulder.x)

    items.append(
        text(
            "angle",
            f"Vinkel af hammer: {ANGLE}",
            Point(w, 0.03 * h),
            w,
            h,
            "#FFFFFF",
            bg_color="#20000000",
            size=0.02,
            align="left",
        )
    )
    items.append(
        text(
            "shoulder_angle",
            f"Vinkel af skuldre: {SHOULDER_ANGLE}",
            Point(w, 0),
            w,
            h,
            "#FFFFFF",
            bg_color="#20000000",
            size=0.02,
            align="left",
        )
    )

    return items


# --- Handling Game Interaction --------------------------------------------
def pause():
    """Placeholder"""
    pass


def unpause():
    """Placeholder"""
    pass


def get_expected_handposition():
    """Placeholder - gets where the hands should be located relative to the center based on how the in-game player is positioned"""
    pass


# --- Main -----------------------------------------------------------------
def update(
    points: dict[str, Point], mode: str, para: dict[str, Any], w: int, h: int
) -> str:
    errors = collect_errors(points, w, h)
    match mode:
        case "setup":
            items = construct(points, mode, w, h)
            if len(items) == 1:
                items.append(
                    text(
                        "helper",
                        f" {(5 + para['timer'] - time.time()):.1f} ",
                        Point(w / 2, h / 2 - 0.035 * h),
                        w,
                        h,
                        "#FFFFFF",
                        0.07,
                        "center",
                        "#2000FF00",
                    )
                )
                if 5 + para["timer"] - time.time() <= 0.05:
                    para["last_valid"] = points
                    mode = "playing"
            else:
                para["timer"] = time.time()
            send([command("clear"), command("batch", {"items": items})])
        case "playing":
            if len(errors) > 0:
                mode = "fixing"
                pause()
            else:
                send(
                    [
                        command("clear"),
                        command("batch", {"items": construct(points, mode, w, h)}),
                    ]
                )
        case "fixing":
            send(
                [
                    command("clear"),
                    command(
                        "batch",
                        {
                            "items": construct(points, mode, w, h)
                            + display_errors(errors, points, w, h)
                        },
                    ),
                ]
            )
            if len(errors) == 0:
                unpause()
                mode = "playing"
        case "_":
            raise ValueError(f"{mode} is not a valid mode")

    if len(errors) == 0:
        para["last_valid"] = points

    return mode


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
    cap = cv2.VideoCapture(cam_index)
    ret, frame = cap.read()
    if not ret:
        return
    h, w, _ = frame.shape

    mode = "setup"
    para: dict[str, Any] = {
        "timer": time.time(),
        "last_valid": {},
        "expected_handpos": get_expected_handposition(),
    }

    # Main Loop
    while cap.isOpened():
        # Optains Points from frame
        ret, frame = cap.read()
        if not ret:
            continue
        points = findPoints(frame)
        if not points:
            continue

        # runs update
        mode = update(points, mode, para, w, h)

        # if cv2.waitKey(1) == ord("q"):
        #     break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
