import cv2
import numpy as np
import mss
import time
import os
from collections import deque


# ============================================================
# CONFIGURATION
# ============================================================

TEMPLATE_FILE = "GrydeFinder/pot_template.png"

# ------------------------------------------------------------
# IMPORTANT:
#
# If your game is fullscreen, leave this as None.
#
# If you know the game occupies a particular region, you can
# specify:
#
# GAME_REGION = {
#     "left": 0,
#     "top": 0,
#     "width": 1920,
#     "height": 1080
# }
# ------------------------------------------------------------

GAME_REGION = None


# ------------------------------------------------------------
# Processing resolution.
#
# 0.5 means detection happens on a 50% sized image.
# This dramatically reduces CPU usage.
#
# The coordinates are converted back to screen coordinates.
# ------------------------------------------------------------

PROCESS_SCALE = 0.50


# ------------------------------------------------------------
# Template matching threshold.
#
# 0.80 is fairly permissive.
# 0.90 is stricter.
# ------------------------------------------------------------

TEMPLATE_THRESHOLD = 0.75


# ------------------------------------------------------------
# ORB settings.
# ------------------------------------------------------------

ORB_FEATURES = 1200

ORB_RATIO = 0.78

MIN_ORB_MATCHES = 6

MIN_ORB_INLIERS = 5


# ------------------------------------------------------------
# SIFT settings.
#
# This is only used when explicitly requested.
# ------------------------------------------------------------

SIFT_FEATURES = 800

SIFT_RATIO = 0.72

MIN_SIFT_MATCHES = 6

MIN_SIFT_INLIERS = 5


# ------------------------------------------------------------
# Local search.
#
# Once the pot has been found, only search this region.
# ------------------------------------------------------------

LOCAL_SEARCH_RADIUS = 300


# Number of frames before doing a complete search again.
REACQUIRE_AFTER = 10


# ------------------------------------------------------------
# How often to run the expensive feature detector.
#
# Template matching runs continuously.
# ORB runs every N frames.
#
# 1 = every frame
# 2 = every second frame
# 3 = every third frame
# ------------------------------------------------------------

ORB_INTERVAL = 2


# ============================================================
# INITIALIZATION
# ============================================================

print("=" * 60)
print("Getting Over It - Pot Tracker Diagnostic")
print("=" * 60)


if not os.path.exists(TEMPLATE_FILE):

    print()
    print("ERROR: Template file not found:")
    print(f"    {TEMPLATE_FILE}")
    print()
    print("Create a crop containing the pot and save it as")
    print("pot_template.png in this directory.")
    print()

    raise SystemExit


template_original = cv2.imread(TEMPLATE_FILE)

if template_original is None:

    print("ERROR: Could not read template.")
    raise SystemExit


template_gray_original = cv2.cvtColor(
    template_original,
    cv2.COLOR_BGR2GRAY
)


template_h_original, template_w_original = (
    template_gray_original.shape
)


print(
    f"Template: "
    f"{template_w_original} x {template_h_original}"
)


# ============================================================
# RESIZE TEMPLATE TO PROCESSING SCALE
# ============================================================

template_gray = cv2.resize(
    template_gray_original,
    None,
    fx=PROCESS_SCALE,
    fy=PROCESS_SCALE,
    interpolation=cv2.INTER_AREA
)


template_h, template_w = template_gray.shape


print(
    f"Processing template: "
    f"{template_w} x {template_h}"
)


# ============================================================
# ORB
# ============================================================

orb = cv2.ORB_create(
    nfeatures=ORB_FEATURES,
    scaleFactor=1.2,
    nlevels=8,
    edgeThreshold=15,
    fastThreshold=10
)


orb_keypoints, orb_descriptors = orb.detectAndCompute(
    template_gray,
    None
)


print(
    f"ORB template features: "
    f"{0 if orb_descriptors is None else len(orb_descriptors)}"
)


