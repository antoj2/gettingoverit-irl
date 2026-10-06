import time
from pathlib import Path

import cv2
import numpy as np

from pipewire_capture import PortalCapture, CaptureStream


# ============================================================
# CONFIGURATION
# ============================================================

TEMPLATE_FILE = Path("GrydeFinder/pot_template.png")

PROCESS_SCALE = 0.50
TEMPLATE_THRESHOLD = 0.75

SHOW_WINDOW = True


# ============================================================
# LOAD TEMPLATE
# ============================================================

if not TEMPLATE_FILE.exists():
    raise FileNotFoundError(
        f"Template not found: {TEMPLATE_FILE}"
    )

template = cv2.imread(
    str(TEMPLATE_FILE),
    cv2.IMREAD_GRAYSCALE
)

if template is None:
    raise RuntimeError(
        f"Could not load template: {TEMPLATE_FILE}"
    )

template = cv2.resize(
    template,
    None,
    fx=PROCESS_SCALE,
    fy=PROCESS_SCALE,
    interpolation=cv2.INTER_AREA
)

template_h, template_w = template.shape

print(
    f"Template size: "
    f"{template_w} x {template_h}"
)


# ============================================================
# START WAYLAND SCREEN CAPTURE
# ============================================================

print()
print("Select the Getting Over It window.")
print()

portal = PortalCapture()

session = portal.select_window()

if session is None:
    print("Screen selection cancelled.")
    raise SystemExit


print(
    f"Capture size: "
    f"{session.width} x {session.height}"
)


stream = CaptureStream(
    session.fd,
    session.node_id,
    session.width,
    session.height,

    # Zero means: don't artificially throttle capture.
    capture_interval=0
)

stream.start()


# ============================================================
# FPS / TIMING
# ============================================================

frame_count = 0
last_report = time.perf_counter()
last_frame_time = time.perf_counter()


# ============================================================
# MAIN LOOP
# ============================================================

try:

    while True:

        frame_start = time.perf_counter()

        # ----------------------------------------------------
        # Get latest frame.
        #
        # pipewire-capture returns:
        #
        #     H x W x 4 BGRA
        # ----------------------------------------------------

        frame = stream.get_frame()

        if frame is None:

            if stream.window_invalid:

                if stream.error:
                    print(
                        "Capture error:",
                        stream.error
                    )

                else:
                    print(
                        "Capture window closed."
                    )

                break

            time.sleep(0.001)
            continue


        # ----------------------------------------------------
        # Convert BGRA -> BGR for OpenCV
        # ----------------------------------------------------

        frame_bgr = cv2.cvtColor(
            frame,
            cv2.COLOR_BGRA2BGR
        )


        # ----------------------------------------------------
        # Resize for detection
        # ----------------------------------------------------

        small = cv2.resize(
            frame_bgr,
            None,
            fx=PROCESS_SCALE,
            fy=PROCESS_SCALE,
            interpolation=cv2.INTER_AREA
        )


        gray = cv2.cvtColor(
            small,
            cv2.COLOR_BGR2GRAY
        )


        # ----------------------------------------------------
        # Template matching
        # ----------------------------------------------------

        if (
            gray.shape[0] >= template_h
            and gray.shape[1] >= template_w
        ):

            result = cv2.matchTemplate(
                gray,
                template,
                cv2.TM_CCOEFF_NORMED
            )

            _, score, _, location = cv2.minMaxLoc(
                result
            )

        else:

            score = 0.0
            location = (0, 0)


        # ----------------------------------------------------
        # Convert match location back to full resolution
        # ----------------------------------------------------

        x = int(location[0] / PROCESS_SCALE)
        y = int(location[1] / PROCESS_SCALE)

        w = int(template_w / PROCESS_SCALE)
        h = int(template_h / PROCESS_SCALE)

        center = (
            x + w // 2,
            y + h // 2
        )


        # ----------------------------------------------------
        # Determine whether pot was found
        # ----------------------------------------------------

        found = score >= TEMPLATE_THRESHOLD


        if found:

            print(
                f"\rPot: "
                f"({center[0]}, {center[1]})"
                f"   score={score:.3f}",
                end="",
                flush=True
            )


        # ----------------------------------------------------
        # Draw debug information
        # ----------------------------------------------------

        if SHOW_WINDOW:

            display = frame_bgr.copy()

            color = (
                (0, 255, 0)
                if found
                else (0, 0, 255)
            )

            cv2.rectangle(
                display,
                (x, y),
                (x + w, y + h),
                color,
                2
            )


            cv2.circle(
                display,
                center,
                8,
                color,
                -1
            )


            cv2.putText(
                display,
                f"Score: {score:.3f}",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                2
            )


            cv2.putText(
                display,
                (
                    f"Pot: {center}"
                    if found
                    else "POT NOT FOUND"
                ),
                (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                2
            )


            cv2.imshow(
                "Getting Over It - Pot Tracker",
                display
            )


            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break


            if key == ord("t"):

                cv2.imwrite(
                    "debug_frame.png",
                    display
                )

                print(
                    "\nSaved debug_frame.png"
                )


        # ----------------------------------------------------
        # FPS measurement
        # ----------------------------------------------------

        frame_count += 1

        now = time.perf_counter()

        if now - last_report >= 2.0:

            fps = (
                frame_count
                / (now - last_report + 1e-9)
            )

            print(
                f"\nCapture/processing FPS: "
                f"{fps:.1f}"
            )

            frame_count = 0
            last_report = now


finally:

    stream.stop()
    session.close()

    cv2.destroyAllWindows()

    print()
    print("Stopped.")