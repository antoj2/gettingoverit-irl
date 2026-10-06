"""
Robust pot tracker for "Getting Over It" (Wayland / PipeWire capture).

What is different from a plain cv2.matchTemplate:

1. LIGHTING: both template and frame are converted to a locally contrast-
   normalised feature image (subtract local mean, divide by local std).
   This is mostly an edge/structure map, so colour/brightness shifts matter far less.

2. OCCLUSION: the template is split into overlapping patches. Each patch is
   matched separately and "votes" for where the pot centre is. Only the best
   ~60% of the votes at each location are averaged, so arms / hammer covering
   part of the pot no longer ruin the match.

3. ROTATION: the template (and its patches) are pre-rotated in ANGLE_STEP
   increments. The best angle is the estimated pot rotation.

4. TEMPORAL: a constant-velocity Kalman filter predicts where the pot should be.
   While tracking we only search a window around the prediction (faster, and the
   pot cannot "teleport"). If a frame has no good match we coast on the
   prediction, with a search window that grows. Only after MAX_COAST_FRAMES
   failures do we fall back to a (stricter) whole-screen search.

Keys in the debug window:  q = quit,  t = save debug_frame.png,  r = reset tracker
"""

import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# ============================================================
# CONFIGURATION
# ============================================================

TEMPLATE_FILE = Path("GrydeFinder/pot_template.png")

PROCESS_SCALE = 0.50          # frame + template are downscaled by this for matching
SHOW_WINDOW = True

# --- appearance / lighting ---------------------------------
FEATURE_SIGMA = 6.0           # local-normalisation radius (px at process scale)
FEATURE_EPS = 6.0             # noise floor, stops flat areas being amplified

# --- rotation ----------------------------------------------
ANGLE_STEP = 15               # degrees between pre-rotated templates (360 must divide evenly)
ANGLE_SEARCH_STEPS = 2        # while tracking: try last angle +/- this many steps
GLOBAL_ANGLE_STRIDE = 1       # whole-screen search only tries every Nth angle

# --- patches (occlusion robustness) ------------------------
PATCH_GRID = 4                # PATCH_GRID x PATCH_GRID overlapping patches
PATCH_FRACTION = 0.35         # patch size relative to the (rotated) template canvas
MIN_PATCH_COVERAGE = 0.50     # drop patches that are mostly transparent / padding
MIN_PATCH_STD = 0.20          # drop patches with no structure
USED_FRACTION = 0.60          # average only the best X of patch scores per location

# --- acceptance thresholds ---------------------------------
TRACK_THRESHOLD = 0.35        # while tracking (lower: occlusion is expected)
GLOBAL_THRESHOLD = 0.55       # to (re)acquire from scratch (higher: no prior to lean on)

# --- tracking ----------------------------------------------
TRACK_RADIUS_PX = 120         # search radius around prediction (full-res px)
TRACK_GROWTH_PX = 40          # extra radius per coasted frame
MAX_COAST_FRAMES = 45         # then give up and search the whole screen
PRIOR_STRENGTH = 0.30         # 0 = ignore prediction inside window, 1 = strongly favour it
# The camera follows the player, so the pot is usually near the centre.
# Fractions of the frame (x0, y0, x1, y1) used for the whole-screen search.
GLOBAL_REGION = (0.10, 0.10, 0.90, 0.90)

# --- Kalman filter -----------------------------------------
KF_POS_NOISE = 30.0           # process noise on position  (px^2 / s)
KF_VEL_NOISE = 3.0e5          # process noise on velocity  ((px/s)^2 / s) - pot accelerates hard
MEAS_NOISE_PX = 6.0           # measurement sigma at score 1.0; grows as score drops
COAST_VELOCITY_DECAY = 0.92   # per coasted frame, so the estimate doesn't run away

# --- output ------------------------------------------------
ANGLE_SMOOTHING = 0.5         # 1 = raw angle each frame, lower = smoother
ANCHOR_UP_PX = 80.0           # full-res px from pot centre to your target point, along the pot's "up"


# ============================================================
# FEATURES
# ============================================================

def make_feature(gray):
    """Locally contrast-normalised image: roughly zero-mean, lighting independent."""
    g = gray.astype(np.float32)
    mu = cv2.GaussianBlur(g, (0, 0), FEATURE_SIGMA)
    d = g - mu
    var = cv2.GaussianBlur(d * d, (0, 0), FEATURE_SIGMA)
    return d / (np.sqrt(var) + FEATURE_EPS)


