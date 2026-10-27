"""Hardware check: prints the ESP32 stream.   python -m goi_bridge.monitor [--port 4210]

Sanity test: hold the stick still and vertical -> 'up in sensor frame' is the stick axis.
Tilt the stick: the numbers must change smoothly. Packet loss should be well under 1%.
"""
import argparse
import time

from .imu import ImuReceiver
from .mathutil import qrotate_inv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4210)
    a = ap.parse_args()
    rx = ImuReceiver("0.0.0.0", a.port)
    rx.start()
    print(f"Listening on UDP {a.port}... Ctrl+C to stop.")
    t0, n0 = time.monotonic(), 0
    try:
        while True:
            time.sleep(0.5)
            s = rx.latest
            if s is None:
                print("no packets yet")
                continue
            now = time.monotonic()
            hz = (rx.received - n0) / (now - t0)
            t0, n0 = now, rx.received
            up = qrotate_inv(s.q, (0.0, 0.0, 1.0))
            print(f"{hz:5.1f} Hz | lost {rx.lost} bad {rx.bad} | bat {s.battery}% | "
                  f"q=({s.q[0]:+.3f},{s.q[1]:+.3f},{s.q[2]:+.3f},{s.q[3]:+.3f}) | "
                  f"up in sensor frame=({up[0]:+.2f},{up[1]:+.2f},{up[2]:+.2f}) | "
                  f"|acc|={sum(c * c for c in s.acc) ** 0.5:.2f}")
    except KeyboardInterrupt:
        pass
    finally:
        rx.close()


if __name__ == "__main__":
    main()
