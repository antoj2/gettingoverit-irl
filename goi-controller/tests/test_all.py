import math
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from goi_bridge import packets
from goi_bridge.calibration import (Calibration, CalibrationError, compute_right,
                                    compute_stick_axis, stick_angle)
from goi_bridge.config import Config
from goi_bridge.controller import Controller
from goi_bridge.filters import OneEuro
from goi_bridge.mathutil import (q_from_two_vectors, qrotate, qrotate_inv, vlen, wrap180)
from goi_bridge.sim import FakeGame, FakeImu
from goi_bridge.vision import extension_ratio


class MathTests(unittest.TestCase):
    def test_rotation_roundtrip(self):
        q = q_from_two_vectors((0, 0, 1), (1, 0, 0))
        v = (0.2, -0.5, 0.7)
        w = qrotate_inv(q, qrotate(q, v))
        for a, b in zip(v, w):
            self.assertAlmostEqual(a, b, places=9)
        self.assertAlmostEqual(vlen(qrotate(q, v)), vlen(v), places=9)

    def test_wrap(self):
        self.assertAlmostEqual(wrap180(190), -170)
        self.assertAlmostEqual(wrap180(-190), 170)

    def test_packet_roundtrip(self):
        s = packets.parse(packets.pack(7, 1234, 55, (1, 0, 0, 0), (1, 2, 3)))
        self.assertEqual((s.seq, s.t_ms, s.battery), (7, 1234, 55))
        self.assertIsNone(packets.parse(b"garbage"))
        self.assertEqual(packets.PACKET_SIZE, 40)

    def test_one_euro_converges_and_tracks(self):
        f = OneEuro(3.0, 0.02)
        y = 0
        for _ in range(300):
            y = f(10.0, 0.01)
        self.assertAlmostEqual(y, 10.0, places=2)


class GeometryTests(unittest.TestCase):
    def _quats(self, imu, theta, n=30):
        imu.theta = theta
        return [imu.quaternion() for _ in range(n)]

    def test_axis_right_and_angle(self):
        imu = FakeImu(axis=(0.2, -0.8, 0.5), heading_deg=-110)
        axis = compute_stick_axis(self._quats(imu, 90))
        for a, b in zip(axis, imu.axis):
            self.assertAlmostEqual(a, b, delta=0.01)
        right, elev = compute_right(self._quats(imu, 0), axis)
        for theta in (0, 30, 90, 135, 180, 250, 300):
            imu.theta = theta
            ang = stick_angle(imu.quaternion(), axis, right)
            if abs(math.cos(math.radians(theta))) < 1e-3 and abs(math.sin(math.radians(theta))) < 1e-3:
                continue
            self.assertAlmostEqual(wrap180(ang - theta), 0.0, delta=1.0, msg=f"theta={theta}")

    def test_tilted_right_pose_still_gives_heading(self):
        imu = FakeImu(heading_deg=33)
        axis = compute_stick_axis(self._quats(imu, 90))
        right, elev = compute_right(self._quats(imu, 25), axis)     # stick 25 deg above horizontal
        self.assertAlmostEqual(elev, 25, delta=1.5)
        imu.theta = 60
        self.assertAlmostEqual(stick_angle(imu.quaternion(), axis, right), 60, delta=1.0)

    def test_moving_during_calibration_is_rejected(self):
        imu = FakeImu()
        imu.theta = 90
        qs = [imu.quaternion() for _ in range(5)]
        imu.theta = 45
        qs += [imu.quaternion() for _ in range(25)]
        with self.assertRaises(CalibrationError):
            compute_stick_axis(qs)

    def test_pointing_at_screen_returns_none(self):
        imu = FakeImu(heading_deg=0)
        axis = compute_stick_axis(self._quats(imu, 90))
        right, _ = compute_right(self._quats(imu, 0), axis)
        imu.theta, imu.tilt = 45, 0
        imu.tilt = 50.0                       # huge out-of-plane component -> direction ~ along +n
        self.assertIsNone(stick_angle(imu.quaternion(), axis, right))


