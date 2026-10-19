import time
import sys

from pynput.mouse import Controller

UPDATE_RATE = 120 # per second

mouse = Controller()

def main():
    target_x, target_y = 1000, 1000

    print(f"Target position: ({target_x}, {target_y})")
    print("Press Ctrl+C to stop.")
    print()

    delay = 1.0 / UPDATE_RATE

    try:
        while True:
            mouse.position = (target_x, target_y)
            time.sleep(delay)

    except KeyboardInterrupt:
        print("\nMouse lock stopped.")

    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()