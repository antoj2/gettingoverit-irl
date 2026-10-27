"""Fake ESP32 and fake game, so the whole pipeline can be tested without hardware.

  python -m goi_bridge.sim imu     # a stick that swings through the vertical plane
  python -m goi_bridge.sim game    # a fake game plugin: prints the H commands it receives
"""
import json
import math
import random
import socket
import sys
import threading
import time

from . import packets
from .mathutil import (q_from_axis_angle, q_from_two_vectors, qmul, qnormalize, vnormalize)


class FakeImu(threading.Thread):
    """Streams packets for a stick whose sensor-frame axis is `axis` and whose world heading
    (the direction of "screen right") is `heading_deg`."""

    def __init__(self, host="127.0.0.1", port=4210, hz=100, axis=(0.3, 0.9, 0.3),
                 heading_deg=70.0, noise_deg=0.15):
        super().__init__(daemon=True, name="fake-imu")
        self.addr = (host, port)
        self.hz = hz
        self.axis = vnormalize(axis)
        self.heading = math.radians(heading_deg)
        self.noise = math.radians(noise_deg)
        self.theta = 45.0          # in-plane angle, deg (0 = right, 90 = up)
        self.tilt = 0.0            # out-of-plane component (toward/away from screen)
        self.roll = 0.0
        self.paused = False
        self._halt = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def direction(self):
        t = math.radians(self.theta)
        r = (math.cos(self.heading), math.sin(self.heading), 0.0)
        n = (-math.sin(self.heading), math.cos(self.heading), 0.0)
        return vnormalize((math.cos(t) * r[0] + self.tilt * n[0],
                           math.cos(t) * r[1] + self.tilt * n[1],
                           math.sin(t)))

    def quaternion(self):
        d = self.direction()
        q = qmul(q_from_axis_angle(d, math.radians(self.roll)), q_from_two_vectors(self.axis, d))
        ax = (random.gauss(0, 1), random.gauss(0, 1), random.gauss(0, 1))
        noise = q_from_axis_angle(ax, random.gauss(0, self.noise))
        return qnormalize(qmul(noise, q))

    def run(self):
        seq, t0 = 0, time.monotonic()
        nxt = t0
        while not self._halt.is_set():
            if not self.paused:
                t_ms = int((time.monotonic() - t0) * 1000)
                self.sock.sendto(packets.pack(seq, t_ms, 90, self.quaternion()), self.addr)
                seq += 1
            nxt += 1.0 / self.hz
            time.sleep(max(0.0, nxt - time.monotonic()))

    def close(self):
        self._halt.set()


class FakeGame(threading.Thread):
    """Mimics the plugin's UDP interface closely enough to test the bridge."""

    def __init__(self, cmd_port=9876, state_port=9877, r0=3.0, start_deg=30.0):
        super().__init__(daemon=True, name="fake-game")
        self.state_addr = ("127.0.0.1", state_port)
        self.cx, self.cy = 0.0, 0.0
        a = math.radians(start_deg)
        self.tx, self.ty = r0 * math.cos(a), r0 * math.sin(a)
        self.override = False
        self.method = None
        self.commands = []                  # (time, text)
        self.last_h = None
        self._halt = threading.Event()
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.rx.bind(("127.0.0.1", cmd_port))
        self.rx.settimeout(0.004)
        self.tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def run(self):
        t0, next_state = time.monotonic(), 0.0
        while not self._halt.is_set():
            try:
                data, _ = self.rx.recvfrom(1024)
                text = data.decode("ascii").strip()
                now = time.monotonic() - t0
                self.commands.append((now, text))
                p = text.split()
                if p[0] == "off":
                    self.override = False
                elif p[0] == "X":
                    self.method = int(p[1])
                elif p[0] == "H":
                    deg, ext = float(p[1]), float(p[2])
                    self.last_h = (now, deg, ext)
                    self.override = True
                    a = math.radians(deg)
                    self.tx, self.ty = self.cx + ext * math.cos(a), self.cy + ext * math.sin(a)
            except socket.timeout:
                pass
            except OSError:
                break
            if time.monotonic() - t0 >= next_state:
                next_state += 1 / 60
                s = {"t": time.monotonic() - t0, "sw": 1920, "sh": 1080, "ov": self.override,
                     "fx": self.tx, "fy": self.ty, "tx": self.tx, "ty": self.ty,
                     "cx": self.cx, "cy": self.cy, "mt": self.method or 0}
                try:
                    self.tx_sock.sendto(json.dumps(s).encode(), self.state_addr)
                except OSError:
                    pass

    def close(self):
        self._halt.set()
        self.rx.close()


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "imu":
        imu = FakeImu()
        imu.start()
        print("Fake IMU streaming to UDP 4210. Swinging the stick; Ctrl+C to stop.")
        t0 = time.monotonic()
        try:
            while True:
                imu.theta = 90 + 80 * math.sin((time.monotonic() - t0) * 1.2)
                time.sleep(0.01)
        except KeyboardInterrupt:
            imu.close()
    elif which == "game":
        g = FakeGame()
        g.start()
        print("Fake game listening on UDP 9876 (state -> 9877). Ctrl+C to stop.")
        n = 0
        try:
            while True:
                time.sleep(1)
                if g.last_h:
                    print(f"last H: angle {g.last_h[1]:.1f} ext {g.last_h[2]:.2f} | override={g.override} "
                          f"| {len(g.commands)} commands")
        except KeyboardInterrupt:
            g.close()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
