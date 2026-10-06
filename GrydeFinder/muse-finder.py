import tkinter as tk
import ctypes
import threading
import time

# ----------------------------
# Configuration
# ----------------------------
RADIUS = 20
LINE_WIDTH = 3
COLOR = "red"
UPDATE_HZ = 120

# ----------------------------
# Get mouse position
# ----------------------------
def get_mouse_position():
    # Windows
    if hasattr(ctypes, "windll"):
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    # Fallback for non-Windows systems
    try:
        import pyautogui
        return pyautogui.position()
    except ImportError:
        raise RuntimeError(
            "Install pyautogui on non-Windows systems:\n"
            "pip install pyautogui"
        )


# ----------------------------
# Overlay
# ----------------------------
root = tk.Tk()

root.overrideredirect(True)
root.attributes("-topmost", True)

# Transparent background
TRANSPARENT = "magenta"
root.config(bg=TRANSPARENT)

try:
    root.attributes("-transparentcolor", TRANSPARENT)
except tk.TclError:
    # Linux/macOS may not support this Tk feature.
    pass

canvas = tk.Canvas(
    root,
    width=RADIUS * 2 + LINE_WIDTH * 2,
    height=RADIUS * 2 + LINE_WIDTH * 2,
    bg=TRANSPARENT,
    highlightthickness=0,
    bd=0
)
canvas.pack()

canvas.create_oval(
    LINE_WIDTH,
    LINE_WIDTH,
    RADIUS * 2 + LINE_WIDTH,
    RADIUS * 2 + LINE_WIDTH,
    outline=COLOR,
    width=LINE_WIDTH
)


# ----------------------------
# Make the window click-through
# ----------------------------
def make_click_through():
    if not hasattr(ctypes, "windll"):
        return

    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020

    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

    ctypes.windll.user32.SetWindowLongW(
        hwnd,
        GWL_EXSTYLE,
        style | WS_EX_LAYERED | WS_EX_TRANSPARENT
    )


# ----------------------------
# Update position
# ----------------------------
def update():
    x, y = get_mouse_position()

    size = RADIUS * 2 + LINE_WIDTH * 2

    root.geometry(
        f"{size}x{size}+"
        f"{int(x - size / 2)}+"
        f"{int(y - size / 2)}"
    )

    root.after(int(1000 / UPDATE_HZ), update)


root.update_idletasks()
make_click_through()
update()

root.mainloop()