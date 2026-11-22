# pyright: basic
import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Self

# Windows: OpenCV's default camera backend (MSMF) can take 20+ s to open a camera because it initialises hardware transforms. This must be set BEFORE cv2 is imported.
if sys.platform == "win32":
    os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import math
import time
import socket, json

HAMMER_LENGTH_GAME_UNIT = 1.96
"""The length of the hammer in game units"""
HAMMER_LENGTH_SHOULDER_DIST = 2.6
"""The length of the hammer in shoulder distances"""
SHOULDER_DIST_TO_GAME_UNIT = HAMMER_LENGTH_GAME_UNIT / HAMMER_LENGTH_SHOULDER_DIST
"""
Approx 2.6 shoulder lengths go into length of hammer, length of hammer approx 1.96 game units

Multiply shoulder_length with SHOULDER_DIST_TO_GAME_UNIT to get game_unit
"""
GAME_UNIT_TO_SHOULDER_DIST = HAMMER_LENGTH_SHOULDER_DIST / HAMMER_LENGTH_GAME_UNIT
"""
Approx 2.6 shoulder lengths go into length of hammer, length of hammer approx 1.96 game units

Multiply game_unit with SHOULDER_DIST_TO_GAME_UNIT to get shoulder_length
"""
THRESHOLD_DIST = 0.08
"""The number of shoulder lengths away the hands must be from the correct position to be accepted"""

# --- PoseLandmarker ---------------------------------------------------------
MODEL_NAME = "pose_landmarker_full.task"


def find_model() -> Path:
    """
    Locates the pose model independently of the current working directory.

    Order: $POSE_MODEL, next to this file, the project folder (skeleton-tracking/), the repository root,
    the current directory.
    """
    env = os.environ.get("POSE_MODEL")
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return path
        raise FileNotFoundError(f"POSE_MODEL peger på en fil der ikke findes: {path}")

    here = Path(__file__).resolve().parent
    candidates = [here / MODEL_NAME]
    candidates += [parent / MODEL_NAME for parent in here.parents[:3]]
    candidates.append(Path.cwd() / MODEL_NAME)
    for path in candidates:
        if path.is_file():
            return path

    searched = "\n".join(f"  {c}" for c in candidates)
    raise FileNotFoundError(
        f"Kunne ikke finde {MODEL_NAME}. Jeg ledte her:\n{searched}\n"
        "Læg filen et af de steder, eller sæt miljøvariablen POSE_MODEL til den fulde sti."
    )


_detector: Any = None


def get_detector() -> Any:
    """Creates the PoseLandmarker on first use (not at import time, so a missing model gives a readable error)."""
    global _detector
    if _detector is None:
        # The model is passed as bytes, not as a path: on Windows MediaPipe can fail to open a path that contains non-ASCII characters (e.g. C:\\Users\\Søren\\...).
        model = find_model().read_bytes()
        _detector = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_buffer=model),
                running_mode=vision.RunningMode.VIDEO,
                min_pose_detection_confidence=0.8,
                min_tracking_confidence=0.8,
            )
        )
    return _detector


# --- Classes---------------------------------------------------------------
# - Points ---------------------------------------
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

    def __sub__(self, other: Self) -> Self:
        return type(self)(self.x - other.x, self.y - other.y)

    def __mul__(self, other: float) -> Self:
        return type(self)(self.x * other, self.y * other)

    def __div__(self, other: float) -> Self:
        return type(self)(self.x / other, self.y / other)

    def __neg__(self) -> Self:
        return type(self)(-self.x, -self.y)

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


class Points:
    """The collection of points that make up relevant body features"""

    left_hand: Point
    """Position of left hand in screen pixels"""
    right_hand: Point
    """Position of right hand in screen pixels"""
    left_shoulder: Point
    """Position of left shoulder in screen pixels"""
    right_shoulder: Point
    """Position of right shoulder in screen pixels"""

    hands: Point
    """The average position of hands in screen pixels"""
    rotation_center: Point
    """The center of rotation in screen pixels"""
    shoulder_mid: Point
    """The center of rotation in screen pixels"""
    hand_dist: float
    """The distance between the two hands in screen pixels"""
    shoulder_dist: float
    """The distance between the two shoulders in screen pixels"""
    hands_angle: float
    """The angle of the hammer based on position of hands, center of rotation and rotation of shoulders relative to the camera rotation"""

    def __init__(self, points: dict[str, Point]) -> None:
        self.left_hand = points["left_hand"]
        self.right_hand = points["right_hand"]
        self.left_shoulder = points["left_shoulder"]
        self.right_shoulder = points["right_shoulder"]

        self.hands = self.left_hand.midpoint(self.right_hand)
        self.shoulder_mid = self.left_shoulder.midpoint(self.right_shoulder)
        self.shoulder_dist = self.left_shoulder.dist(self.right_shoulder)
        self.hand_dist = self.left_hand.dist(self.right_hand)

        # - Rotation Center ---
        # How much to move it down by
        HMTMIDB = self.shoulder_dist * 0.4

        PERP = Point(
            -(self.right_shoulder.y - self.left_shoulder.y),
            self.right_shoulder.x - self.left_shoulder.x,
        )
        PERP_UNIT = PERP / PERP.length()

        self.rotation_center = self.shoulder_mid + (PERP_UNIT * HMTMIDB)

        # - Hands angle -------
        HAND_VECTOR = self.hands - self.rotation_center

        SHOULDER_ANGLE = math.atan2(
            self.right_shoulder.y - self.left_shoulder.y,
            self.right_shoulder.x - self.left_shoulder.x,
        )

        HAND_ANGLE = math.atan2(HAND_VECTOR.y, HAND_VECTOR.x)

        self.hands_angle = HAND_ANGLE - (SHOULDER_ANGLE)

    def normalized(self) -> Self:
        return type(self)(
            {
                "left_hand": (self.left_hand - self.rotation_center)
                / self.shoulder_dist,
                "right_hand": (self.right_hand - self.rotation_center)
                / self.shoulder_dist,
                "left_shoulder": (self.left_shoulder - self.rotation_center)
                / self.shoulder_dist,
                "right_shoulder": (self.right_shoulder - self.rotation_center)
                / self.shoulder_dist,
            }
        )