if orb_descriptors is None:

    print()
    print("WARNING:")
    print("ORB could not find any features in the template.")
    print("This is important and probably means the template")
    print("is too small / blurry / featureless.")
    print()


orb_matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING
)


# ============================================================
# SIFT
# ============================================================

try:

    sift = cv2.SIFT_create(
        nfeatures=SIFT_FEATURES,
        contrastThreshold=0.02,
        edgeThreshold=10
    )

    sift_keypoints, sift_descriptors = (
        sift.detectAndCompute(
            template_gray,
            None
        )
    )

    print(
        f"SIFT template features: "
        f"{0 if sift_descriptors is None else len(sift_descriptors)}"
    )

    sift_matcher = cv2.BFMatcher(
        cv2.NORM_L2
    )

    SIFT_AVAILABLE = sift_descriptors is not None

except Exception as e:

    print("SIFT unavailable:", e)

    SIFT_AVAILABLE = False
    sift = None
    sift_keypoints = []
    sift_descriptors = None


# ============================================================
# MSS SCREEN CAPTURE
# ============================================================

sct = mss.mss()


if GAME_REGION is None:

    monitor = sct.monitors[1]

else:

    monitor = GAME_REGION


print()
print(
    "Capture region:",
    monitor
)


# ============================================================
# TEMPLATE MATCHING
# ============================================================

def template_match(frame):

    """
    Fast sanity-check detector.

    Returns:
        result or None
    """

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY
    )


    # Resize screen to processing resolution.

    gray = cv2.resize(
        gray,
        None,
        fx=PROCESS_SCALE,
        fy=PROCESS_SCALE,
        interpolation=cv2.INTER_AREA
    )


    # Template cannot be larger than image.

    if (
        template_h > gray.shape[0]
        or template_w > gray.shape[1]
    ):

        return None


    result = cv2.matchTemplate(
        gray,
        template_gray,
        cv2.TM_CCOEFF_NORMED
    )


    _, max_value, _, max_location = (
        cv2.minMaxLoc(result)
    )


    x = max_location[0]
    y = max_location[1]


    # Convert back to capture coordinates.

    x /= PROCESS_SCALE
    y /= PROCESS_SCALE


    w = template_w / PROCESS_SCALE
    h = template_h / PROCESS_SCALE


    center = (
        x + w / 2,
        y + h / 2
    )


    return {
        "center": center,
        "rect": (
            x,
            y,
            w,
            h
        ),
        "score": float(max_value)
    }


# ============================================================
# ORB DETECTION
# ============================================================

def orb_match(frame):

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY
    )


    gray = cv2.resize(
        gray,
        None,
        fx=PROCESS_SCALE,
        fy=PROCESS_SCALE,
        interpolation=cv2.INTER_AREA
    )


    keypoints, descriptors = orb.detectAndCompute(
        gray,
        None
    )


    if descriptors is None:

        return {
            "matches": 0,
            "inliers": 0,
            "result": None
        }


    if orb_descriptors is None:

        return {
            "matches": 0,
            "inliers": 0,
            "result": None
        }


    try:

        raw_matches = orb_matcher.knnMatch(
            orb_descriptors,
            descriptors,
            k=2
        )

    except cv2.error:

        return {
            "matches": 0,
            "inliers": 0,
            "result": None
        }


    good = []

    for pair in raw_matches:

        if len(pair) != 2:
            continue

        m, n = pair

        if m.distance < ORB_RATIO * n.distance:

            good.append(m)


    if len(good) < MIN_ORB_MATCHES:

        return {
            "matches": len(good),
            "inliers": 0,
            "result": None
        }


    src = np.float32([
        orb_keypoints[m.queryIdx].pt
        for m in good
    ]).reshape(-1, 1, 2)


    dst = np.float32([
        keypoints[m.trainIdx].pt
        for m in good
    ]).reshape(-1, 1, 2)


    try:

        H, mask = cv2.findHomography(
            src,
            dst,
            cv2.RANSAC,
            5.0
        )

    except cv2.error:

        return {
            "matches": len(good),
            "inliers": 0,
            "result": None
        }


    if H is None or mask is None:

        return {
            "matches": len(good),
            "inliers": 0,
            "result": None
        }


    inlier_count = int(
        np.sum(mask)
    )


    if inlier_count < MIN_ORB_INLIERS:

        return {
            "matches": len(good),
            "inliers": inlier_count,
            "result": None
        }


    # --------------------------------------------------------
    # Transform template corners.
    # --------------------------------------------------------

    corners = np.float32([
        [0, 0],
        [template_w - 1, 0],
        [template_w - 1, template_h - 1],
        [0, template_h - 1]
    ]).reshape(-1, 1, 2)


    transformed = cv2.perspectiveTransform(
        corners,
        H
    ).reshape(4, 2)


    # Convert processing coordinates back to capture coords.

    transformed /= PROCESS_SCALE


    center = np.mean(
        transformed,
        axis=0
    )


    return {
        "matches": len(good),
        "inliers": inlier_count,
        "result": {
            "center": (
                float(center[0]),
                float(center[1])
            ),
            "corners": transformed
        }
    }


