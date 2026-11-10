import socket

UDP_PORT = 4210
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', UDP_PORT))
print(f'Listening for CodeCell on UDP port {UDP_PORT}...')
try:
    while True:
        data, address = sock.recvfrom(1024)
        try:
            x_text, y_text, battery_text = data.decode('utf-8').strip().split(',')
#            x, y, battery = float(x_text), float(y_text), float(battery_text)
            print("X: " + x_text + " Y: " + y_text + " Battery: " + battery_text, end='', flush=True)
            # print(f'\rX: {x:+10.4f} | Y: {y:+10.4f} | Battery: {battery:.0f}%', end='', flush=True)
        except (ValueError, UnicodeDecodeError):
            print(f'\nInvalid packet: {data!r}')
except KeyboardInterrupt:
    print('\nReceiver stopped.')
finally:
    sock.close()
