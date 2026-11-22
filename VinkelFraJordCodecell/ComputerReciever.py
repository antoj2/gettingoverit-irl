import socket
import sys
import time

# Wi-Fi power saving on the computer adds latency. See README.md in this folder for Windows and macOS.
# On Linux: sudo nmcli connection modify "SchimonsTuffESP32v2" 802-11-wireless.powersave 2

UDP_PORT = 4210
CODECELL_AP_ADDR = ("192.168.4.1", UDP_PORT)  # the CodeCell is the access point
SILENCE_HINT_AFTER = 3.0  # seconds without packets before printing help

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
sock.bind(('0.0.0.0', UDP_PORT))
# A timeout (instead of blocking forever) lets us print help when nothing arrives. On Windows it is also the only way Ctrl+C can interrupt a waiting recvfrom.
sock.settimeout(1.0)

# Tells the CodeCell where to send its data. Own send-only socket: on Windows a UDP socket that both sends and receives can get spurious "connection reset" errors.
hello_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
last_hello = float("-inf")


def say_hello():
    global last_hello
    now = time.monotonic()
    if now - last_hello < 1.0:
        return
    last_hello = now
    try:
        hello_sock.sendto(b"hello", CODECELL_AP_ADDR)
    except OSError:
        pass  # not on the CodeCell's network (yet)


def silence_help():
    print("No packets from the CodeCell. Check that:")
    print("  - the CodeCell is on, and this computer is connected to its Wi-Fi network (SchimonsTuffESP32v2)")
    if sys.platform == "win32":
        print("  - Windows Firewall allows incoming UDP on port 4210 (see README.md), or set the network to Private")
    elif sys.platform == "darwin":
        print("  - you answered Allow to the 'accept incoming network connections' prompt for Python")
        print("  - Terminal is allowed under Privacy & Security > Local Network (macOS 15 and later)")
    else:
        print("  - the firewall allows incoming UDP on port 4210")
    print("  - older firmware (no hello support) only works if this computer has the address 192.168.4.2")


print("Listening for CodeCell...")

last_packet_time = time.perf_counter()
last_report_time = last_packet_time
last_hint_time = last_packet_time

intervals = []
packet_count = 0
previous_sequence = None
missing_packets = 0

try:
    while True:
        say_hello()
        try:
            data, address = sock.recvfrom(1024)
        except socket.timeout:
            now = time.perf_counter()
            if now - max(last_packet_time, last_hint_time) >= SILENCE_HINT_AFTER:
                silence_help()
                last_hint_time = now
            continue

        now = time.perf_counter()
        interval = (now - last_packet_time) * 1000
        last_packet_time = now

        try:
            sequence, timestamp, angle, battery = (
                data.decode().strip().split(',')
            )

            sequence = int(sequence)

            if previous_sequence is not None:
                missing_packets += max(0, sequence - previous_sequence - 1)

            previous_sequence = sequence

            intervals.append(interval)
            packet_count += 1

        except (ValueError, UnicodeDecodeError):
            continue

        if now - last_report_time >= 1.0:
            if intervals:
                avg = sum(intervals) / len(intervals)
                minimum = min(intervals)
                maximum = max(intervals)

                print(
                    f"Packets/s: {packet_count:3} | "
                    f"Avg: {avg:6.2f} ms | "
                    f"Min: {minimum:6.2f} ms | "
                    f"Max: {maximum:6.2f} ms | "
                    f"Missing: {missing_packets}"
                )

            intervals = []
            packet_count = 0
            missing_packets = 0
            last_report_time = now

except KeyboardInterrupt:
    print("Receiver stopped.")

finally:
    sock.close()