# ============================================================
# SIFT DETECTION
# ============================================================

def sift_match(frame):

    if not SIFT_AVAILABLE:

        return {
            "matches": 0,
            "inliers": 0,
            "result": None
        }


    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY
    )


    gray = cv2.resize(
        gray,
        None,
        fx=PROCESS_SCALE,
        fy=PROCESS_SCALE,
        interpolation=cv2.INTER_AREA
    )


    keypoints, descriptors = sift.detectAndCompute(
        gray,
        None
    )


    if descriptors is None:

        return {
            "matches": 0,
            "inliers": 0,
            "result": None
        }


    try:

        raw_matches = sift_matcher.knnMatch(
            sift_descriptors,
            descriptors,
            k=2
        )

    except cv2.error:

        return {
            "matches": 0,
            "inliers": 0,
            "result": None
        }


    good = []

    for pair in raw_matches:

        if len(pair) != 2:
            continue

        m, n = pair

        if m.distance < SIFT_RATIO * n.distance:

            good.append(m)


    if len(good) < MIN_SIFT_MATCHES:

        return {
            "matches": len(good),
            "inliers": 0,
            "result": None
        }


    src = np.float32([
        sift_keypoints[m.queryIdx].pt
        for m in good
    ]).reshape(-1, 1, 2)


    dst = np.float32([
        keypoints[m.trainIdx].pt
        for m in good
    ]).reshape(-1, 1, 2)


    try:

        H, mask = cv2.findHomography(
            src,
            dst,
            cv2.RANSAC,
            5.0
        )

    except cv2.error:

        return {
            "matches": len(good),
            "inliers": 0,
            "result": None
        }


    if H is None or mask is None:

        return {
            "matches": len(good),
            "inliers": 0,
            "result": None
        }


    inlier_count = int(
        np.sum(mask)
    )


    if inlier_count < MIN_SIFT_INLIERS:

        return {
            "matches": len(good),
            "inliers": inlier_count,
            "result": None
        }


    corners = np.float32([
        [0, 0],
        [template_w - 1, 0],
        [template_w - 1, template_h - 1],
        [0, template_h - 1]
    ]).reshape(-1, 1, 2)


    transformed = cv2.perspectiveTransform(
        corners,
        H
    ).reshape(4, 2)


    transformed /= PROCESS_SCALE


    center = np.mean(
        transformed,
        axis=0
    )


    return {
        "matches": len(good),
        "inliers": inlier_count,
        "result": {
            "center": (
                float(center[0]),
                float(center[1])
            ),
            "corners": transformed
        }
    }


# ============================================================
# DRAW RESULT
# ============================================================

def draw_polygon(
    image,
    corners,
    thickness=3
):

    if corners is None:
        return

    corners = np.int32(
        corners
    )

    cv2.polylines(
        image,
        [corners],
        True,
        (0, 255, 0),
        thickness
    )


