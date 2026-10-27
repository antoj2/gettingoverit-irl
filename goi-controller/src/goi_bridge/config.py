from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class Config:
    # network
    imu_bind: str = "0.0.0.0"
    imu_port: int = 4210
    game_host: str = "127.0.0.1"
    cmd_port: int = 9876
    state_port: int = 9877
    method: int = 3                    # plugin override method ("X n"); 3 = cursor transform
    state_wait: float = 15.0           # seconds to wait for the game before using --r0 fallback
    # hammer geometry
    r0: Optional[float] = None         # pivot->tip distance in world units (measured from the game if None)
    ext_mult: float = 1.0              # extension (x r0) when the camera is not providing one
    ext_min: float = 0.6               # extension (x r0) with arm tucked
    ext_max: float = 1.5               # extension (x r0) with arm fully extended
    ext_slew: float = 4.0              # max extension change, in r0 per second
    # angle
    flip_vertical: bool = False
    angle_offset: float = 0.0          # manual trim, degrees
    min_inplane: float = 0.25
    min_cutoff: float = 3.0            # One Euro (Hz) for the angle
    beta: float = 0.02
    imu_timeout: float = 0.25          # release the override if the IMU is silent this long (s)
    # camera (optional)
    camera: Optional[Union[int, str]] = None   # None = off, "auto", or an index
    hand: str = "right"
    cam_width: int = 640
    cam_height: int = 480
    cam_fps: int = 30
    cam_confidence: float = 0.6
    model: Optional[str] = None
    no_preview: bool = False
    ext_min_cutoff: float = 1.0        # One Euro for the camera extension (0..1 scale)
    ext_beta: float = 1.0
    # calibration
    calib_file: Optional[str] = None
    recalibrate: bool = False          # redo the vertical-pose (stick axis) step
    reuse_right: bool = False          # keep the saved "right" heading (only valid with a magnetometer rotation vector)
    calibrate_camera: bool = False
    countdown: float = 4.0
    hold_time: float = 1.5
    # misc
    dry_run: bool = False
    verbose: bool = False
    hotkeys: bool = False
