import socket

UDP_PORT = 4210
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', UDP_PORT))
print(f'Listening for CodeCell on UDP port {UDP_PORT}...')
try:
    while True:
        data, address = sock.recvfrom(1024)
        try:
            angle, battery_text = data.decode('utf-8').strip().split(',')
            print("Angle: " + angle + " Battery: " + battery_text, end='\n', flush=True)
        except (ValueError, UnicodeDecodeError):
            print(f'\nInvalid packet: {data!r}')
except KeyboardInterrupt:
    print('\nReceiver stopped.')
finally:
    sock.close()
