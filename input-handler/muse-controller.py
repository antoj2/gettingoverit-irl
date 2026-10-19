import time
import sys
import subprocess

from pynput.mouse import Controller

UPDATE_RATE = 120  # per second
CHECK_WINDOW_INTERVAL = 0.1  # seconds between checking active window

mouse = Controller()

def get_active_window():
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True,
            text=True,
            timeout=1,
            check=True
        )

        window_id = result.stdout.strip()

        result = subprocess.run(
            ["xdotool", "getwindowname", window_id],
            capture_output=True,
            text=True,
            timeout=1,
            check=True
        )

        return window_id, result.stdout.strip().lower()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

def get_window_geometry(window_id):
    try:
        result = subprocess.run(
            [
                "xdotool",
                "getwindowgeometry",
                "--shell",
                window_id
            ],
            capture_output=True,
            text=True,
            timeout=1,
            check=True
        )

        values = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = int(value)

        return (
            values["X"],
            values["Y"],
            values["WIDTH"],
            values["HEIGHT"]
        )
    except (subprocess.SubprocessError, FileNotFoundError, ValueError, KeyError):
        return None

def main():
    x_pos = 0
    y_pos = 0

    print("muse-controller")
    print("Tryk på Ctrl+C for at stoppe programmet.\n")

    delay = 1.0 / UPDATE_RATE

    last_window_check = 0
    game_active = False
    geometry = None

    try:
        while True:
            now = time.monotonic()

            # I check the active window less frequently for performance reasons and since it isn't likely to change.
            if now - last_window_check >= CHECK_WINDOW_INTERVAL:
                last_window_check = now

                active_window = get_active_window()

                if active_window is not None:
                    window_id, title = active_window

                    if "getting over it" in title:
                        game_active = True
                        geometry = get_window_geometry(window_id)

                        if geometry is not None:
                            x, y, width, height = geometry

                            print(
                                f"\rGame window: ({x}, {y}), "
                                f"{width}x{height}     ",
                                end="",
                                flush=True
                            )
                    else:
                        game_active = False
                        geometry = None
                else:
                    game_active = False
                    geometry = None

            if game_active and geometry is not None:
                window_x, window_y, width, height = geometry

                target_x = window_x + x_pos
                target_y = window_y + y_pos

                mouse.position = (target_x, target_y)

            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n\nMouse controller stopped.")
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()