# - Game State -----------------------------------
class State:
    """The current state of the game"""

    up_time: float
    """Time in seconds since the game started"""
    screen_dim: tuple[float, float]
    """Screen/window width and height in pixels"""
    overiding: bool
    """True if a command is currently overriding the cursor"""
    block_pause: bool
    """True if the player cannot pause/unpause on their own"""
    paused: bool
    """True if the controller has paused the game"""
    time_scale: float
    """The game's time scale"""
    menu_is_open: bool | None
    """The game's own "menu is open" flag"""
    focused: bool
    """True if the game window is focused"""
    run_in_background: bool
    """True if the game keep running when not focused"""
    cursor_pos: Point | None
    """In-game cursor position, world units"""
    cursor_screen: Point | None
    """In-game cursor position, screen pixels"""
    hammer_tip: Point | None
    """Hammer tip position, world units"""
    rotation_point: Point | None
    """Centre of rotation (the hinge pivot), world units"""
    hammer_extension: float | None
    """Hammer extension (slider), world units"""
    hinge_angle: float | None
    """Hinge angle, degrees"""
    object_pos: Point | None
    """Position of the object carrying the hinge"""
    object_rotation: float | None
    """Rotation of the object carrying the hinge"""
    cursor_rigidbody_pos: Point | None
    """Cursor rigidbody position"""
    cursor_rigidbody_vel: Point | None
    """Cursor rigidbody velocity"""
    cursor_target: Point | None
    """The game's own cursor target"""
    # method: Any
    # """The currently selected `X` method"""

    def __init__(self, state: dict[str, Any]) -> None:
        self.up_time = state["t"]
        self.screen_dim = (state["sw"], state["sh"])
        self.overiding = state["ov"]
        self.block_pause = state["bp"]
        self.paused = state["pz"]
        self.pause_menu = state.get("pm")
        self.time_scale = state["ts"]
        self.menu_is_open = state.get("mp")
        self.focused = state["fo"]
        self.run_in_background = state["bg"]
        self.cursor_pos = Point(state["fx"], state["fy"]) if state.get("fx") else None
        self.cursor_screen = (
            Point(state["mx"], state["my"]) if state.get("mx") else None
        )
        self.hammer_tip = Point(state["tx"], state["ty"]) if state.get("tx") else None
        self.rotation_point = (
            Point(state["cx"], state["cy"]) if state.get("cx") else None
        )
        self.hammer_extension = state.get("st")
        self.hinge_angle = state.get("ha")
        self.object_pos = Point(state["bx"], state["by"]) if state.get("bx") else None
        self.object_rotation = state.get("br")
        self.cursor_rigidbody_pos = (
            Point(state["rx"], state["ry"]) if state.get("rx") else None
        )
        self.cursor_rigidbody_vel = (
            Point(state["vx"], state["vy"]) if state.get("vx") else None
        )
        self.cursor_target = (
            Point(state["wx"], state["wy"]) if state.get("wx") else None
        )
        # self.method = state["mt"]


# - CodeCell -------------------------------------
class CodeCell:
    """The data recived from potential CodeCell"""

    angle: float
    """The angle relative to the direction of gravity in radians"""
    charge: float
    """Percentage charge of the CodeCell"""

    def __init__(self, state: list[str]) -> None:
        # Current firmware: "sequence,millis,tilt,battery". Older firmware: "tilt,battery".
        if len(state) >= 4:
            self.angle = float(state[2])
            self.charge = float(state[3])
        elif len(state) == 2:
            self.angle = float(state[0])
            self.charge = float(state[1])
        else:
            raise ValueError(f"Ukendt CodeCell-pakke: {state!r}")


# - Parameters -----------------------------------
class Parameters:
    """Common shared paramters"""

    state: State | None = None
    """The current game state"""
    codecell: CodeCell | None
    """The current state of the CodeCell if it is available"""
    timer: float = time.time()
    """Used for measuring time"""
    expected_handpos: Point | None = None
    """The point where the game expects your hands to be located"""
    last_handpos: Point | None = None
    """The point where your hands were last frame"""
    use_codecell: bool = False
    """The point where the game expects your hands to be located"""

    def __init__(self, para: dict[str, Any] = {}) -> None:
        self.state = para.get("state")
        self.timer = para.get("timer") or time.time()
        self.expected_handpos = para.get("expected_handpos")
        self.use_codecell = para.get("use_codecell") or False


para = Parameters()
"""The globally shared parameters"""


# --- Color ----------------------------------------------------------------
def interpolate_color(color1: str, color2: str, factor: float) -> str:
    """Interpolates between two hex colors."""
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


