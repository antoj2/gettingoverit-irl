"""Prints game state. --circle drives the cursor target in a circle (screen px). --polar sweeps the hammer angle."""
import socket, json, time, math, sys
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 9877))
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
t0 = time.time()
while True:
    s = json.loads(rx.recv(4096)); print(s)
    a = time.time() - t0
    if "--circle" in sys.argv:
        tx.sendto(f"M {s['sw']/2+200*math.cos(a):.1f} {s['sh']/2+200*math.sin(a):.1f}".encode(), ("127.0.0.1", 9876))
    elif "--polar" in sys.argv:
        tx.sendto(f"H {math.degrees(a)%360:.1f} 3.0".encode(), ("127.0.0.1", 9876))