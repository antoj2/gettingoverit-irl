"""Tests hammer control with the 'H deg ext' command (angle + distance from the pivot).
Phase 1: sweep the angle through 360 deg at constant distance.  Phase 2: sweep the distance at constant angle.
Start the game, get in, keep the game window focused, hands off the mouse, ideally with the hammer in open air.
Usage: python3 sweep.py"""
import socket, json, time, math
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 9877)); rx.settimeout(5)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send = lambda m: tx.sendto(m.encode(), ("127.0.0.1", 9876))
def state():
    """Return the NEWEST packet: block for one, then drain everything queued behind it."""
    rx.settimeout(5)
    last = json.loads(rx.recv(65536))
    rx.setblocking(False)
    try:
        while True: last = json.loads(rx.recv(65536))
    except BlockingIOError:
        pass
    rx.settimeout(5)
    return last
def polar(s):
    dx, dy = s["tx"] - s["cx"], s["ty"] - s["cy"]
    return math.degrees(math.atan2(dy, dx)), math.hypot(dx, dy)
def wrap(a): return (a + 180) % 360 - 180

while "fx" not in state(): pass
send("off"); send("X 3"); time.sleep(0.5)
s0 = state(); th0, r0 = polar(s0)
print(f"start: tip angle {th0:.1f} deg, distance from pivot {r0:.2f}, slider st={s0['st']:.2f}")

def run(label, seq, dt=0.02, every=0.5):
    print(f"\n{label}\n  t   target_ang target_r | meas_ang meas_r  ang_err  st   r-st")
    t0 = time.time(); nxt = 0; errs = []; rs = []
    for i, (ang, r) in enumerate(seq):
        send(f"H {ang:.3f} {r:.3f}")
        while time.time() < t0 + (i + 1) * dt: time.sleep(0.001)   # fixed 50 Hz schedule
        s = state(); a, rr = polar(s)
        errs.append(abs(wrap(a - ang))); rs.append(rr)
        if time.time() - t0 >= nxt:
            nxt += every
            print(f"{time.time()-t0:5.1f}  {wrap(ang):8.1f} {r:8.2f} | {a:8.1f} {rr:7.2f} {wrap(a-ang):8.1f} {s['st']:7.2f} {rr-s['st']:6.2f}")
    print(f"  mean |angle error| = {sum(errs)/len(errs):.1f} deg, max = {max(errs):.1f} deg;  measured distance range {min(rs):.2f} .. {max(rs):.2f}")

# Phase 1: 360 deg in 15 s, constant distance
N = 750
run("PHASE 1: angle sweep at constant distance", [(th0 + 360 * i / N, r0) for i in range(N)])
# Phase 2: distance from 0.6*r0 -> 1.5*r0 -> back, angle fixed at start angle
M = 300
seq = [(th0, r0 * (0.6 + 0.9 * i / M)) for i in range(M)] + [(th0, r0 * (1.5 - 0.9 * i / M)) for i in range(M)]
run("PHASE 2: distance sweep at constant angle", seq)
send("off")
print("\ndone")