# --- Overlay output -------------------------------------------------------
# Commands are sent as JSON over UDP to the overlay instead of being drawn on the frame.
OVERLAY_ADDR = ("127.0.0.1", int(os.environ.get("OVERLAY_PORT", "45454")))
MIRROR = True  # the camera view is mirrored for the player
# Note to self: pretty much none of the code has been tested with this set to False. I just noticied it now and I've realized that a lot of the math I've been struggling with might be because the math I did assumed plus is plus and minus is minus, which it isn't always when this is True.
TTL_MS = 1000  # drawn items vanish by themselves if tracking stops sending updates after this time
_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def dot(
    id: str, p: Point, srW: int, srH: int, color: str, r: float = 0.006
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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


def arrow(
    id: str,
    start: Point,
    end: Point,
    srW: int,
    srH: int,
    color: str,
    width: float = 0.008,
) -> dict[str, Any]:
    x1 = start.x / srW
    x2 = end.x / srW
    if MIRROR:
        x1 = 1 - x1
        x2 = 1 - x2
    return {
        "id": id,
        "type": "line",
        "x1": x1,
        "y1": start.y / srH,
        "x2": x2,
        "y2": end.y / srH,
        "width": width,
        "color": color,
        "arrow": True,
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
) -> dict[str, Any]:
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


def command(cmd: str, input: dict = {}) -> dict[str, Any]:
    input["cmd"] = cmd
    return input


def json_command(cmd: dict[str, Any] | list[dict[str, Any]] = {}) -> str:
    if isinstance(cmd, dict):
        return json.dumps(cmd, separators=(",", ":"))
    return "\n".join(json.dumps(ting, separators=(",", ":")) for ting in cmd)


def overlay(commands: list[dict[str, Any]]) -> None:
    if commands:
        _sock.sendto(json_command(commands).encode() + b"\n", OVERLAY_ADDR)


# --- Camera ---------------------------------------------------------------
_CAMERA_BACKENDS: dict[str, int] = {
    "any": cv2.CAP_ANY,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "avfoundation": cv2.CAP_AVFOUNDATION,
    "v4l2": cv2.CAP_V4L2,
}


def _camera_backends(override: str | None = None) -> list[tuple[str, int]]:
    """
    The OpenCV camera backends to try, in order.

    Windows: DirectShow first (opens fast), then Media Foundation. macOS: AVFoundation, then OpenCV's choice.
    Linux: OpenCV's own choice. Override with --backend or $CAMERA_BACKEND (any, dshow, msmf, avfoundation, v4l2).
    """
    name = (override or os.environ.get("CAMERA_BACKEND", "")).strip().lower()
    if name:
        if name in _CAMERA_BACKENDS:
            return [(name, _CAMERA_BACKENDS[name])]
        print(f"Ukendt kamera-backend '{name}', bruger standard. Gyldige: {', '.join(_CAMERA_BACKENDS)}")

    if sys.platform == "win32":
        return [("dshow", cv2.CAP_DSHOW), ("msmf", cv2.CAP_MSMF)]
    if sys.platform == "darwin":
        return [("avfoundation", cv2.CAP_AVFOUNDATION), ("any", cv2.CAP_ANY)]
    return [("any", cv2.CAP_ANY)]


def _camera_help() -> str:
    if sys.platform == "darwin":
        return "Tip: Giv programmet du kører fra (Terminal, VS Code, ...) adgang under Systemindstillinger → Anonymitet og sikkerhed → Kamera, og start så programmet igen. Kamera index 0 kan være en iPhone (Continuity Camera); prøv 1 eller 2."
    if sys.platform == "win32":
        return "Tip: Tjek Indstillinger → Anonymitet og sikkerhed → Kamera (tillad skrivebordsapps), og at ingen anden app (Teams, Zoom, ...) bruger kameraet. Du kan også prøve --backend msmf eller dshow."
    return "Tip: Tjek at /dev/video* findes, og at du har rettigheder til det (gruppen 'video')."


def open_camera(
    index: int,
    backend: str | None = None,
    wait: float = 5.0,
) -> tuple[cv2.VideoCapture, cv2.typing.MatLike] | None:
    """
    Opens camera `index` and returns it together with a first frame, or None if no frame could be read.

    Waiting for a real frame (instead of trusting isOpened()) matters on macOS and Windows: the camera can
    report "open" while it is still starting up, or while the OS permission prompt is waiting for an answer.
    """
    for name, flag in _camera_backends(backend):
        cap = cv2.VideoCapture(index, flag)
        if not cap.isOpened():
            cap.release()
            continue
        deadline = time.monotonic() + wait
        while True:
            ret, frame = cap.read()
            if ret and frame is not None:
                print(f"Kamera {index} åbnet ({name}).")
                return cap, frame
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        cap.release()
    return None


def choose_camera(
    index: int | None = None, backend: str | None = None
) -> tuple[cv2.VideoCapture, cv2.typing.MatLike] | None:
    """
    Opens the camera given by --camera / $CAMERA, or asks the user for an index.
    Returns the open camera and a first frame, or None (after printing why).
    """
    if index is None and "CAMERA" in os.environ:
        try:
            index = int(os.environ["CAMERA"])
        except ValueError:
            print("CAMERA er ikke et rigtigt tal!")
            return None

    if index is not None:
        cam = open_camera(index, backend)
        if cam is None:
            print("Det er IKKE et rigtigt kamera!")
            print(_camera_help())
        return cam

    while True:
        print("Hvilket kamera index vil du gerne bruge?")
        try:
            raw = input()
        except (EOFError, KeyboardInterrupt):
            return None
        try:
            idx = int(raw)
        except ValueError:
            print("Det er ikke et rigtigt tal")
            continue

        cam = open_camera(idx, backend)
        if cam is not None:
            return cam
        print("Det kamera eksisterer ikke (eller kunne ikke åbnes)")
        print(_camera_help())


# --- Find Points ----------------------------------------------------------
def findPoints(
    frame: cv2.typing.MatLike,
    detector: Any = None,
) -> Points | None:
    """Returns the position of relevant body markers from skeleton tracking"""
    if detector is None:
        detector = get_detector()
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

            return Points(
                {
                    "left_hand": left_hand,
                    "right_hand": right_hand,
                    "left_shoulder": left_shoulder,
                    "right_shoulder": right_shoulder,
                }
            )


# --- Overlay construction -------------------------------------------------
def collect_errors(points: Points, w: int, h: int, max_arm=2.2) -> list[str]:
    errors: list[str] = []

    if not points.left_hand.inside_bounds(
        0, 0, w, h
    ) or not points.right_hand.inside_bounds(0, 0, w, h):
        errors.append("hands_outside")
    if not points.left_shoulder.inside_bounds(
        0, 0, w, h
    ) or not points.right_shoulder.inside_bounds(0, 0, w, h):
        errors.append("shoulders_outside")
    MAX_ARM = points.shoulder_dist * max_arm
    if not points.left_shoulder.inside_bounds(
        MAX_ARM, MAX_ARM, w - MAX_ARM, h - MAX_ARM
    ) or not points.right_shoulder.inside_bounds(
        MAX_ARM, MAX_ARM, w - MAX_ARM, h - MAX_ARM
    ):
        if points.shoulder_dist + MAX_ARM * 2 > w:
            errors.append("too_close")
        else:
            errors.append("not_middle")
    hand_dist = points.left_hand.dist(points.right_hand)
    if (
        hand_dist > points.shoulder_dist + para.last_handpos.dist(points.hands)
        if para.last_handpos
        else 0
    ):
        errors.append("hands_together")
    if para.expected_handpos:
        exp_hands = (
            para.expected_handpos * GAME_UNIT_TO_SHOULDER_DIST * points.shoulder_dist
            + points.rotation_center
        )
        dist = points.hands.dist(exp_hands) / points.shoulder_dist
        if dist > THRESHOLD_DIST:
            errors.append("hands_wrong")

    return errors


def display_errors(
    errors: list[str], points: Points, w: int, h: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

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
        items.append(dot("right_hand", points.right_hand, w, h, "#ff0000", r=0.01))
        items.append(dot("left_hand", points.left_hand, w, h, "#ff0000", r=0.01))
        offset += SIZE * 1.5
    elif "hands_wrong" in errors:
        items.append(
            text(
                "hands_wrong",
                "Dine hænder skal matche der hvor de var, før du pausede",
                Point(w / 2, offset * h),
                w,
                h,
                "#FFFFFF",
                SIZE,
                "center",
                "#20990000",
            )
        )
        if para.expected_handpos:
            exp_hands = (
                para.expected_handpos
                * GAME_UNIT_TO_SHOULDER_DIST
                * points.shoulder_dist
                + points.rotation_center
            )
            dist = points.hands.dist(exp_hands) / points.shoulder_dist
            color = interpolate_color("#00FF00", "#FF0000", dist)
            items.append(arrow("hands_arrow", points.hands, exp_hands, w, h, color))
        offset += SIZE * 1.5

    return items


def construct(points: Points, mode: str, w: int, h: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

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
        if not para.state:
            items.append(
                text(
                    "helper",
                    "Du er ikke inden i spillet\nEller du har ikke installeret pluginet",
                    Point(w / 2, h / 2 - 0.035 * h),
                    w,
                    h,
                    "#FFFFFF",
                    0.07,
                    "center",
                    "#20800000",
                )
            )
        elif not para.state.rotation_point:
            items.append(
                text(
                    "helper",
                    "Du er i menuen\nDu skal gå ind i et spil",
                    Point(w / 2, h / 2 - 0.035 * h),
                    w,
                    h,
                    "#FFFFFF",
                    0.07,
                    "center",
                    "#20800000",
                )
            )
        elif "shoulders_outside" in errors:
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
        elif para.expected_handpos:
            exp_hands = (
                para.expected_handpos
                * GAME_UNIT_TO_SHOULDER_DIST
                * points.shoulder_dist
                + points.rotation_center
            )
            dist = points.hands.dist(exp_hands) / points.shoulder_dist
            color = interpolate_color("#00FF00", "#FF0000", dist)
            if dist > THRESHOLD_DIST:
                items.append(
                    text(
                        "helper",
                        "Dine hænder skal være tæt på\nden aktuelle position\ni spillet",
                        Point(w / 2, h / 2 - 0.035 * h),
                        w,
                        h,
                        "#FFFFFF",
                        0.07,
                        "center",
                        "#20800000",
                    )
                )

                arrow_offset = exp_hands - points.hands
                arrow_offset = (
                    arrow_offset
                    / arrow_offset.length()
                    * (dist * points.shoulder_dist - 10)
                )
                items.append(
                    arrow(
                        "hands_arrow",
                        points.hands,
                        points.hands + arrow_offset,
                        w,
                        h,
                        color[:1] + "80" + color[3:],
                        0.006,
                    )
                )

            # This could be replaced by a "circle" function instead of two dots, it would then have an outline paramter and a fill paramter.
            items.append(
                dot("expected_hands_outline", exp_hands, w, h, "#000000", 0.019)
            )
            items.append(dot("expected_hands", exp_hands, w, h, color, 0.015))
            items.append(dot("hands", points.hands, w, h, color))
        return items
    # - Playing -------------------
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
        line(
            "shoulders", points.left_shoulder, points.right_shoulder, w, h, "#B0310874"
        )
    )
    items.append(dot("shoulder1", points.left_shoulder, w, h, "#3b2fdd"))
    items.append(dot("shoulder2", points.right_shoulder, w, h, "#3b2fdd"))

    items.append(dot("rot_cen", points.rotation_center, w, h, "#ffffff"))

    items.append(
        dot(
            "mid_hands",
            points.hands,
            w,
            h,
            interpolate_color(
                "#00FF00", "#FF0000", points.hand_dist / points.shoulder_dist
            ),
        )
    )

    return items


# --- Handling Game Interaction --------------------------------------------
rx_game = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_game.bind(("127.0.0.1", 9877))
rx_game.settimeout(5)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_game(msg: str):
    tx.sendto(msg.encode(), ("127.0.0.1", 9876))
    return


def game_state(time_out: float = 500) -> State | None:
    """Return the NEWEST packet: block for one, then drain everything queued behind it.
    If nothing arrives, it returns None."""
    time_out /= 1000
    while True:
        rx_game.settimeout(time_out)
        try:
            last: dict = json.loads(rx_game.recv(65536))
            break
        except socket.timeout:
            return None
    rx_game.setblocking(False)
    try:
        while True:
            last: dict = json.loads(rx_game.recv(65536))
    except BlockingIOError:
        pass
    rx_game.settimeout(5)
    if last.get("fo") is False and not last.get("bg", False):
        print(
            "[game window unfocused and runInBackground is off - it may stall]",
            flush=True,
        )
    return State(last)


def prevent_player_pause(state: State | None = None):
    """Prevents the player from pausing or unpausing the game themselfs"""
    if not state or state.block_pause == False:
        send_game("set block_pause 1")
    return


def pause(state: State | None = None):
    """Pauses the game without the pause menu"""
    if not state or state.paused == False:
        send_game("pause 1")
    return


def unpause(state: State | None = None):
    """Unpauses the game if there is no pause menu"""
    if not state or state.paused == True:
        send_game("pause 0")
    return


def pause_menu(state: State | None = None):
    """Pauses the game with the pause menu"""
    if not state or state.pause_menu == False:
        send_game("pause_menu 1")
    return


def unpause_menu(state: State | None = None):
    """Unpauses the game from the pause menu"""
    if not state or state.pause_menu == True:
        send_game("pause_menu 0")
    return


def place_hammer(ang: float, ext: float):
    """Put the hammer at an angle (degrees, 0 = pointing right, counter-clockwise) and a distance from the hinge pivot (in world units and degrees)."""
    send_game(f"H {ang} {max(1, ext)}")
    return


def release_player(state: State | None = None):
    """Stop overriding. The game's own mouse input takes over again."""
    if not state or state.overiding == True:
        send_game("off")
    if not state or state.block_pause == True:
        send_game("set block_pause 0")
    unpause(state)
    pause_menu(state)
    return


def get_expected_handposition(rotation_point: Point, hammer_tip: Point) -> Point:
    """Gets where the hands should be located relative to the center based on how the in-game player is positioned"""
    hammer = hammer_tip - rotation_point
    ang = math.atan2(hammer.x, hammer.y)
    return hammer - (Point(math.sin(ang), math.cos(ang)) * HAMMER_LENGTH_GAME_UNIT)


# --- CodeCell -------------------------------------------------------------
CODECELL_SSID = "SchimonsTuffESP32v2"
CODECELL_PORT = 4210
CODECELL_AP_ADDR = (os.environ.get("CODECELL_IP", "192.168.4.1"), CODECELL_PORT)
"""The CodeCell is the Wi-Fi access point; 192.168.4.1 is always its own address."""

rx_codecell = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_codecell.bind(("0.0.0.0", CODECELL_PORT))
rx_codecell.settimeout(5)

# The CodeCell sends to whatever address last said hello to it (the computer's address is assigned by the CodeCell's DHCP server and is not always 192.168.4.2). Hellos go out on their own send-only socket: on Windows a UDP socket that both sends and receives can get spurious "connection reset" errors.
_hello = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_last_hello = float("-inf")


def _say_hello() -> None:
    """Tells the CodeCell where to send its data. Rate limited to once a second; never raises."""
    global _last_hello
    now = time.monotonic()
    if now - _last_hello < 1.0:
        return
    _last_hello = now
    try:
        _hello.sendto(b"hello", CODECELL_AP_ADDR)
    except OSError:
        pass  # not connected to the CodeCell's network (yet)


def _codecell_help() -> str:
    tips = f"Tip: Er computeren forbundet til wifi-netværket '{CODECELL_SSID}', og er CodeCellen tændt? "
    if sys.platform == "win32":
        return tips + "Windows Firewall skal tillade indgående UDP på port 4210 (se VinkelFraJordCodecell/README.md); sæt gerne netværket til 'Privat'."
    if sys.platform == "darwin":
        return tips + "Svar Tillad hvis macOS spørger om Python må modtage indgående forbindelser, og tillad Terminal under Systemindstillinger → Anonymitet og sikkerhed → Lokalt netværk (macOS 15 og nyere)."
    return tips + "Tjek også firewallen (UDP port 4210 ind)."


def _parse_codecell(data: bytes) -> "CodeCell | None":
    try:
        return CodeCell(data.decode("utf-8").strip().split(","))
    except (ValueError, UnicodeDecodeError):
        return None  # not a CodeCell packet


def codecell(time_out: float = 250) -> CodeCell | None:
    """Return the NEWEST packet: block for one, then drain everything queued behind it.
    If nothing arrives, it returns None."""
    _say_hello()
    time_out /= 1000
    rx_codecell.settimeout(time_out)
    try:
        last = _parse_codecell(rx_codecell.recvfrom(256)[0])
    except socket.timeout:
        return None
    rx_codecell.setblocking(False)
    try:
        while True:
            newer = _parse_codecell(rx_codecell.recvfrom(256)[0])
            if newer is not None:
                last = newer
    except BlockingIOError:
        pass
    rx_codecell.settimeout(5)

    return last


def find_codecell(timeout: float = 3.0) -> CodeCell | None:
    """Waits up to `timeout` seconds for the CodeCell (it needs a moment to learn the address)."""
    deadline = time.monotonic() + timeout
    while True:
        cell = codecell(250)
        if cell is not None:
            return cell
        if time.monotonic() >= deadline:
            return None


CODECELL_DEAD_ZONE = math.radians(15)
"""Within this angle of horizontal, the CodeCell can't tell the two hammer directions apart"""


def _wrap(a: float) -> float:
    """Wraps an angle in radians to [-pi, pi)"""
    return (a + math.pi) % math.tau - math.pi


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class HammerTracker:
    """Decides which end of the hammer is the tip.

    The hands (relative to the rotation center) only give the hammer *line*; the tip is at
    `hands_angle + k*pi`. k = 0 is the old `hammer_inward == True` (tip angle == hands_angle),
    k = 1 is the old `hammer_inward == False`.

    Per frame:
      1. The hand vector (shoulder lengths, relative to the rotation center) is despiked and
         smoothed with a One Euro filter.
      2. A dead zone around the pivot with hysteresis (`dead_in` / `dead_out`) decides whether
         the hand angle can be trusted at all.
      3. Outside it, k is kept unless the other candidate is clearly nearer the predicted tip
         angle for `switch_frames` frames in a row (anything else is a glitch). While such a
         switch is pending the tip is held still.
      4. Inside it, the tip coasts (angular velocity decaying). On exit the candidate nearest the
         coasted tip wins: a straight pass flips k and keeps the tip, a bounce-back keeps k.
         Near ties are broken by which way the hand swept around the pivot.
      5. The CodeCell votes into a leaky accumulator; only persistent disagreement flips the tip.
      6. The output is slew-limited to `max_rate` rad/s.
    """

    MIN_DT = 1 / 240
    MAX_DT = 0.2

    def __init__(
        self,
        *,
        dead_in: float = 0.12,
        dead_out: float = 0.20,
        min_cutoff: float = 1.2,
        beta: float = 6.0,
        d_cutoff: float = 1.0,
        max_hand_speed: float = 10.0,
        jump_margin: float = 0.15,
        max_rejects: int = 3,
        omega_decay: float = 0.08,
        switch_margin: float = 0.8,
        switch_frames: int = 3,
        tie_margin: float = 0.5,
        sensor_dead_zone: float = CODECELL_DEAD_ZONE,
        sensor_tau: float = 0.6,
        sensor_flip: float = 0.2,
        sensor_max_omega: float = 6.0,
        max_rate: float = 20.0,
        initial_inward: bool = False,
    ) -> None:
        self.dead_in = dead_in
        """Enter the dead zone when the hand is closer than this to the pivot (shoulder lengths)"""
        self.dead_out = dead_out
        """Leave the dead zone when the hand is further than this from the pivot (shoulder lengths)"""
        self.min_cutoff = min_cutoff
        """One Euro: cutoff in Hz when the hand is still. Lower = smoother but laggier"""
        self.beta = beta
        """One Euro: how quickly the cutoff opens up with hand speed"""
        self.d_cutoff = d_cutoff
        """One Euro: cutoff in Hz of the speed estimate"""
        self.max_hand_speed = max_hand_speed
        """Fastest believable hand speed in shoulder lengths per second; faster jumps are glitches"""
        self.jump_margin = jump_margin
        """Extra slack in shoulder lengths on top of `max_hand_speed` before a sample is a glitch"""
        self.max_rejects = max_rejects
        """A 'glitch' that persists this many frames is accepted as real"""
        self.omega_decay = omega_decay
        """Time constant in seconds with which the coasting angular velocity dies out"""
        self.switch_margin = switch_margin
        """Radians the current candidate must be further from the predicted tip than the other"""
        self.switch_frames = switch_frames
        """Frames the other candidate must stay clearly nearer before it is accepted"""
        self.tie_margin = tie_margin
        """If both candidates are this close to equally far from the coasted tip, use the sweep direction"""
        self.sensor_dead_zone = sensor_dead_zone
        """Radians from horizontal within which the CodeCell (or the tip) is ignored as a vote"""
        self.sensor_tau = sensor_tau
        """Leak time constant in seconds of the CodeCell vote accumulator"""
        self.sensor_flip = sensor_flip
        """Accumulated disagreement (full-strength seconds) needed to flip the tip"""
        self.sensor_max_omega = sensor_max_omega
        """CodeCell votes are ignored while the tip turns faster than this (rad/s); accelerations fool the sensor"""
        self.max_rate = max_rate
        """Fastest the commanded angle may turn, rad/s"""
        self.initial_inward = initial_inward
        """The branch to start with when the game can't tell us"""

        self.hand_angle: float = 0.0
        """Smoothed hand angle in radians (same frame as `Points.hands_angle`)"""
        self.hand_radius: float = 0.0
        """Smoothed hand distance from the pivot in shoulder lengths"""
        self.reset()

    # - Public ---------------------------------------
    @property
    def inward(self) -> bool:
        """True while the tip angle equals the hand angle (the old `para.hammer_inward`)"""
        return self._k == 0

    @property
    def in_dead_zone(self) -> bool:
        return self._dead

    def reset(self) -> None:
        """Forget everything. The next `update` starts from scratch"""
        self._t: float | None = None
        self._raw: tuple[float, float] | None = None
        self._pos: tuple[float, float] | None = None
        self._vel = (0.0, 0.0)
        self._rejects = 0
        self._k = 0 if self.initial_inward else 1
        self._phi = 0.0
        self._out = 0.0
        self._omega = 0.0
        self._dead = False
        self._last_out: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._pending = 0
        self._acc = 0.0
        self._sync: float | None = None

    def resync(self, tip: Point | None = None, pivot: Point | None = None) -> None:
        """Restart from the in-game pose (world units). Without it, starts from `initial_inward`.
        The tip angle is taken as atan2 in world space, 0 = right, counter-clockwise,
        the same convention as `place_hammer`."""
        self.reset()
        if tip is not None and pivot is not None:
            self._sync = math.atan2(tip.y - pivot.y, tip.x - pivot.x)

    def update(
        self,
        hands_angle: float,
        radius: float,
        codecell_angle: float | None = None,
        now: float | None = None,
    ) -> float:
        """Angle in degrees for `place_hammer` (0 = right, counter-clockwise).

        `hands_angle`: `Points.hands_angle` in radians.

        `radius`: distance from rotation center to the hands in shoulder lengths.

        `codecell_angle`: `CodeCell.angle` in radians (0 = up, pi = down) or None.
        """
        now = time.perf_counter() if now is None else now
        raw = (radius * math.cos(hands_angle), radius * math.sin(hands_angle))

        if self._pos is None or self._raw is None or self._t is None:
            self._seed(raw, now)
            return math.degrees(self._out)

        dt = _clamp(now - self._t, self.MIN_DT, self.MAX_DT)
        self._t = now

        # Despike + smooth
        jump = math.hypot(raw[0] - self._raw[0], raw[1] - self._raw[1])
        limit = self.max_hand_speed * dt * (self._rejects + 1) + self.jump_margin
        if jump > limit and self._rejects < self.max_rejects:
            self._rejects += 1  # glitch: keep the previous smoothed position
        else:
            if jump > limit:  # it persisted, so it's real don't glide there slowly
                self._pos, self._vel = raw, (0.0, 0.0)
            else:
                self._one_euro(raw, dt)
            self._raw = raw
            self._rejects = 0

        x, y = self._pos
        r, theta = math.hypot(x, y), math.atan2(y, x)
        self.hand_angle, self.hand_radius = theta, r

        # Dead zone with hysteresis
        just_exited = False
        if not self._dead and r < self.dead_in:
            self._dead = True
        elif self._dead and r > self.dead_out:
            self._dead = False
            just_exited = True
            self._resolve_exit(theta)

        # Track or coast
        if self._dead:
            self._omega *= math.exp(-dt / self.omega_decay)
            self._phi = _wrap(self._phi + self._omega * dt)
        else:
            if not just_exited:
                self._track(theta, dt)
            self._last_out = (self._pos, self._vel)

        # CodeCell
        if codecell_angle is not None:
            self._vote(codecell_angle, dt)

        # Slew limit
        step = self.max_rate * dt
        self._out = _wrap(self._out + _clamp(_wrap(self._phi - self._out), -step, step))
        return math.degrees(self._out)

    # - Internals ------------------------------------
    def _seed(self, raw: tuple[float, float], now: float) -> None:
        self._t, self._raw, self._pos, self._vel = now, raw, raw, (0.0, 0.0)
        r, theta = math.hypot(*raw), math.atan2(raw[1], raw[0])
        self.hand_angle, self.hand_radius = theta, r
        if self._sync is not None:
            self._phi = self._sync
            near0 = abs(_wrap(theta - self._phi))
            near1 = abs(_wrap(theta + math.pi - self._phi))
            self._k = 0 if near0 <= near1 else 1
            self._sync = None
        else:
            self._phi = _wrap(theta + self._k * math.pi)
        self._out = self._phi
        self._dead = r < self.dead_in
        if not self._dead:
            self._last_out = (self._pos, self._vel)

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1 / (math.tau * cutoff)
        return 1 / (1 + tau / dt)

    def _one_euro(self, raw: tuple[float, float], dt: float) -> None:
        assert self._raw is not None and self._pos is not None
        vx = (raw[0] - self._raw[0]) / dt
        vy = (raw[1] - self._raw[1]) / dt
        ad = self._alpha(dt, self.d_cutoff)
        self._vel = (
            self._vel[0] + ad * (vx - self._vel[0]),
            self._vel[1] + ad * (vy - self._vel[1]),
        )
        a = self._alpha(dt, self.min_cutoff + self.beta * math.hypot(*self._vel))
        self._pos = (
            self._pos[0] + a * (raw[0] - self._pos[0]),
            self._pos[1] + a * (raw[1] - self._pos[1]),
        )

    def _track(self, theta: float, dt: float) -> None:
        """Outside the dead zone: stay on the current branch unless the evidence is clear"""
        cands = (theta, theta + math.pi)
        pred = self._phi + _clamp(self._omega * dt, -0.5, 0.5)
        d = (abs(_wrap(cands[0] - pred)), abs(_wrap(cands[1] - pred)))
        best = 0 if d[0] <= d[1] else 1

        if best != self._k and d[self._k] - d[best] > self.switch_margin:
            self._pending += 1
        else:
            self._pending = 0

        if self._pending >= self.switch_frames:
            self._k, self._pending, self._omega = best, 0, 0.0
            self._phi = _wrap(cands[best])
        elif (
            self._pending
        ):  # disputed: hold still instead of whipping the hammer around
            self._omega *= math.exp(-dt / self.omega_decay)
            self._phi = _wrap(pred)
        else:
            target = cands[self._k]
            measured = _wrap(target - self._phi) / dt
            self._omega = _clamp(
                self._omega + 0.5 * (measured - self._omega),
                -self.max_rate,
                self.max_rate,
            )
            self._phi = _wrap(target)

    def _resolve_exit(self, theta: float) -> None:
        """Leaving the dead zone: pick the candidate nearest the coasted tip"""
        cands = (theta, theta + math.pi)
        off = (_wrap(cands[0] - self._phi), _wrap(cands[1] - self._phi))
        k = 0 if abs(off[0]) <= abs(off[1]) else 1

        if (
            abs(abs(off[0]) - abs(off[1])) < self.tie_margin
            and self._last_out
            and off[0]
        ):
            # Roughly a 90° corner. Turn the tip the way the hand swept around the pivot.
            s = self._sweep_sign()
            if s:
                k = 0 if _sign(off[0]) == s else 1

        self._k, self._pending, self._omega = k, 0, 0.0
        self._phi = _wrap(cands[k])

    def _sweep_sign(self) -> int:
        """+1 if the hand passed the pivot counter-clockwise (angle increasing), -1 if clockwise"""
        assert self._last_out is not None and self._pos is not None
        (px, py), (vx, vy) = self._last_out
        if (
            math.hypot(vx, vy) >= 0.5
        ):  # direction of travel is trustworthy: use the line
            return _sign(px * vy - py * vx)
        return _sign(
            px * self._pos[1] - py * self._pos[0]
        )  # else the entry -> exit chord

    def _vote(self, codecell_angle: float, dt: float) -> None:
        """The CodeCell says whether the tip is up or down. Persistent disagreement flips the tip."""
        self._acc *= math.exp(-dt / self.sensor_tau)
        lim = math.sin(self.sensor_dead_zone)
        tip_up = math.sin(self._phi)  # >0: we currently claim the tip points up
        cell_up = math.cos(codecell_angle)  # >0: the CodeCell says it points up
        if (
            abs(self._omega) < self.sensor_max_omega
            and abs(tip_up) > lim
            and abs(cell_up) > lim
        ):
            self._acc -= (
                tip_up * cell_up * dt
            )  # strength = how far from horizontal both are
        cap = self.sensor_flip * 1.25
        self._acc = _clamp(self._acc, -cap, cap)
        if self._acc > self.sensor_flip:
            self._phi = _wrap(self._phi + math.pi)
            self._k ^= 1
            self._pending, self._omega = 0, 0.0
            self._acc = (
                -self.sensor_flip
            )  # needs a full swing back to flip again (hysteresis)


hammer_tracker = HammerTracker()
"""Decides which end of the hammer is the tip"""


# --- Main -----------------------------------------------------------------
def update(points: Points, mode: str, w: int, h: int) -> str:
    errors = collect_errors(points, w, h)
    para.state = game_state()
    para.codecell = codecell() if para.use_codecell else None
    para.expected_handpos = (
        -get_expected_handposition(para.state.rotation_point, para.state.hammer_tip)
        if para.state and para.state.rotation_point and para.state.hammer_tip
        else None
    )
    prevent_player_pause(para.state)
    unpause_menu(para.state)

    match mode:
        case "setup":
            if para.state and para.state.rotation_point:
                pause(para.state)
            # unpause(para.state)
            items = construct(points, mode, w, h)

            if not any(item.get("id") == "helper" for item in items):
                items.append(
                    text(
                        "helper",
                        f" {(5 + para.timer - time.time()):.1f} ",
                        Point(w / 2, h / 2 - 0.035 * h),
                        w,
                        h,
                        "#FFFFFF",
                        0.07,
                        "center",
                        "#2000FF00",
                    )
                )
                if 5 + para.timer - time.time() <= 0.05:
                    hammer_tracker.resync(
                        para.state.hammer_tip if para.state else None,
                        para.state.rotation_point if para.state else None,
                    )
                    mode = "playing"
            else:
                para.timer = time.time()
            overlay([command("clear"), command("batch", {"items": items})])
        case "playing":
            if len(errors) > 1 or (len(errors) == 1 and errors[0] != "hands_wrong"):
                for error in errors:
                    if error != "hands_wrong":
                        print("Error: " + error)
                mode = "fixing"
                pause(para.state)
            else:
                unpause(para.state)
                overlay(
                    [
                        command("clear"),
                        command("batch", {"items": construct(points, mode, w, h)}),
                    ]
                )
                ANGLE = hammer_tracker.update(
                    points.hands_angle,
                    (points.hands - points.rotation_center).length()
                    / points.shoulder_dist,
                    para.codecell.angle if para.codecell else None,
                )
                # Use the smoothed hand vector so angle and extension agree with each other
                HANDS_DIST = hammer_tracker.hand_radius * SHOULDER_DIST_TO_GAME_UNIT
                # Inward: ANGLE == hands_angle, so cos = 1 and EXTENSION = L - r
                # Outward: ANGLE == hands_angle + 180, so cos = -1 and EXTENSION = L + r
                EXTENSION = HAMMER_LENGTH_GAME_UNIT - HANDS_DIST * math.cos(
                    hammer_tracker.hand_angle - math.radians(ANGLE)
                )
                place_hammer(ANGLE, EXTENSION)
        case "fixing":
            overlay(
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
                unpause(para.state)
                hammer_tracker.resync(
                    para.state.hammer_tip if para.state else None,
                    para.state.rotation_point if para.state else None,
                )
                mode = "playing"
        case "_":
            raise ValueError(f"{mode} is not a valid mode")

    return mode


MAX_READ_FAILURES = 100
"""Consecutive failed camera reads (about 2 s) before the program gives up."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="skeleton-tracking")
    parser.add_argument(
        "-c",
        "--camera",
        type=int,
        help="kamera index (alternativ til miljøvariablen CAMERA; virker i alle shells)",
    )
    parser.add_argument(
        "--backend",
        choices=sorted(_CAMERA_BACKENDS),
        help="OpenCV kamera-backend (standard afhænger af styresystemet)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Loads the pose model first, so a missing model is reported before anything else happens
    try:
        get_detector()
    except FileNotFoundError as e:
        print(e)
        return

    # Optains Camera and the first frame
    cam = choose_camera(args.camera, args.backend)
    if cam is None:
        return
    cap, frame = cam
    h, w, _ = frame.shape

    mode = "setup"

    if find_codecell():
        para.use_codecell = True
        print("Bruger CodeCell til at korrigere vinkler.")
    else:
        print("Ingen CodeCell fundet, kører uden den.")
        print(_codecell_help())

    # Main Loop
    failures = 0
    try:
        while cap.isOpened():
            # Optains Points from frame
            ret, frame = cap.read()
            if not ret:
                failures += 1
                if failures >= MAX_READ_FAILURES:
                    print("Mistede forbindelsen til kameraet (kunne ikke læse billeder).")
                    print(_camera_help())
                    break
                time.sleep(0.02)
                continue
            failures = 0
            points = findPoints(frame)
            if not points:
                continue

            # runs update
            mode = update(points, mode, w, h)
            para.last_handpos = points.hands
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        release_player()
        overlay([command("clear")])
        print("\nStopped")


if __name__ == "__main__":
    main()