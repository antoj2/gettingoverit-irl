import socket
import time

# On Linux: sudo nmcli connection modify "SchimonsTuffESP32v2" 802-11-wireless.powersave 2

UDP_PORT = 4210

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
sock.bind(('0.0.0.0', UDP_PORT))

print("Listening for CodeCell...")

last_packet_time = time.perf_counter()
last_report_time = last_packet_time

intervals = []
packet_count = 0
previous_sequence = None
missing_packets = 0

try:
    while True:
        data, address = sock.recvfrom(1024)

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