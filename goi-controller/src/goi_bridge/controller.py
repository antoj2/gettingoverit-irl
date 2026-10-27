"""Fuses IMU angle (+ optional camera extension) and drives the game plugin."""
import math
import threading
import time
from collections import deque

from .calibration import (Calibration, CalibrationError, compute_right, compute_stick_axis,
                          default_path, stick_angle)
from .config import Config
from .filters import OneEuro
from .game import GameLink
from .imu import ImuReceiver
from .mathutil import clamp, wrap180

DEFAULT_RHO = (0.5, 1.6)     # used until the camera has been calibrated
FALLBACK_R0 = 3.0            # used if the game never reports its geometry


class Controller:
    def __init__(self, cfg: Config, out=print):
        self.cfg = cfg
        self.out = out
        self.calib_path = cfg.calib_file or default_path()
        self.cal = Calibration.load(self.calib_path)
        if not cfg.reuse_right:
            self.cal.right = None             # screen-right is re-measured every session by default
        if cfg.recalibrate:
            self.cal.stick_axis = None
        self.link = GameLink(cfg.game_host, cfg.cmd_port, cfg.state_port, cfg.dry_run)
        self.imu = ImuReceiver(cfg.imu_bind, cfg.imu_port, self._on_sample)
        self.angle_f = OneEuro(cfg.min_cutoff, cfg.beta)
        self.ext_f = OneEuro(cfg.ext_min_cutoff, cfg.ext_beta)
        self.on_step = lambda step, text: self.out(text)   # tests hook this to move a fake stick
        self.r0 = cfg.r0
        self.paused = False
        self.calibrating = False
        self.quit = False
        self.active = False                 # an override is currently being driven in the game
        self._busy = threading.Lock()
        self._imu_ok = None
        # angle state
        self._last_t_ms = None
        self._last_raw = None
        self._unwrapped = 0.0
        self.angle_out = None
        self.ext_out = None
        self._ext_cur = None
        # camera state
        self.ratio = None
        self.ext_norm = None
        self._ext_time = 0.0
        self._ratio_time = 0.0
        self._ratios = deque(maxlen=300)    # (time, ratio) for calibration

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        self.link.start()
        self.imu.start()
        self.link.send_off()
        self.link.send_method(self.cfg.method)

    def shutdown(self):
        self.quit = True
        try:
            self.link.send_off()
        finally:
            self.imu.close()
            self.link.close()

    def wait_for_game(self):
        """Measure the hammer's rest length from the game. Falls back to cfg.r0 / FALLBACK_R0."""
        if self.r0 is not None:
            return
        if self.cfg.dry_run:
            self.r0 = FALLBACK_R0
            return
        self.out(f"Waiting for the game (up to {self.cfg.state_wait:.0f}s)... load into a level.")
        if self.link.wait_for_player(self.cfg.state_wait):
            self.link.send_off()
            time.sleep(0.5)
            r = self.link.measure_r0()
            if r and r > 0.05:
                self.r0 = r
                self.out(f"Hammer rest length r0 = {r:.3f} world units.")
                return
        self.r0 = FALLBACK_R0
        self.out(f"No game state received; using r0 = {FALLBACK_R0} (set --r0 to change).")

    def wait_for_imu(self, timeout=60.0):
        self.out(f"Waiting for the ESP32 on UDP {self.cfg.imu_port} "
                 "(join its Wi-Fi network; see README)...")
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.quit:
            if self.imu.latest is not None:
                self.out("IMU stream detected.")
                return True
            time.sleep(0.1)
        self.out("No IMU packets received.")
        return False

    # ------------------------------------------------------------------ IMU -> game
    def _on_sample(self, s):
        if self.paused or self.calibrating or not self.cal.ready:
            self._release()
            return
        cfg = self.cfg
        if self._last_t_ms is None:
            dt = 0.01
        else:
            dt = ((s.t_ms - self._last_t_ms) & 0xFFFFFFFF) / 1000.0
            if dt <= 0.0 or dt > 0.2:
                dt = 0.01
        self._last_t_ms = s.t_ms

        raw = stick_angle(s.q, self.cal.stick_axis, self.cal.right, cfg.flip_vertical, cfg.min_inplane)
        if raw is not None:
            raw += cfg.angle_offset
            if self._last_raw is None:
                self._unwrapped = raw
                self.angle_f.reset()
            else:
                self._unwrapped += wrap180(raw - self._last_raw)
            self._last_raw = raw
        if self._last_raw is None:
            return                          # no usable angle yet
        angle = self.angle_f(self._unwrapped, dt) % 360.0
        ext = self._extension(dt)
        self.angle_out, self.ext_out = angle, ext
        if not self.active:
            self.out("Override ON")
        self.active = True
        self.link.send_polar(angle, ext)

    def _extension(self, dt):
        r0 = self.r0 or FALLBACK_R0
        norm = self.ext_norm if (time.monotonic() - self._ext_time) < 0.5 else None
        if norm is None:
            mult = self.cfg.ext_mult
        else:
            mult = self.cfg.ext_min + (self.cfg.ext_max - self.cfg.ext_min) * norm
        target = r0 * mult
        if self._ext_cur is None:
            self._ext_cur = target
        else:
            step = self.cfg.ext_slew * r0 * dt
            self._ext_cur += clamp(target - self._ext_cur, -step, step)
        return self._ext_cur

    def _release(self):
        if self.active:
            self.active = False
            self.link.send_off()
            self.out("Override OFF")

    # ------------------------------------------------------------------ camera -> extension
    def set_ratio(self, ratio):
        """Called from the camera loop with the raw hand/shoulder ratio (or None if not visible)."""
        now = time.monotonic()
        if ratio is None:
            return
        self.ratio = ratio
        self._ratios.append((now, ratio))
        near, far = self.cal.rho_near, self.cal.rho_far
        if near is None or far is None:
            near, far = DEFAULT_RHO
        norm = clamp((ratio - near) / (far - near), 0.0, 1.0)
        dt = now - self._ratio_time
        if dt <= 0.0 or dt > 0.5:
            dt = 1.0 / 30.0
        self._ratio_time = now
        self.ext_norm = self.ext_f(norm, dt)
        self._ext_time = now

    # ------------------------------------------------------------------ housekeeping
    def housekeeping(self):
        ok = self.imu.age() < self.cfg.imu_timeout
        if ok != self._imu_ok:
            if self._imu_ok is not None:
                self.out("IMU stream resumed." if ok else "IMU stream lost - releasing the cursor.")
            self._imu_ok = ok
        if not ok:
            self._release()

    def status_line(self):
        s = self.imu.latest
        a = "  --  " if self.angle_out is None else f"{self.angle_out:6.1f}"
        e = "  --  " if self.ext_out is None else f"{self.ext_out:5.2f}"
        r = "--" if self.ratio is None else f"{self.ratio:.2f}"
        bat = "--" if s is None else f"{s.battery}%"
        flags = ("PAUSED " if self.paused else "") + ("CALIB " if self.calibrating else "") + \
                ("ON " if self.active else "off")
        return (f"angle {a} deg | ext {e} | cam ratio {r} | rx {self.imu.received} lost {self.imu.lost} "
                f"| bat {bat} | {flags}   ")

    # ------------------------------------------------------------------ calibration wizard
    def _countdown(self, seconds):
        end = time.monotonic() + seconds
        while True:
            rem = end - time.monotonic()
            if rem <= 0:
                break
            self.out(f"   {math.ceil(rem)}...")
            time.sleep(min(1.0, rem))

    def _collect(self, duration):
        """Collect distinct IMU samples for `duration` seconds."""
        out, last = [], None
        end = time.monotonic() + duration
        while time.monotonic() < end:
            s = self.imu.latest
            if s is not None and s.seq != last:
                last = s.seq
                out.append(s)
            time.sleep(0.004)
        return out

    def calibrate_up(self):
        self.calibrating = True
        try:
            self.on_step("up", "STEP 1: hold the stick STRAIGHT UP (vertical) and keep it still.")
            self._countdown(self.cfg.countdown)
            samples = self._collect(self.cfg.hold_time)
            self.cal.stick_axis = compute_stick_axis([s.q for s in samples])
            self.cal.right = None           # the right-heading depends on the axis
            self.cal.save(self.calib_path)
            self.out("   stick axis measured.")
        finally:
            self.calibrating = False

    def calibrate_right(self):
        if self.cal.stick_axis is None:
            raise CalibrationError("measure the vertical pose first (press u).")
        self.calibrating = True
        try:
            self.on_step("right", "STEP 2: point the stick to your RIGHT, parallel to the screen, and keep it still.")
            self._countdown(self.cfg.countdown)
            samples = self._collect(self.cfg.hold_time)
            self.cal.right, elev = compute_right([s.q for s in samples], self.cal.stick_axis)
            self.cal.save(self.calib_path)
            msg = "   screen-right heading measured."
            if abs(elev) > 20:
                msg += f" (note: the stick was {elev:+.0f} deg from horizontal; fine, only its heading matters)"
            self.out(msg)
        finally:
            self.calibrating = False

    def _ratio_window(self, duration):
        t0 = time.monotonic()
        time.sleep(duration)
        vals = sorted(r for (t, r) in list(self._ratios) if t >= t0)
        if len(vals) < 8:
            raise CalibrationError("the camera did not see your arm/shoulders (need hand and both shoulders in view).")
        return vals[len(vals) // 2]

    def calibrate_camera(self):
        self.calibrating = True
        try:
            self.on_step("near", "CAMERA STEP A: pull the stick hand in close to your shoulder (arm tucked).")
            self._countdown(self.cfg.countdown)
            near = self._ratio_window(self.cfg.hold_time)
            self.on_step("far", "CAMERA STEP B: extend your arm fully forward/outward with the stick.")
            self._countdown(self.cfg.countdown)
            far = self._ratio_window(self.cfg.hold_time)
            if far - near < 0.2:
                raise CalibrationError(f"near ({near:.2f}) and far ({far:.2f}) are too similar; "
                                       "move your arm through a larger range.")
            self.cal.rho_near, self.cal.rho_far = near, far
            self.cal.save(self.calib_path)
            self.out(f"   camera range measured: near {near:.2f}, far {far:.2f}.")
        finally:
            self.calibrating = False

    def run_startup_wizard(self):
        try:
            if self.cal.stick_axis is None:
                self.calibrate_up()
            if self.cal.right is None:
                self.calibrate_right()
            if self.cfg.camera is not None and (self.cfg.calibrate_camera or self.cal.rho_near is None):
                self.calibrate_camera()
            self.out("Calibration complete. Commands: r=recenter right, u=redo vertical, c=camera range, "
                     "p=pause, e=remeasure r0, s=status, q=quit")
            return True
        except CalibrationError as e:
            self.out(f"Calibration failed: {e}  (press u / r / c to retry)")
            return False

    # ------------------------------------------------------------------ interactive commands
    def handle_command(self, cmd):
        cmd = cmd.strip().lower()
        if not cmd:
            return
        c = cmd[0]
        if c == "q":
            self.quit = True
        elif c == "p":
            self.paused = not self.paused
            if self.paused:
                self._release()
            self.out("Paused (real mouse works)." if self.paused else "Resumed.")
        elif c == "s":
            self.out(self.status_line())
        elif c == "h" or c == "?":
            self.out("u=redo vertical pose | r=recenter right | c=camera range | e=remeasure r0 | "
                     "p=pause/resume | s=status | q=quit")
        elif c in "urce":
            threading.Thread(target=self._run_task, args=(c,), daemon=True).start()
        else:
            self.out("unknown command (h for help)")

    def _run_task(self, c):
        if not self._busy.acquire(blocking=False):
            self.out("busy with another calibration step...")
            return
        try:
            if c == "u":
                self.calibrate_up()
                self.calibrate_right()
            elif c == "r":
                self.calibrate_right()
            elif c == "c":
                if self.cfg.camera is None:
                    self.out("camera is not enabled (start with --camera auto).")
                else:
                    self.calibrate_camera()
            elif c == "e":
                was = self.paused
                self.paused = True
                self._release()
                time.sleep(0.6)
                r = self.link.measure_r0()
                if r and r > 0.05:
                    self.r0 = r
                    self.out(f"r0 = {r:.3f}")
                else:
                    self.out("no game state; r0 unchanged.")
                self.paused = was
        except CalibrationError as e:
            self.out(f"Calibration failed: {e}")
        finally:
            self._busy.release()
