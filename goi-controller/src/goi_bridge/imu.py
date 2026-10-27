"""UDP receiver for the ESP32 IMU stream."""
import socket
import threading
import time

from . import packets


class ImuReceiver(threading.Thread):
    def __init__(self, bind="0.0.0.0", port=4210, callback=None):
        super().__init__(daemon=True, name="imu-rx")
        self.callback = callback
        self.latest = None
        self.received = 0
        self.lost = 0
        self.bad = 0
        self._last_seq = None
        self._halt = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((bind, port))
        except OSError as e:
            raise RuntimeError(f"cannot bind UDP {bind}:{port} for the ESP32 stream ({e}). "
                               "Is another receiver (e.g. the old ComputerReciever.py) running?")
        self.sock.settimeout(0.2)

    def run(self):
        while not self._halt.is_set():
            try:
                data, _ = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            s = packets.parse(data)
            if s is None:
                self.bad += 1
                continue
            if self._last_seq is not None:
                gap = (s.seq - self._last_seq) & 0xFFFFFFFF
                if gap == 0 or gap > 0x7FFFFFFF:      # duplicate / out-of-order: drop
                    continue
                self.lost += gap - 1
            self._last_seq = s.seq
            self.received += 1
            self.latest = s
            if self.callback:
                try:
                    self.callback(s)
                except Exception as e:                  # never kill the receive thread
                    print(f"[imu] callback error: {e!r}")

    def age(self):
        s = self.latest
        return float("inf") if s is None else time.monotonic() - s.rx_time

    def close(self):
        self._halt.set()
        try:
            self.sock.close()
        except OSError:
            pass
