"""Optional global hotkeys (needs `pip install pynput`; works on X11/Windows/macOS, not on Wayland)."""


def start_hotkeys(push, out=print):
    try:
        from pynput import keyboard
    except Exception:
        out("Hotkeys requested but pynput is not installed (pip install pynput). Skipping.")
        return None
    mapping = {keyboard.Key.f8: "r", keyboard.Key.f9: "p", keyboard.Key.f10: "q"}

    def on_press(key):
        cmd = mapping.get(key)
        if cmd:
            push(cmd)

    try:
        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
    except Exception as e:
        out(f"Could not start global hotkeys ({e}). Skipping.")
        return None
    out("Global hotkeys: F8 = recenter (point stick right), F9 = pause/resume, F10 = quit")
    return listener