def draw_center(
    image,
    center,
    color=(0, 0, 255)
):

    if center is None:
        return

    x = int(center[0])
    y = int(center[1])


    cv2.circle(
        image,
        (x, y),
        8,
        color,
        -1
    )


    cv2.line(
        image,
        (x - 25, y),
        (x + 25, y),
        color,
        2
    )


    cv2.line(
        image,
        (x, y - 25),
        (x, y + 25),
        color,
        2
    )


# ============================================================
# DEBUG TEXT
# ============================================================

def text(
    image,
    string,
    y,
    color=(255, 255, 255)
):

    cv2.putText(
        image,
        string,
        (15, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        color,
        2,
        cv2.LINE_AA
    )


# ============================================================
# MAIN LOOP
# ============================================================

frame_number = 0

previous_center = None

last_orb = None
last_sift = None
last_template = None

fps_history = deque(
    maxlen=30
)


print()
print("Controls:")
print("  Q = quit")
print("  T = save current frame as debug.png")
print("  S = run SIFT immediately")
print("  F = toggle full/local search")
print()
print("Starting...")
print()


force_full_search = True


while True:

    frame_start = time.perf_counter()


    # --------------------------------------------------------
    # CAPTURE
    # --------------------------------------------------------

    capture_start = time.perf_counter()


    screenshot = np.array(
        sct.grab(monitor)
    )


    frame = cv2.cvtColor(
        screenshot,
        cv2.COLOR_BGRA2BGR
    )


    capture_time = (
        time.perf_counter()
        - capture_start
    )


    # --------------------------------------------------------
    # TEMPLATE MATCH
    # --------------------------------------------------------

    detection_start = time.perf_counter()


    last_template = template_match(
        frame
    )


    template_time = (
        time.perf_counter()
        - detection_start
    )


    # --------------------------------------------------------
    # ORB
    # --------------------------------------------------------

    if (
        frame_number % ORB_INTERVAL == 0
        or last_orb is None
    ):

        orb_start = time.perf_counter()

        last_orb = orb_match(
            frame
        )

        orb_time = (
            time.perf_counter()
            - orb_start
        )

    else:

        orb_time = 0


    # --------------------------------------------------------
    # Pick candidate.
    #
    # Template matching is deliberately used as the first
    # sanity check.
    # --------------------------------------------------------

    detected_center = None

    detected_corners = None

    detector_name = "NONE"


    if (
        last_template is not None
        and last_template["score"]
        >= TEMPLATE_THRESHOLD
    ):

        detected_center = (
            last_template["center"]
        )

        x, y, w, h = (
            last_template["rect"]
        )

        detected_corners = np.float32([
            [x, y],
            [x + w, y],
            [x + w, y + h],
            [x, y + h]
        ])

        detector_name = "TEMPLATE"


    elif (
        last_orb is not None
        and last_orb["result"] is not None
    ):

        detected_center = (
            last_orb["result"]["center"]
        )

        detected_corners = (
            last_orb["result"]["corners"]
        )

        detector_name = "ORB"


    # --------------------------------------------------------
    # Draw template candidate.
    # --------------------------------------------------------

    if last_template is not None:

        score = last_template["score"]

        x, y, w, h = (
            last_template["rect"]
        )

        color = (
            (0, 255, 0)
            if score >= TEMPLATE_THRESHOLD
            else (0, 165, 255)
        )

        cv2.rectangle(
            frame,
            (
                int(x),
                int(y)
            ),
            (
                int(x + w),
                int(y + h)
            ),
            color,
            2
        )


    # --------------------------------------------------------
    # Draw ORB candidate.
    # --------------------------------------------------------

    if (
        last_orb is not None
        and last_orb["result"] is not None
    ):

        draw_polygon(
            frame,
            last_orb["result"]["corners"],
            3
        )


    # --------------------------------------------------------
    # Draw chosen result.
    # --------------------------------------------------------

    if detected_center is not None:

        previous_center = detected_center

        draw_center(
            frame,
            detected_center,
            (0, 0, 255)
        )


    # --------------------------------------------------------
    # FPS
    # --------------------------------------------------------

    total_time = (
        time.perf_counter()
        - frame_start
    )


    if total_time > 0:

        current_fps = (
            1.0 / total_time
        )

        fps_history.append(
            current_fps
        )


    if fps_history:

        fps = np.mean(
            fps_history
        )

    else:

        fps = 0


    # --------------------------------------------------------
    # DEBUG INFORMATION
    # --------------------------------------------------------

    text(
        frame,
        f"FPS: {fps:.1f}",
        30
    )


    text(
        frame,
        f"Capture: {capture_time * 1000:.1f} ms",
        60
    )


    text(
        frame,
        f"Template: {template_time * 1000:.1f} ms",
        90
    )


    text(
        frame,
        f"ORB: {orb_time * 1000:.1f} ms",
        120
    )


    if last_template is not None:

        text(
            frame,
            f"Template score: "
            f"{last_template['score']:.4f}",
            155,
            (
                0,
                255,
                0
                if last_template["score"]
                >= TEMPLATE_THRESHOLD
                else 255
            )
        )

    else:

        text(
            frame,
            "Template score: ERROR",
            155,
            (0, 0, 255)
        )


    if last_orb is not None:

        text(
            frame,
            f"ORB matches: "
            f"{last_orb['matches']}   "
            f"inliers: "
            f"{last_orb['inliers']}",
            190
        )


    text(
        frame,
        f"Detector: {detector_name}",
        225
    )


    if detected_center is not None:

        text(
            frame,
            f"Pot center: "
            f"{int(detected_center[0])}, "
            f"{int(detected_center[1])}",
            260,
            (0, 255, 0)
        )

    else:

        text(
            frame,
            "POT NOT FOUND",
            260,
            (0, 0, 255)
        )


    # --------------------------------------------------------
    # Draw a small thumbnail of the template.
    # --------------------------------------------------------

    thumb_width = 200

    thumb_height = int(
        template_original.shape[0]
        * thumb_width
        / template_original.shape[1]
    )


    thumbnail = cv2.resize(
        template_original,
        (
            thumb_width,
            thumb_height
        )
    )


    # Put thumbnail in top-right.

    h, w = frame.shape[:2]

    if (
        thumb_height + 10 < h
        and thumb_width + 10 < w
    ):

        frame[
            10:10 + thumb_height,
            w - 10 - thumb_width:
            w - 10
        ] = thumbnail


    # --------------------------------------------------------
    # Display.
    # --------------------------------------------------------

    cv2.imshow(
        "Pot Tracker Diagnostic",
        frame
    )


    # --------------------------------------------------------
    # Keyboard.
    # --------------------------------------------------------

    key = cv2.waitKey(1) & 0xFF


    if key == ord("q"):

        break


    elif key == ord("t"):

        cv2.imwrite(
            "debug_frame.png",
            frame
        )

        print(
            "Saved debug_frame.png"
        )


    elif key == ord("s"):

        print(
            "Running SIFT..."
        )

        sift_start = time.perf_counter()

        last_sift = sift_match(
            frame
        )

        sift_time = (
            time.perf_counter()
            - sift_start
        )


        if last_sift["result"] is None:

            print(
                f"SIFT FAILED | "
                f"matches={last_sift['matches']} "
                f"inliers={last_sift['inliers']} "
                f"time={sift_time * 1000:.1f} ms"
            )

        else:

            print(
                f"SIFT SUCCESS | "
                f"matches={last_sift['matches']} "
                f"inliers={last_sift['inliers']} "
                f"center={last_sift['result']['center']} "
                f"time={sift_time * 1000:.1f} ms"
            )


    frame_number += 1


# ============================================================
# CLEANUP
# ============================================================

cv2.destroyAllWindows()

sct.close()

print()
print("Stopped.")