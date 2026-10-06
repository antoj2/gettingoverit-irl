import cv2
import numpy as np
import mss
import time
import os


# ============================================================
# CONFIGURATION
# ============================================================

# Screenshot containing ONLY the pot, or as little background
# around the pot as possible.
TEMPLATE_FILE = "GrydeFinder/pot_template.png"

# Set this to True if you want to capture the entire screen.
# Later we can change this to capture only the Getting Over It
# window for much higher performance.
CAPTURE_MONITOR = 1

# Minimum number of good feature matches required.
MIN_MATCHES = 8

# RANSAC reprojection threshold.
RANSAC_THRESHOLD = 5.0

# When tracking is already established, only search around
# the previous pot position.
LOCAL_SEARCH_RADIUS = 400

# Number of consecutive failed frames before switching
# back to a full-screen search.
MAX_TRACKING_FAILURES = 5

# Resize factor for processing.
# 1.0 = native resolution.
# 0.75 = faster but less precise.
# 0.5 = much faster but less precise.
PROCESS_SCALE = 1.0


# ============================================================
# FEATURE DETECTOR
# ============================================================

# SIFT is a good choice here because it handles:
#   - rotation
#   - moderate scale changes
#   - illumination changes
#   - partial occlusion
#
# It is somewhat heavier than ORB, but we're prioritizing
# reliability for the first prototype.

try:
    detector = cv2.SIFT_create(
        nfeatures=1000,
        contrastThreshold=0.02,
        edgeThreshold=10,
        sigma=1.6
    )

    NORM_TYPE = cv2.NORM_L2

except Exception:
    print("SIFT unavailable, falling back to ORB.")

    detector = cv2.ORB_create(
        nfeatures=1500,
        scaleFactor=1.2,
        nlevels=8,
        edgeThreshold=15
    )

    NORM_TYPE = cv2.NORM_HAMMING


matcher = cv2.BFMatcher(NORM_TYPE)


# ============================================================
# LOAD TEMPLATE
# ============================================================

if not os.path.exists(TEMPLATE_FILE):
    print()
    print("ERROR:")
    print(f"Could not find '{TEMPLATE_FILE}'.")
    print()
    print("Take a screenshot of the pot and save it as:")
    print("    pot_template.png")
    print()
    raise SystemExit


template = cv2.imread(TEMPLATE_FILE)

if template is None:
    print("Could not load template image.")
    raise SystemExit


template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

template_h, template_w = template_gray.shape[:2]

print(f"Template size: {template_w} x {template_h}")


# ============================================================
# EXTRACT TEMPLATE FEATURES
# ============================================================

template_keypoints, template_descriptors = detector.detectAndCompute(
    template_gray,
    None
)

if template_descriptors is None or len(template_keypoints) < MIN_MATCHES:
    print()
    print("ERROR: Not enough features found in template.")
    print()
    print("Your template should:")
    print("  - contain the pot clearly")
    print("  - include some visible detail")
    print("  - not be extremely blurry")
    print()
    raise SystemExit


print(f"Template features: {len(template_keypoints)}")


# ============================================================
# SCREEN CAPTURE
# ============================================================

sct = mss.mss()

monitor = sct.monitors[CAPTURE_MONITOR]

print()
print("Capturing:")
print(
    f"  x={monitor['left']} "
    f"y={monitor['top']} "
    f"w={monitor['width']} "
    f"h={monitor['height']}"
)


# ============================================================
# DETECTION FUNCTION
# ============================================================

