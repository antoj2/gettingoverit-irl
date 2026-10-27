"""Calibration data + the geometry that turns an IMU quaternion into a hammer angle.

Frames
  sensor : fixed to the CodeCell on the stick
  world  : the IMU's own frame, Z = up (gravity). Yaw is arbitrary / magnetic.
Screen plane = the vertical plane spanned by `right` (horizontal, world) and world Z.
Angle convention matches the game's `H` command: 0 deg = right, 90 deg = up, CCW.
"""
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional, Tuple

from .mathutil import (qrotate, qrotate_inv, mean_unit_vector, angle_between_deg,
                       vnormalize)

Vec = Tuple[float, float, float]


class CalibrationError(Exception):
    pass


def default_path() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "goi-controller", "calibration.json")


@dataclass
class Calibration:
    stick_axis: Optional[Vec] = None    # stick direction expressed in the SENSOR frame
    right: Optional[Vec] = None         # horizontal unit vector, WORLD frame ("screen right")
    rho_near: Optional[float] = None    # camera: hand-shoulder distance / shoulder width, arm tucked
    rho_far: Optional[float] = None     # same, arm fully extended

    @property
    def ready(self) -> bool:
        return self.stick_axis is not None and self.right is not None

    @classmethod
    def load(cls, path: str) -> "Calibration":
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return cls()
        def vec(k):
            v = d.get(k)
            return tuple(float(x) for x in v) if isinstance(v, list) and len(v) == 3 else None
        def num(k):
            v = d.get(k)
            return float(v) if isinstance(v, (int, float)) else None
        return cls(vec("stick_axis"), vec("right"), num("rho_near"), num("rho_far"))

    def save(self, path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError as e:
            print(f"[calibration] could not save {path}: {e}")


def _check_spread(vectors, mean, max_spread_deg, what):
    spread = max(angle_between_deg(v, mean) for v in vectors)
    if spread > max_spread_deg:
        raise CalibrationError(f"the stick moved too much while measuring the {what} "
                               f"({spread:.0f} deg spread, limit {max_spread_deg:.0f}). Hold it still and retry.")


def compute_stick_axis(quats, max_spread_deg=10.0) -> Vec:
    """Stick held vertically: the stick axis in the sensor frame is world-up seen from the sensor."""
    if len(quats) < 10:
        raise CalibrationError("not enough IMU samples (is the CodeCell streaming?)")
    ups = [qrotate_inv(q, (0.0, 0.0, 1.0)) for q in quats]
    mean = mean_unit_vector(ups)
    _check_spread(ups, mean, max_spread_deg, "vertical pose")
    return mean


def compute_right(quats, axis: Vec, max_spread_deg=10.0) -> Tuple[Vec, float]:
    """Stick pointing to the player's right: its horizontal heading defines screen-right.

    Returns (right_vector, elevation_deg). Elevation is only informational (the stick does not
    have to be perfectly horizontal; only its heading matters).
    """
    if len(quats) < 10:
        raise CalibrationError("not enough IMU samples (is the CodeCell streaming?)")
    ds = [qrotate(q, axis) for q in quats]
    mean = mean_unit_vector(ds)
    _check_spread(ds, mean, max_spread_deg, "sideways pose")
    horiz = math.hypot(mean[0], mean[1])
    if horiz < 0.35:
        raise CalibrationError("the stick is pointing almost straight up/down; point it to your right.")
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, mean[2]))))
    return vnormalize((mean[0], mean[1], 0.0)), elevation


def stick_angle(q, axis: Vec, right: Vec, flip_vertical=False, min_inplane=0.25) -> Optional[float]:
    """Hammer angle in degrees (0 = right, 90 = up, CCW), or None if the stick points
    (almost) straight at / away from the screen so the in-plane angle is ill-defined."""
    d = qrotate(q, axis)
    x = d[0] * right[0] + d[1] * right[1] + d[2] * right[2]
    y = -d[2] if flip_vertical else d[2]
    if math.hypot(x, y) < min_inplane:
        return None
    return math.degrees(math.atan2(y, x))
