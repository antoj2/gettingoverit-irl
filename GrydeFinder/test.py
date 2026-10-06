import os
import cv2
import mss
import numpy as np

print("XDG_SESSION_TYPE =", os.environ.get("XDG_SESSION_TYPE"))
print("DISPLAY          =", os.environ.get("DISPLAY"))
print("WAYLAND_DISPLAY  =", os.environ.get("WAYLAND_DISPLAY"))

with mss.MSS() as sct:

    print("\nAvailable monitors:")

    for i, monitor in enumerate(sct.monitors):
        print(i, monitor)

    print("\nCapturing primary monitor...")

    monitor = sct.primary_monitor

    screenshot = sct.grab(monitor)

    image = np.array(screenshot)

    image = cv2.cvtColor(
        image,
        cv2.COLOR_BGRA2BGR
    )

    cv2.imwrite(
        "raw_capture.png",
        image
    )

    print(
        "Saved raw_capture.png:",
        image.shape
    )

    print(
        "MSS performance:",
        sct.performance_status
    )