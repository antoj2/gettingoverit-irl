"""Minimal client: prints game state, optionally drives the mouse override in a circle."""
import socket, json, time, math, sys
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 9877))
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
drive = "--drive" in sys.argv
t0 = time.time()
while True:
    s = json.loads(rx.recv(4096))
    print(s)
    if drive:
        a = (time.time() - t0)
        x = s["sw"]/2 + 200*math.cos(a); y = s["sh"]/2 + 200*math.sin(a)
        tx.sendto(f"M {x:.1f} {y:.1f}".encode(), ("127.0.0.1", 9876))