def rot_ccw(dx, dy, deg):
    """Rotate an image-space offset counter-clockwise (as seen on screen) by deg."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return dx * c + dy * s, -dx * s + dy * c


# ============================================================
# TEMPLATE BANK
# ============================================================

@dataclass
class Patch:
    img: np.ndarray
    mask: np.ndarray
    ox: int
    oy: int


@dataclass
class AngleBank:
    angle: float
    size: int
    patches: list
    k: int


def load_template():
    raw = cv2.imread(str(TEMPLATE_FILE), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise RuntimeError(f"Could not load template: {TEMPLATE_FILE}")

    if raw.ndim == 2:
        gray = raw
        alpha = np.full(raw.shape, 255, np.uint8)
    elif raw.shape[2] == 4:
        gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
        alpha = raw[:, :, 3]
    else:
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        alpha = np.full(gray.shape, 255, np.uint8)

    gray = cv2.resize(gray, None, fx=PROCESS_SCALE, fy=PROCESS_SCALE,
                      interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, None, fx=PROCESS_SCALE, fy=PROCESS_SCALE,
                       interpolation=cv2.INTER_AREA)

    mask = (alpha > 127).astype(np.float32)
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8))   # avoid fringe pixels
    if mask.sum() < 50:
        raise RuntimeError("Template mask is (almost) empty")

    g = gray.astype(np.float32)
    g[mask == 0] = g[mask > 0].mean()                    # neutral fill before normalising
    feat = make_feature(g) * mask
    return feat, mask


def build_banks(feat_t, mask_t):
    th, tw = feat_t.shape
    cs = int(math.ceil(math.hypot(th, tw)))
    if cs % 2 == 0:
        cs += 1

    canvas_f = np.zeros((cs, cs), np.float32)
    canvas_m = np.zeros((cs, cs), np.float32)
    y0, x0 = (cs - th) // 2, (cs - tw) // 2
    canvas_f[y0:y0 + th, x0:x0 + tw] = feat_t
    canvas_m[y0:y0 + th, x0:x0 + tw] = mask_t
    c = (cs - 1) / 2.0

    ps = max(8, int(cs * PATCH_FRACTION))
    positions = sorted(set(np.linspace(0, cs - ps, PATCH_GRID).astype(int).tolist()))

    banks = []
    for angle in range(-180, 180, ANGLE_STEP):
        M = cv2.getRotationMatrix2D((c, c), angle, 1.0)   # +angle = counter-clockwise
        rf = cv2.warpAffine(canvas_f, M, (cs, cs), flags=cv2.INTER_LINEAR, borderValue=0)
        rm = cv2.warpAffine(canvas_m, M, (cs, cs), flags=cv2.INTER_LINEAR, borderValue=0)
        rm = (rm > 0.5).astype(np.float32)

        patches = []
        for oy in positions:
            for ox in positions:
                m = rm[oy:oy + ps, ox:ox + ps]
                if m.mean() < MIN_PATCH_COVERAGE:
                    continue
                f = rf[oy:oy + ps, ox:ox + ps]
                sel = m > 0
                mean = f[sel].mean()
                img = ((f - mean) * m).astype(np.float32)
                if img[sel].std() < MIN_PATCH_STD:
                    continue
                patches.append(Patch(np.ascontiguousarray(img),
                                     np.ascontiguousarray(m), ox, oy))

        if not patches:
            raise RuntimeError("No usable template patches - is the template too flat/small?")
        k = max(1, int(math.ceil(USED_FRACTION * len(patches))))
        banks.append(AngleBank(float(angle), cs, patches, k))
    return banks


# ============================================================
# MATCHING
# ============================================================

def match_bank(feat, bank, prior=None):
    """
    Returns (weighted_peak, raw_score, (cx, cy)) in feat coordinates, or None.
    cx, cy is the centre of the (rotated) template canvas = pot centre.
    """
    H, W = feat.shape
    cs = bank.size
    Ht, Wt = H - cs + 1, W - cs + 1
    if Ht < 1 or Wt < 1:
        return None

    maps = []
    for p in bank.patches:
        r = cv2.matchTemplate(feat, p.img, cv2.TM_CCORR_NORMED, mask=p.mask)
        # Re-align so every patch map is indexed by template top-left.
        maps.append(r[p.oy:p.oy + Ht, p.ox:p.ox + Wt])
    stack = np.stack(maps).astype(np.float32)
    np.nan_to_num(stack, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    np.maximum(stack, 0.0, out=stack)

    P = stack.shape[0]
    k = min(bank.k, P)
    if k < P:
        stack = np.partition(stack, P - k, axis=0)[P - k:]
    raw = np.ascontiguousarray(stack.mean(axis=0), dtype=np.float32)

    half = (cs - 1) / 2.0
    if prior is not None:
        px, py, sigma = prior
        xs = np.arange(Wt, dtype=np.float32) + half - px
        ys = np.arange(Ht, dtype=np.float32) + half - py
        g = np.exp(-(ys[:, None] ** 2 + xs[None, :] ** 2) / (2.0 * sigma * sigma))
        weighted = np.ascontiguousarray(raw * ((1.0 - PRIOR_STRENGTH) + PRIOR_STRENGTH * g),
                                        dtype=np.float32)
    else:
        weighted = raw

    _, wmax, _, loc = cv2.minMaxLoc(weighted)
    return wmax, float(raw[loc[1], loc[0]]), (loc[0] + half, loc[1] + half)


def search(feat, banks, indices, prior=None):
    best = None
    for i in indices:
        res = match_bank(feat, banks[i], prior)
        if res is None:
            continue
        if best is None or res[0] > best[0]:
            best = (res[0], res[1], res[2], i)
    return best  # (weighted, raw, (cx, cy), bank_index) or None


def clamp_roi(cx, cy, half_w, half_h, W, H, min_size):
    half_w = max(half_w, min_size / 2.0 + 2)
    half_h = max(half_h, min_size / 2.0 + 2)
    w = int(min(round(2 * half_w), W))
    h = int(min(round(2 * half_h), H))
    x0 = int(min(max(round(cx - half_w), 0), W - w))
    y0 = int(min(max(round(cy - half_h), 0), H - h))
    return x0, y0, x0 + w, y0 + h


# ============================================================
# TRACKING
# ============================================================

class Kalman2D:
    def __init__(self):
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
        self.initialized = False

    def init(self, x, y):
        self.kf.statePost = np.array([[x], [y], [0], [0]], np.float32)
        self.kf.errorCovPost = np.eye(4, dtype=np.float32) * 100.0
        self.initialized = True

    def predict(self, dt):
        self.kf.transitionMatrix = np.array(
            [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
        self.kf.processNoiseCov = (
            np.diag([KF_POS_NOISE, KF_POS_NOISE, KF_VEL_NOISE, KF_VEL_NOISE]) * dt
        ).astype(np.float32)
        s = self.kf.predict()
        return float(s[0, 0]), float(s[1, 0])

    def coast(self):
        # OpenCV's predict() doesn't feed back into statePost without a correct().
        self.kf.statePost = self.kf.statePre.copy()
        self.kf.errorCovPost = self.kf.errorCovPre.copy()
        self.kf.statePost[2:] *= COAST_VELOCITY_DECAY

    def correct(self, x, y, score):
        sigma = MEAS_NOISE_PX / max(score, 0.1)
        self.kf.measurementNoiseCov = (np.eye(2) * sigma * sigma).astype(np.float32)
        self.kf.correct(np.array([[x], [y]], np.float32))

    def position(self):
        s = self.kf.statePost
        return float(s[0, 0]), float(s[1, 0])


class AngleFilter:
    def __init__(self):
        self.v = None

    def reset(self):
        self.v = None

    def update(self, deg):
        r = math.radians(deg)
        u = np.array([math.cos(r), math.sin(r)])
        self.v = u if self.v is None else (1 - ANGLE_SMOOTHING) * self.v + ANGLE_SMOOTHING * u

    @property
    def deg(self):
        if self.v is None:
            return 0.0
        return math.degrees(math.atan2(self.v[1], self.v[0]))


def anchor_point(cx, cy, angle_deg):
    ux, uy = rot_ccw(0.0, -1.0, angle_deg)       # pot's "up" in screen space
    return cx + ANCHOR_UP_PX * ux, cy + ANCHOR_UP_PX * uy


# ============================================================
# MAIN
# ============================================================

def main():
    from pipewire_capture import PortalCapture, CaptureStream

    S = PROCESS_SCALE
    feat_t, mask_t = load_template()
    th, tw = feat_t.shape
    banks = build_banks(feat_t, mask_t)
    n_banks = len(banks)
    cs = banks[0].size
    all_idx = list(range(n_banks))
    global_idx = all_idx[::GLOBAL_ANGLE_STRIDE]
    print(f"Template {tw}x{th} (process scale), canvas {cs}, "
          f"{n_banks} angles, ~{len(banks[0].patches)} patches each")

    print("\nPlease select the Getting Over It window.\n")
    session = PortalCapture().select_window()
    if session is None:
        print("Screen selection cancelled.")
        raise SystemExit
    print(f"Capture size: {session.width} x {session.height}")

    stream = CaptureStream(session.fd, session.node_id, session.width, session.height,
                           capture_interval=0)
    stream.start()

    tracker = Kalman2D()
    angle_filter = AngleFilter()
    last_idx = None
    lost = 0
    score = 0.0
    t_prev = time.perf_counter()

    try:
        while True:
            frame = stream.get_frame()
            if frame is None:
                if stream.window_invalid:
                    print("Capture error:" if stream.error else "Capture window closed.",
                          stream.error or "")
                    break
                time.sleep(0.001)
                continue

            now = time.perf_counter()
            dt = min(max(now - t_prev, 0.001), 0.1)
            t_prev = now

            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            small = cv2.resize(frame_bgr, None, fx=S, fy=S, interpolation=cv2.INTER_AREA)
            feat = make_feature(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            H, W = feat.shape
            if H < cs or W < cs:
                continue

            accepted = None   # (cx_full, cy_full, raw_score, bank_index)
            tracking_mode = tracker.initialized and lost <= MAX_COAST_FRAMES

            if tracking_mode:
                px, py = tracker.predict(dt)
                radius = (TRACK_RADIUS_PX + lost * TRACK_GROWTH_PX) * S
                cxs, cys = px * S, py * S
                x0, y0, x1, y1 = clamp_roi(cxs, cys, radius, radius, W, H, cs)
                if last_idx is None:
                    idxs = all_idx
                else:
                    idxs = [(last_idx + d) % n_banks
                            for d in range(-ANGLE_SEARCH_STEPS, ANGLE_SEARCH_STEPS + 1)]
                res = search(feat[y0:y1, x0:x1], banks, idxs,
                             prior=(cxs - x0, cys - y0, max(radius * 0.5, 10.0)))
                if res is not None and res[1] >= TRACK_THRESHOLD:
                    accepted = ((res[2][0] + x0) / S, (res[2][1] + y0) / S, res[1], res[3])
            else:
                gx0, gy0, gx1, gy1 = GLOBAL_REGION
                x0, y0 = int(W * gx0), int(H * gy0)
                x1, y1 = int(W * gx1), int(H * gy1)
                x1, y1 = max(x1, x0 + cs), max(y1, y0 + cs)
                x1, y1 = min(x1, W), min(y1, H)
                x0, y0 = min(x0, x1 - cs), min(y0, y1 - cs)
                res = search(feat[y0:y1, x0:x1], banks, global_idx)
                if res is not None and res[1] >= GLOBAL_THRESHOLD:
                    accepted = ((res[2][0] + x0) / S, (res[2][1] + y0) / S, res[1], res[3])

            if accepted is not None:
                mx, my, score, idx = accepted
                if tracking_mode:
                    tracker.correct(mx, my, score)
                else:
                    tracker.init(mx, my)
                    angle_filter.reset()
                lost = 0
                last_idx = idx
                angle_filter.update(banks[idx].angle)
                status = "TRACKING"
            elif tracking_mode:
                tracker.coast()
                lost += 1
                score = 0.0
                status = f"COASTING {lost}"
            else:
                tracker.initialized = False
                last_idx = None
                score = 0.0
                status = "LOST"

            have_pos = tracker.initialized and lost <= MAX_COAST_FRAMES
            if have_pos:
                cx, cy = tracker.position()
                ang = angle_filter.deg
                ax, ay = anchor_point(cx, cy, ang)
                print(f"\r{status:<12} pot=({cx:7.1f},{cy:7.1f}) "
                      f"anchor=({ax:7.1f},{ay:7.1f}) angle={ang:+6.1f} score={score:.2f}   ",
                      end="", flush=True)
            else:
                print(f"\r{status:<12}" + " " * 70, end="", flush=True)

            if SHOW_WINDOW:
                display = frame_bgr.copy()
                color = ((0, 255, 0) if status == "TRACKING"
                         else (0, 165, 255) if have_pos else (0, 0, 255))
                if have_pos:
                    hw, hh = tw / S / 2, th / S / 2
                    corners = []
                    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                        ox, oy = rot_ccw(sx * hw, sy * hh, ang)
                        corners.append((int(cx + ox), int(cy + oy)))
                    cv2.polylines(display, [np.array(corners, np.int32)], True, color, 2)
                    cv2.circle(display, (int(cx), int(cy)), 6, color, -1)
                    cv2.line(display, (int(cx), int(cy)), (int(ax), int(ay)), color, 2)
                    cv2.circle(display, (int(ax), int(ay)), 8, (255, 255, 0), -1)
                cv2.putText(display, f"{status}  score={score:.2f}", (20, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                if have_pos:
                    cv2.putText(display, f"angle={ang:+.0f}", (20, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("Getting Over It - Pot Tracker", display)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    tracker.initialized = False
                    last_idx = None
                    lost = 0
                if key == ord("t"):
                    cv2.imwrite("debug_frame.png", display)
                    print("\nSaved debug_frame.png")
    finally:
        stream.stop()
        session.close()
        cv2.destroyAllWindows()
        print("\nStopped.")


if __name__ == "__main__":
    main()