class VisionMathTests(unittest.TestCase):
    def _lms(self, hand_xy, vis=1.0):
        lms = [SimpleNamespace(x=0.5, y=0.5, visibility=vis) for _ in range(33)]
        lms[11] = SimpleNamespace(x=0.6, y=0.4, visibility=vis)   # subject's left shoulder
        lms[12] = SimpleNamespace(x=0.4, y=0.4, visibility=vis)   # subject's right shoulder
        for i in (16, 18, 20):
            lms[i] = SimpleNamespace(x=hand_xy[0], y=hand_xy[1], visibility=vis)
        return lms

    def test_ratio_scale_invariant(self):
        r, _, _ = extension_ratio(self._lms((0.4, 0.4 + 0.3)), 640, 480)
        # shoulders 0.2*640=128 px apart; hand 0.3*480=144 px below the shoulder
        self.assertAlmostEqual(r, 144 / 128, places=3)

    def test_low_visibility_rejected(self):
        self.assertIsNone(extension_ratio(self._lms((0.4, 0.7), vis=0.2), 640, 480))

    def test_hand_selection(self):
        lms = self._lms((0.4, 0.7))
        self.assertIsNotNone(extension_ratio(lms, 640, 480, "right"))


class EndToEnd(unittest.TestCase):
    def test_wizard_swing_and_release(self):
        ports = dict(imu_port=14210, cmd_port=19876, state_port=19877)
        game = FakeGame(ports["cmd_port"], ports["state_port"], r0=3.0)
        imu = FakeImu(port=ports["imu_port"], axis=(0.3, 0.9, 0.3), heading_deg=70)
        calib = os.path.join(tempfile.mkdtemp(), "cal.json")
        cfg = Config(calib_file=calib, countdown=0.2, hold_time=0.5, imu_bind="127.0.0.1", **ports)
        logs = []
        ctl = Controller(cfg, out=logs.append)

        def hook(step, text):
            imu.theta = {"up": 90.0, "right": 0.0}[step]
        ctl.on_step = hook
        game.start(); imu.start(); ctl.start()
        try:
            ctl.wait_for_game()
            self.assertAlmostEqual(ctl.r0, 3.0, places=2)
            self.assertTrue(ctl.wait_for_imu(5))
            self.assertTrue(ctl.run_startup_wizard(), logs)
            self.assertTrue(ctl.cal.ready)
            self.assertEqual(game.method, 3)

            # swing 0 -> 300 deg over 5 s (60 deg/s) and compare the commanded angle with truth
            errs, t0 = [], time.monotonic()
            while time.monotonic() - t0 < 5.0:
                imu.theta = 60.0 * (time.monotonic() - t0)
                time.sleep(0.05)
                ctl.housekeeping()
                if game.last_h and time.monotonic() - game.last_h[0] - 0 >= 0:
                    truth = imu.theta
                    errs.append(abs(wrap180(game.last_h[1] - truth)))
            self.assertGreater(len(game.commands), 300)
            self.assertLess(sorted(errs)[int(len(errs) * 0.95)], 8.0, f"angle errors too large: {max(errs):.1f}")
            self.assertAlmostEqual(game.last_h[2], 3.0, places=2)          # no camera -> r0 * 1.0

            # unplug the stick -> override must be released
            imu.paused = True
            time.sleep(0.5)
            ctl.housekeeping()
            time.sleep(0.1)
            self.assertFalse(game.override)
            # and it resumes
            imu.paused = False
            time.sleep(0.3)
            self.assertTrue(game.override)

            # camera extension: fully extended -> ext_max * r0 (after the slew limit)
            ctl.cal.rho_near, ctl.cal.rho_far = 0.5, 1.5
            for _ in range(60):
                ctl.set_ratio(1.5)
                time.sleep(0.02)
            self.assertAlmostEqual(game.last_h[2], 3.0 * 1.5, delta=0.1)
            # camera lost for >0.5 s -> falls back towards r0 * ext_mult
            time.sleep(1.2)
            self.assertAlmostEqual(game.last_h[2], 3.0, delta=0.1)
        finally:
            ctl.shutdown(); imu.close(); game.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