def detect_pot(frame, search_region=None):
    """
    Search for the pot in frame.

    Returns:

        center      -> (x, y)
        corners     -> 4-point polygon
        confidence  -> approximate confidence
        good_matches
        inliers

    or

        None
    """

    original_h, original_w = frame.shape[:2]

    offset_x = 0
    offset_y = 0

    # --------------------------------------------------------
    # Restrict search to a local region if requested.
    # --------------------------------------------------------

    if search_region is not None:

        x1, y1, x2, y2 = search_region

        x1 = max(0, int(x1))
        y1 = max(0, int(y1))
        x2 = min(original_w, int(x2))
        y2 = min(original_h, int(y2))

        if x2 <= x1 or y2 <= y1:
            return None

        search_image = frame[y1:y2, x1:x2]

        offset_x = x1
        offset_y = y1

    else:
        search_image = frame


    # --------------------------------------------------------
    # Convert to grayscale.
    # --------------------------------------------------------

    gray = cv2.cvtColor(search_image, cv2.COLOR_BGR2GRAY)


    # --------------------------------------------------------
    # Resize if requested.
    # --------------------------------------------------------

    if PROCESS_SCALE != 1.0:

        gray = cv2.resize(
            gray,
            None,
            fx=PROCESS_SCALE,
            fy=PROCESS_SCALE,
            interpolation=cv2.INTER_AREA
        )


    # --------------------------------------------------------
    # Find features in current frame.
    # --------------------------------------------------------

    keypoints, descriptors = detector.detectAndCompute(
        gray,
        None
    )

    if descriptors is None or len(keypoints) < MIN_MATCHES:
        return None


    # --------------------------------------------------------
    # Match template → screen.
    #
    # k=2 allows Lowe's ratio test.
    # --------------------------------------------------------

    try:

        matches = matcher.knnMatch(
            template_descriptors,
            descriptors,
            k=2
        )

    except cv2.error:
        return None


    # --------------------------------------------------------
    # Lowe ratio test.
    # --------------------------------------------------------

    good_matches = []

    for pair in matches:

        if len(pair) < 2:
            continue

        m, n = pair

        if m.distance < 0.72 * n.distance:
            good_matches.append(m)


    if len(good_matches) < MIN_MATCHES:
        return None


    # --------------------------------------------------------
    # Build point correspondences.
    # --------------------------------------------------------

    src_points = np.float32([
        template_keypoints[m.queryIdx].pt
        for m in good_matches
    ]).reshape(-1, 1, 2)

    dst_points = np.float32([
        keypoints[m.trainIdx].pt
        for m in good_matches
    ]).reshape(-1, 1, 2)


    # --------------------------------------------------------
    # Homography.
    #
    # RANSAC removes geometrically inconsistent matches.
    # --------------------------------------------------------

    H, mask = cv2.findHomography(
        src_points,
        dst_points,
        cv2.RANSAC,
        RANSAC_THRESHOLD
    )

    if H is None or mask is None:
        return None


    inlier_mask = mask.ravel().astype(bool)

    inliers = int(np.sum(inlier_mask))

    if inliers < MIN_MATCHES:
        return None


    # --------------------------------------------------------
    # Map the template's four corners into the screen.
    # --------------------------------------------------------

    template_corners = np.float32([
        [0, 0],
        [template_w - 1, 0],
        [template_w - 1, template_h - 1],
        [0, template_h - 1]
    ]).reshape(-1, 1, 2)


    detected_corners = cv2.perspectiveTransform(
        template_corners,
        H
    )


    detected_corners = detected_corners.reshape(4, 2)


    # --------------------------------------------------------
    # Undo processing scale.
    # --------------------------------------------------------

    if PROCESS_SCALE != 1.0:

        detected_corners /= PROCESS_SCALE


    # Add local-search offset.
    detected_corners[:, 0] += offset_x
    detected_corners[:, 1] += offset_y


    # --------------------------------------------------------
    # Calculate center.
    #
    # Using the four corners rather than the bounding rectangle
    # means rotation doesn't cause the center to jump as much.
    # --------------------------------------------------------

    center = np.mean(
        detected_corners,
        axis=0
    )


    center_x = float(center[0])
    center_y = float(center[1])


    # --------------------------------------------------------
    # Calculate confidence.
    #
    # This isn't a probability. It's simply a useful tracking
    # quality metric.
    # --------------------------------------------------------

    confidence = inliers / max(len(good_matches), 1)


    return (
        (center_x, center_y),
        detected_corners,
        confidence,
        len(good_matches),
        inliers
    )


