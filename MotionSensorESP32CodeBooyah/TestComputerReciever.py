
import socket
import time

# ==========================================
# CONFIGURATION
# ==========================================

UDP_IP = "0.0.0.0"
UDP_PORT = 4210

# ==========================================
# CREATE UDP RECEIVER
# ==========================================

sock = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM
)

sock.bind((UDP_IP, UDP_PORT))

# ==========================================
# RECEIVE POSITION
# ==========================================

print("CodeCell Position Receiver")
print("==========================")
print(f"Listening on UDP port {UDP_PORT}")
print("Waiting for ESP32...")
print()

try:
    while True:

        # Wait for incoming packet
        data, address = sock.recvfrom(1024)

        try:
            # Decode received message
            message = data.decode("utf-8").strip()

            # Extract coordinates
            x, y, bat = map(float, message.split(","))

            # Display position
            print(
                f"\rX: {x:+10.4f} | Y: {y:+10.4f} | Battery: {bat:#0x}",
                end="",
                flush=True
            )

        except (ValueError, UnicodeDecodeError):
            print(f"\nInvalid packet: {data}")

except KeyboardInterrupt:
    print("\nReceiver stopped.")

finally:
    sock.close()
