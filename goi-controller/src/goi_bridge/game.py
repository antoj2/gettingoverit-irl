"""UDP link to the in-game BepInEx plugin (Plugin.cs).

  PC -> game   127.0.0.1:9876   text commands:  "H deg ext" | "W x y" | "M sx sy" | "X n" | "off"
  game -> PC   127.0.0.1:9877   one JSON object per game frame
"""
import json
import math
import socket
import threading
import time


class GameLink(threading.Thread):
    def __init__(self, host="127.0.0.1", cmd_port=9876, state_port=9877, dry_run=False):
        super().__init__(daemon=True, name="game-state-rx")
        self.dry_run = dry_run
        self.target = (host, cmd_port)
        self.latest_state = None
        self.state_time = 0.0
        self._halt = threading.Event()
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.rx.bind(("127.0.0.1", state_port))
        except OSError as e:
            raise RuntimeError(f"cannot bind UDP 127.0.0.1:{state_port} for game state ({e}). "
                               "Close test_client.py / Sweep.py first - only one program can listen there.")
        self.rx.settimeout(0.2)

    # ---- commands -------------------------------------------------------
    def _send(self, text):
        if self.dry_run:
            return
        try:
            self.tx.sendto(text.encode("ascii"), self.target)
        except OSError:
            pass

    def send_polar(self, angle_deg, ext):
        self._send(f"H {angle_deg:.3f} {ext:.4f}")

    def send_off(self):
        self._send("off")

    def send_method(self, n):
        self._send(f"X {int(n)}")

    # ---- state ----------------------------------------------------------
    def run(self):
        while not self._halt.is_set():
            try:
                data, _ = self.rx.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.latest_state = json.loads(data)
                self.state_time = time.monotonic()
            except ValueError:
                pass

    def have_player(self):
        s = self.latest_state
        return bool(s) and "tx" in s and "cx" in s and time.monotonic() - self.state_time < 1.0

    def wait_for_player(self, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.have_player():
                return True
            time.sleep(0.05)
        return False

    def measure_r0(self, duration=0.4):
        """Average pivot->hammer-tip distance (world units) over `duration` seconds."""
        vals, end, last = [], time.monotonic() + duration, None
        while time.monotonic() < end:
            s, t = self.latest_state, self.state_time
            if s and "tx" in s and t != last:
                last = t
                vals.append(math.hypot(s["tx"] - s["cx"], s["ty"] - s["cy"]))
            time.sleep(0.005)
        return sum(vals) / len(vals) if vals else None

    def close(self):
        self._halt.set()
        for s in (self.rx, self.tx):
            try:
                s.close()
            except OSError:
                pass