# ============================================================
# DRAW DETECTION
# ============================================================

def draw_detection(frame, result):

    if result is None:
        return

    center, corners, confidence, good, inliers = result

    corners_int = np.int32(corners)

    # Polygon around detected pot.
    cv2.polylines(
        frame,
        [corners_int],
        True,
        (0, 255, 0),
        2
    )

    cx = int(center[0])
    cy = int(center[1])

    # Center point.
    cv2.circle(
        frame,
        (cx, cy),
        7,
        (0, 0, 255),
        -1
    )

    # Crosshair.
    cv2.line(
        frame,
        (cx - 20, cy),
        (cx + 20, cy),
        (0, 0, 255),
        2
    )

    cv2.line(
        frame,
        (cx, cy - 20),
        (cx, cy + 20),
        (0, 0, 255),
        2
    )

    # Information.
    cv2.putText(
        frame,
        f"POT: ({cx}, {cy})",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2
    )

    cv2.putText(
        frame,
        f"Matches: {good}  Inliers: {inliers}",
        (20, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2
    )

    cv2.putText(
        frame,
        f"Confidence: {confidence:.2f}",
        (20, 92),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2
    )


# ============================================================
# MAIN LOOP
# ============================================================

previous_center = None
tracking_failures = 0

fps = 0.0
last_time = time.perf_counter()

print()
print("Starting tracker.")
print("Press Q to quit.")
print()


while True:

    # --------------------------------------------------------
    # Capture screen.
    # --------------------------------------------------------

    screenshot = np.array(
        sct.grab(monitor)
    )

    frame = cv2.cvtColor(
        screenshot,
        cv2.COLOR_BGRA2BGR
    )


    # --------------------------------------------------------
    # Determine search region.
    # --------------------------------------------------------

    search_region = None

    if previous_center is not None and tracking_failures == 0:

        px, py = previous_center

        search_region = (
            px - LOCAL_SEARCH_RADIUS,
            py - LOCAL_SEARCH_RADIUS,
            px + LOCAL_SEARCH_RADIUS,
            py + LOCAL_SEARCH_RADIUS
        )


    # --------------------------------------------------------
    # Detect pot.
    # --------------------------------------------------------

    result = detect_pot(
        frame,
        search_region
    )


    # --------------------------------------------------------
    # If local tracking failed, try entire screen.
    # --------------------------------------------------------

    if result is None:

        tracking_failures += 1

        if tracking_failures >= MAX_TRACKING_FAILURES:

            result = detect_pot(
                frame,
                None
            )

            if result is not None:
                tracking_failures = 0

    else:

        tracking_failures = 0


    # --------------------------------------------------------
    # Update position.
    # --------------------------------------------------------

    if result is not None:

        center = result[0]

        previous_center = center

        draw_detection(
            frame,
            result
        )

    else:

        cv2.putText(
            frame,
            "POT NOT FOUND",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 255),
            2
        )


    # --------------------------------------------------------
    # FPS.
    # --------------------------------------------------------

    now = time.perf_counter()

    dt = now - last_time

    last_time = now

    if dt > 0:

        instantaneous_fps = 1.0 / dt

        fps = (
            fps * 0.9 +
            instantaneous_fps * 0.1
        )


    cv2.putText(
        frame,
        f"FPS: {fps:.1f}",
        (20, 125),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2
    )


    # --------------------------------------------------------
    # Display.
    # --------------------------------------------------------

    cv2.imshow(
        "Getting Over It - Pot Tracker",
        frame
    )


    # --------------------------------------------------------
    # Keyboard.
    # --------------------------------------------------------

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


# ============================================================
# CLEANUP
# ============================================================

cv2.destroyAllWindows()
sct.close()

print("Tracker stopped.")