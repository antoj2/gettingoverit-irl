"""Command line entry point:  goi-bridge [options]   (or: python -m goi_bridge)"""
import argparse
import queue
import sys
import threading
import time

from .config import Config
from .controller import Controller


def build_parser():
    d = Config()
    p = argparse.ArgumentParser(prog="goi-bridge",
                                description="Bridge ESP32 stick (+ optional camera) -> Getting Over It hammer.")
    n = p.add_argument_group("network")
    n.add_argument("--imu-port", type=int, default=d.imu_port, help="UDP port the ESP32 sends to (4210)")
    n.add_argument("--imu-bind", default=d.imu_bind)
    n.add_argument("--game-host", default=d.game_host)
    n.add_argument("--cmd-port", type=int, default=d.cmd_port, help="plugin listen port (9876)")
    n.add_argument("--state-port", type=int, default=d.state_port, help="plugin state port (9877)")
    n.add_argument("--method", type=int, default=d.method, help="plugin override method, X n (3)")
    n.add_argument("--dry-run", action="store_true", help="do not send anything to the game")

    g = p.add_argument_group("hammer")
    g.add_argument("--r0", type=float, help="rest pivot->tip distance (world units); default: measured from the game")
    g.add_argument("--ext-mult", type=float, default=d.ext_mult, help="extension x r0 when no camera (1.0)")
    g.add_argument("--ext-min", type=float, default=d.ext_min, help="extension x r0, arm tucked (0.6)")
    g.add_argument("--ext-max", type=float, default=d.ext_max, help="extension x r0, arm extended (1.5)")
    g.add_argument("--ext-slew", type=float, default=d.ext_slew, help="max extension change, r0/s (4)")
    g.add_argument("--flip-vertical", action="store_true", help="invert up/down (if the hammer goes the wrong way)")
    g.add_argument("--angle-offset", type=float, default=d.angle_offset, help="trim, degrees")
    g.add_argument("--min-cutoff", type=float, default=d.min_cutoff, help="angle smoothing at rest, Hz (3.0); lower = smoother")
    g.add_argument("--beta", type=float, default=d.beta, help="angle smoothing relaxation when moving (0.02)")
    g.add_argument("--imu-timeout", type=float, default=d.imu_timeout)

    c = p.add_argument_group("camera (optional)")
    c.add_argument("--camera", help="'auto' or a device index; omit to run without the camera")
    c.add_argument("--hand", choices=["right", "left"], default=d.hand, help="hand holding the stick")
    c.add_argument("--cam-width", type=int, default=d.cam_width)
    c.add_argument("--cam-height", type=int, default=d.cam_height)
    c.add_argument("--cam-fps", type=int, default=d.cam_fps)
    c.add_argument("--cam-confidence", type=float, default=d.cam_confidence)
    c.add_argument("--model", help="path to pose_landmarker_full.task")
    c.add_argument("--no-preview", action="store_true")

    k = p.add_argument_group("calibration")
    k.add_argument("--calib-file")
    k.add_argument("--recalibrate", action="store_true", help="redo the vertical-pose step")
    k.add_argument("--reuse-right", action="store_true", help="reuse the saved screen-right heading")
    k.add_argument("--calibrate-camera", action="store_true")
    k.add_argument("--countdown", type=float, default=d.countdown, help="seconds before each measurement (4)")
    k.add_argument("--hold-time", type=float, default=d.hold_time)

    p.add_argument("--hotkeys", action="store_true", help="global hotkeys F8/F9/F10 (needs pynput)")
    p.add_argument("-v", "--verbose", action="store_true", help="live status line")
    return p


def config_from_args(a) -> Config:
    cam = a.camera
    if cam is not None and cam != "auto":
        cam = int(cam)
    return Config(
        imu_bind=a.imu_bind, imu_port=a.imu_port, game_host=a.game_host, cmd_port=a.cmd_port,
        state_port=a.state_port, method=a.method, r0=a.r0, ext_mult=a.ext_mult, ext_min=a.ext_min,
        ext_max=a.ext_max, ext_slew=a.ext_slew, flip_vertical=a.flip_vertical,
        angle_offset=a.angle_offset, min_cutoff=a.min_cutoff, beta=a.beta, imu_timeout=a.imu_timeout,
        camera=cam, hand=a.hand, cam_width=a.cam_width, cam_height=a.cam_height, cam_fps=a.cam_fps,
        cam_confidence=a.cam_confidence, model=a.model, no_preview=a.no_preview,
        calib_file=a.calib_file, recalibrate=a.recalibrate, reuse_right=a.reuse_right,
        calibrate_camera=a.calibrate_camera, countdown=a.countdown, hold_time=a.hold_time,
        dry_run=a.dry_run, verbose=a.verbose, hotkeys=a.hotkeys)


def _stdin_reader(cmds):
    if not sys.stdin or not sys.stdin.isatty():
        return
    for line in sys.stdin:
        cmds.put(line)


def main(argv=None):
    cfg = config_from_args(build_parser().parse_args(argv))
    out = lambda msg: print(msg, flush=True)
    try:
        ctl = Controller(cfg, out)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    cmds = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(cmds,), daemon=True).start()
    if cfg.hotkeys:
        from .hotkeys import start_hotkeys
        start_hotkeys(cmds.put, out)

    tracker = None
    if cfg.camera is not None:
        try:
            from .vision import HandTracker
            tracker = HandTracker(cfg, out)
            tracker.open()
        except Exception as e:
            out(f"Camera disabled: {e}")
            tracker = None
            cfg.camera = None

    ctl.start()

    def startup():
        ctl.wait_for_game()
        if ctl.wait_for_imu():
            with ctl._busy:
                ctl.run_startup_wizard()
    threading.Thread(target=startup, daemon=True).start()

    last_status = 0.0
    try:
        while not ctl.quit:
            while True:
                try:
                    ctl.handle_command(cmds.get_nowait())
                except queue.Empty:
                    break
            if tracker is not None:
                try:
                    ctl.set_ratio(tracker.step())
                except Exception as e:
                    out(f"Camera stopped: {e}")
                    tracker.close()
                    tracker = None
                    continue
                key = tracker.pressed()
                if key and key in "urcpq":
                    ctl.handle_command(key)
            else:
                time.sleep(0.02)
            ctl.housekeeping()
            if cfg.verbose and time.monotonic() - last_status > 0.25:
                last_status = time.monotonic()
                print("\r" + ctl.status_line(), end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if tracker is not None:
            tracker.close()
        ctl.shutdown()
        print("\nBridge stopped; cursor released.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
