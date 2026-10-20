"""Tries every override method once and reports which one moves the cursor / hammer tip.
Start the game, get in (hammer visible), keep your hands off the mouse, then run:  python3 probe.py"""
import socket, json, time
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 9877)); rx.settimeout(2)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send = lambda m: tx.sendto(m.encode(), ("127.0.0.1", 9876))
def state():
    try:
        rx.recv(65536)                  # drop one stale packet
        return json.loads(rx.recv(65536))
    except socket.timeout:
        raise SystemExit("No packets from the game on UDP 9877. Is the game running with BepInEx (setarch -R ./run_bepinex.sh) "
                         "and the plugin loaded? Check: ss -ulpn | grep 987   and   BepInEx/LogOutput.log")
def settle(sec):
    t = time.time()
    while time.time() - t < sec: state()
print("waiting for the game ... (click into the game window and keep it focused; Unity pauses unfocused games)")
while True:
    try:
        rx.settimeout(5); d = json.loads(rx.recv(65536))
    except socket.timeout:
        print("  still no packets - is the game window focused / running through BepInEx?"); continue
    if "fx" in d: break
    print("  connected, but PlayerControl not found yet - get into the game (hammer visible)")
rx.settimeout(2)
print("connected!")
D = 1.5   # world units to move the cursor
print("method           cursor dx   tip dx    mw dx    rb dx   (target dx = %.1f)" % D)
for m, name in enumerate(["0 mw", "1 rb.position", "2 rb.MovePosition", "3 transform", "4 all", "5 rb.velocity"]):
    send("off"); settle(0.8)
    s0 = state()
    send(f"X {m}")
    for _ in range(30):                 # hold the target for ~0.6 s
        send(f"W {s0['fx']+D:.4f} {s0['fy']:.4f}"); time.sleep(0.02)
    s1 = state()
    for _ in range(30):                 # go back
        send(f"W {s0['fx']:.4f} {s0['fy']:.4f}"); time.sleep(0.02)
    send("off"); settle(0.3)
    print(f"{name:<17}{s1['fx']-s0['fx']:+9.2f}{s1['tx']-s0['tx']:+9.2f}{s1['wx']-s0['wx']:+9.2f}{s1['rx']-s0['rx']:+9.2f}")