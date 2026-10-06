"""
Pot tracker v2 for "Getting Over It" (Wayland / PipeWire capture).

Approach: gradient-ORIENTATION template matching (the idea behind shape-based
matching in industrial vision / LINE-MOD), instead of raw pixel correlation.

Why it is more robust than cv2.matchTemplate on grey values
-----------------------------------------------------------
* Lighting: only the *direction* of image edges is compared, not brightness.
  Dimmer/brighter/tinted lighting changes edge strength, not edge direction.
* Occlusion: the template is a set of edge points (outline of the pot).
  The score is "what fraction of those points land on an image edge with the
  right direction". If an arm hides 30% of the pot you still score ~70% of
  normal, instead of the correlation collapsing.  Clutter that is *added*
  (arms, hammer) does not subtract from the score.
* Rotation: the edge points are rotated analytically for every angle in
  -45..+45 deg (coarse steps for searching, fine steps for refinement), so the
  best angle is the pot's rotation.  Rotation is not "inferred" from anything
  else - it is simply which rotated template fits best.
* Motion blur: edges are measured on a downscaled, slightly blurred image and
  each image edge is "spread" over a small neighbourhood, so a few pixels of
  smear don't break the match.
* Precision: coarse-to-fine.  A cheap coarse search finds the pot and rough
  angle, then a fine search (finer scale, 1.5 deg steps, sub-pixel peak
  interpolation) polishes position and angle.

Temporal logic (no Kalman filter)
---------------------------------
Every frame we search near the last position first.  A confident local match
is accepted immediately.  Otherwise a whole-screen search runs.  A far-away
result only replaces the current track if it is clearly better AND is seen in
consecutive frames (so single-frame spikes are ignored, but real teleports
are followed after ~2 frames).  When nothing is found we HOLD the last pose for
a short while instead of jumping to garbage.

Keys: q quit | r reset tracker | t save debug_frame.png | k save raw frame to captures/
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
SHOW_WINDOW = True

# --- rotation ----------------------------------------------
ANGLE_RANGE = (-45.0, 45.0)    # degrees; + = counter-clockwise on screen
COARSE_ANGLE_STEP = 6.0        # used for searching
FINE_ANGLE_STEP = 1.5          # used for refinement
GLOBAL_ANGLE_STRIDE = 2        # whole-screen search tries every Nth coarse angle
NUM_CANDIDATES = 6             # coarse peaks that the fine stage re-scores (kills false positives)
LOCAL_ANGLE_SPAN = 24.0        # while tracking: +/- this many degrees around last angle

# --- scales ------------------------------------------------
COARSE_TARGET_PX = 64          # coarse level downscales so the template is ~this big
REFINE_MARGIN_PX = 14          # full-res px the fine step may move the coarse result

# --- edge features -----------------------------------------
NUM_BINS = 16                  # orientation bins over 180 deg (must be multiple of 4)
IMG_BLUR_SIGMA = 0.8           # blur (level px) before gradients: noise / blur tolerance
MAG_THRESHOLD = 40.0           # min gradient magnitude in image (Sobel units, ~10 grey levels)
TEMPLATE_MAG_MIN = 40.0        # same for template points
SPREAD_PX = 3                  # an image edge counts for +/- this/2 px around it
NEIGHBOR_BIN_WEIGHT = 0.3      # credit for orientation one bin (11.25 deg) off
TARGET_POINTS = 150            # template points per level (cell size is chosen to reach this)

# --- decision thresholds (edge score is the fraction of matched template points, 0..1) ---
GOOD_SCORE = 0.55              # local match this good is accepted immediately
LOCAL_MIN = 0.38               # weaker local match accepted as "WEAK" (probably occluded)
GLOBAL_MIN = 0.50              # whole-screen result must reach this to be considered
ACQUIRE_SCORE = 0.55           # needed to start tracking from nothing
FAR_SCORE = 0.62               # needed to jump far from the current track
FAR_MARGIN = 0.10              # ...and beat the local result by this much
FAR_SURE_SCORE = 0.72          # a far result this good is followed immediately (no confirmation)
CONFIRM_FRAMES = 2             # far candidate must be seen this many global searches in a row

# --- tracking geometry -------------------------------------
TRACK_RADIUS_FACTOR = 0.6      # coarse local search radius = this x template size (full-res px)
FAST_MARGIN_FACTOR = 0.2       # fast path (fine level only) may move this x template size per frame
FAST_ANGLE_SPAN = 3.0          # ...and rotate this many degrees
TRACK_GROWTH_FACTOR = 0.25     # extra radius per held frame, x template size
HOLD_MAX = 20                  # frames to hold the last pose before declaring LOST
GLOBAL_EVERY = 2               # while the track is weak/held: whole-screen search every N frames

# --- output ------------------------------------------------
ANCHOR_UP_PX = 80.0            # full-res px from pot centre to your point, along the pot's "up"


# ============================================================
# HELPERS
# ============================================================

def rot_ccw(dx, dy, deg):
    """Rotate an image-space offset counter-clockwise (as seen on screen) by deg."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return dx * c + dy * s, -dx * s + dy * c


def anchor_point(cx, cy, angle_deg):
    ux, uy = rot_ccw(0.0, -1.0, angle_deg)
    return cx + ANCHOR_UP_PX * ux, cy + ANCHOR_UP_PX * uy


def orientation_bins(gx, gy):
    ang = np.mod(np.arctan2(gy, gx), np.pi)                   # direction, sign-insensitive
    return np.floor(ang / np.pi * NUM_BINS + 0.5).astype(np.int32) % NUM_BINS


def downscale(gray, k):
    if k == 1:
        return gray
    h, w = gray.shape
    return cv2.resize(gray, (w // k, h // k), interpolation=cv2.INTER_AREA)


def image_maps(gray_u8, k):
    """
    Edge-orientation maps of an image (size must be a multiple of k).
    Returns NUM_BINS/4 float32 images with 4 channels each; channel value is
    1 where an edge of that orientation is within SPREAD_PX, 0.5 for the
    neighbouring orientation bin.
    """
    g = downscale(gray_u8, k).astype(np.float32)
    if IMG_BLUR_SIGMA > 0:
        g = cv2.GaussianBlur(g, (0, 0), IMG_BLUR_SIGMA)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    bins = orientation_bins(gx, gy)
    valid = mag > MAG_THRESHOLD
    kernel = np.ones((SPREAD_PX, SPREAD_PX), np.uint8)
    spread = []
    for b in range(NUM_BINS):
        m = ((bins == b) & valid).astype(np.uint8)
        spread.append(cv2.dilate(m, kernel).astype(np.float32))
    soft = []
    for b in range(NUM_BINS):
        nb = np.maximum(spread[(b - 1) % NUM_BINS], spread[(b + 1) % NUM_BINS])
        soft.append(np.maximum(spread[b], nb * NEIGHBOR_BIN_WEIGHT))
    return [cv2.merge(soft[i:i + 4]) for i in range(0, NUM_BINS, 4)]


def subpixel(sm, x, y):
    """Parabolic peak refinement on a score map."""
    h, w = sm.shape
    dx = dy = 0.0
    if 0 < x < w - 1:
        a, b, c = sm[y, x - 1], sm[y, x], sm[y, x + 1]
        den = a - 2 * b + c
        if den < -1e-9:
            dx = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    if 0 < y < h - 1:
        a, b, c = sm[y - 1, x], sm[y, x], sm[y + 1, x]
        den = a - 2 * b + c
        if den < -1e-9:
            dy = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return dx, dy


# ============================================================
# TEMPLATE LEVELS
# ============================================================

@dataclass
class Bank:
    angle: float
    groups: list        # [(group_index, template(hb, wb, 4))]
    n: int              # number of template points
    ox: int             # column of the pot centre inside the template array
    oy: int
    hb: int
    wb: int
    iy: np.ndarray      # point rows / cols / bins inside the template array (for debug)
    ix: np.ndarray
    bn: np.ndarray


def load_template():
    raw = cv2.imread(str(TEMPLATE_FILE), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise RuntimeError(f"Could not load template: {TEMPLATE_FILE}")
    if raw.ndim == 2:
        gray, alpha = raw, np.full(raw.shape, 255, np.uint8)
    elif raw.shape[2] == 4:
        gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
        alpha = raw[:, :, 3]
    else:
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        alpha = np.full(gray.shape, 255, np.uint8)
    return gray, alpha


class Level:
    """Template edge points at one scale (1/k), pre-rotated for a list of angles."""

    def __init__(self, gray_full, alpha_full, k, angles):
        self.k = k
        self.angles = list(angles)
        th, tw = gray_full.shape
        wl, hl = tw // k, th // k
        wl -= (wl % 2 == 0)             # odd size -> the centre is a whole pixel
        hl -= (hl % 2 == 0)
        x0, y0 = (tw - wl * k) // 2, (th - hl * k) // 2
        g = downscale(np.ascontiguousarray(gray_full[y0:y0 + hl * k, x0:x0 + wl * k]), k)
        a = downscale(np.ascontiguousarray(alpha_full[y0:y0 + hl * k, x0:x0 + wl * k]), k)
        g = g.astype(np.float32)
        inside = a > 127
        fill = g[inside].mean() - 40.0   # background gets a definite step so the silhouette is an edge
        comp = np.where(inside, g, fill).astype(np.float32)
        if IMG_BLUR_SIGMA > 0:
            comp = cv2.GaussianBlur(comp, (0, 0), IMG_BLUR_SIGMA)
        gx = cv2.Sobel(comp, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(comp, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        phi = np.arctan2(gy, gx)
        allowed = cv2.dilate(inside.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        mag_a = np.where(allowed, mag, 0.0)

        thr = max(TEMPLATE_MAG_MIN, 0.2 * float(np.percentile(mag_a[allowed], 99)))
        pts = None
        for cell in (4, 3, 2, 1):
            pts = self._pick(mag_a, thr, cell)
            if len(pts[0]) >= TARGET_POINTS:
                break
        ys, xs = pts
        if len(xs) < 12:
            raise RuntimeError(f"Only {len(xs)} edge points found in the template at scale 1/{k}; "
                               f"is the template too small/flat?")
        cx, cy = (wl - 1) / 2.0, (hl - 1) / 2.0
        self.px, self.py = xs - cx, ys - cy
        self.phi = phi[ys, xs]
        self.size_level = (hl, wl)
        self.banks = [self._bank(a_) for a_ in self.angles]
        self.max_h = max(b.hb for b in self.banks)
        self.max_w = max(b.wb for b in self.banks)

    @staticmethod
    def _pick(mag, thr, cell):
        h, w = mag.shape
        ys, xs = [], []
        for y in range(0, h, cell):
            for x in range(0, w, cell):
                blk = mag[y:y + cell, x:x + cell]
                j = int(np.argmax(blk))
                if blk.flat[j] > thr:
                    ys.append(y + j // blk.shape[1])
                    xs.append(x + j % blk.shape[1])
        return np.array(ys, np.int32), np.array(xs, np.int32)

    def _bank(self, angle_deg):
        t = math.radians(angle_deg)
        c, s = math.cos(t), math.sin(t)
        rx = self.px * c + self.py * s
        ry = -self.px * s + self.py * c
        bn = orientation_bins(np.cos(self.phi - t), np.sin(self.phi - t))
        ix = np.rint(rx).astype(np.int32)
        iy = np.rint(ry).astype(np.int32)
        minx, miny = int(ix.min()), int(iy.min())
        wb, hb = int(ix.max()) - minx + 1, int(iy.max()) - miny + 1
        T = np.zeros((NUM_BINS, hb, wb), np.float32)
        np.add.at(T, (bn, iy - miny, ix - minx), 1.0)
        groups = []
        for gi in range(NUM_BINS // 4):
            sub = T[gi * 4:(gi + 1) * 4]
            if sub.any():
                groups.append((gi, np.ascontiguousarray(np.transpose(sub, (1, 2, 0)))))
        return Bank(angle_deg, groups, len(ix), -minx, -miny, hb, wb,
                    iy - miny, ix - minx, bn)

    def search(self, maps, angle_idxs):
        """Returns a list of (score, angle_idx, (x, y), score_map) - the best peak for each angle."""
        H, W = maps[0].shape[:2]
        out = []
        for ai in angle_idxs:
            bank = self.banks[ai]
            if H < bank.hb or W < bank.wb:
                continue
            acc = None
            for gi, T in bank.groups:
                r = cv2.matchTemplate(maps[gi], T, cv2.TM_CCORR)
                acc = r if acc is None else acc + r
            acc *= 1.0 / bank.n
            _, mx, _, loc = cv2.minMaxLoc(acc)
            out.append((float(mx), ai, loc, acc))
        return out


def top_candidates(level, results, shape, k_peaks, nms_radius):
    """Collapse per-angle score maps into one centre-indexed map and return its top peaks."""
    H, W = shape
    best = np.full((H, W), -1.0, np.float32)
    best_ai = np.zeros((H, W), np.int32)
    for score, ai, loc, acc in results:
        bank = level.banks[ai]
        h, w = acc.shape
        view = best[bank.oy:bank.oy + h, bank.ox:bank.ox + w]
        better = acc > view
        view[better] = acc[better]
        best_ai[bank.oy:bank.oy + h, bank.ox:bank.ox + w][better] = ai
    out = []
    r = int(max(1, nms_radius))
    for _ in range(k_peaks):
        _, mx, _, loc = cv2.minMaxLoc(best)
        if mx <= 0:
            break
        x, y = loc
        out.append((float(mx), int(best_ai[y, x]), (x, y), best))
        best[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = -1.0
    return out


# ============================================================
# DETECTOR (coarse + fine)
# ============================================================

@dataclass
class Pose:
    cx: float
    cy: float
    angle: float
    score: float
    fine_score: float = 0.0


def align_crop(cx, cy, half_w, half_h, W, H, k):
    x0 = int(max(0, cx - half_w)) // k * k
    y0 = int(max(0, cy - half_h)) // k * k
    x1 = int(min(W, cx + half_w)) // k * k
    y1 = int(min(H, cy + half_h)) // k * k
    return x0, y0, x1, y1


class Detector:
    def __init__(self, gray_t, alpha_t):
        th, tw = gray_t.shape
        self.tsize = float(max(th, tw))
        self.kc = max(2, int(round(self.tsize / COARSE_TARGET_PX)))
        self.kf = max(1, int(math.ceil(self.kc / 2)))
        lo, hi = ANGLE_RANGE
        self.c_angles = list(np.arange(lo, hi + 1e-6, COARSE_ANGLE_STEP))
        self.f_angles = list(np.arange(lo, hi + 1e-6, FINE_ANGLE_STEP))
        self.coarse = Level(gray_t, alpha_t, self.kc, self.c_angles)
        self.fine = Level(gray_t, alpha_t, self.kf, self.f_angles)
        self.debug_pts = None   # (x_full, y_full, hit) of the last refinement

    # ---- conversions -------------------------------------------------------
    @staticmethod
    def _full(v_level, k, origin):
        return origin + (v_level + 0.5) * k - 0.5

    def _to_pose(self, level, res, x0, y0):
        score, ai, loc, acc = res
        bank = level.banks[ai]
        sx, sy = subpixel(acc, loc[0], loc[1])
        cxl = loc[0] + sx + bank.ox
        cyl = loc[1] + sy + bank.oy
        return Pose(self._full(cxl, level.k, x0), self._full(cyl, level.k, y0),
                    bank.angle, score)

    # ---- coarse searches -----------------------------------------------------
    def _candidates(self, maps, idxs, x0, y0, k_peaks):
        res = self.coarse.search(maps, idxs)
        if not res:
            return []
        H, W = maps[0].shape[:2]
        peaks = top_candidates(self.coarse, res, (H, W), k_peaks,
                               0.35 * self.tsize / self.kc)
        out = []
        for score, ai, (x, y), _ in peaks:
            out.append(Pose(self._full(x, self.kc, x0), self._full(y, self.kc, y0),
                            self.coarse.banks[ai].angle, score))
        return out

    def search_global(self, gray, k_peaks=None):
        """Returns candidate poses (coarse), best first."""
        H, W = gray.shape
        k = self.kc
        x1, y1 = W // k * k, H // k * k
        maps = image_maps(gray[:y1, :x1], k)
        idxs = list(range(0, len(self.c_angles), GLOBAL_ANGLE_STRIDE))
        return self._candidates(maps, idxs, 0, 0, k_peaks or NUM_CANDIDATES)

    def search_local(self, gray, cx, cy, radius, angle, k_peaks=2):
        H, W = gray.shape
        k = self.kc
        half_w = radius + self.coarse.max_w * k / 2.0 + k
        half_h = radius + self.coarse.max_h * k / 2.0 + k
        x0, y0, x1, y1 = align_crop(cx, cy, half_w, half_h, W, H, k)
        if x1 - x0 < k * self.coarse.max_w or y1 - y0 < k * self.coarse.max_h:
            return []
        maps = image_maps(gray[y0:y1, x0:x1], k)
        idxs = [i for i, a in enumerate(self.c_angles) if abs(a - angle) <= LOCAL_ANGLE_SPAN]
        return self._candidates(maps, idxs, x0, y0, k_peaks)

    def best_of(self, gray, candidates):
        """Fine-score every coarse candidate; return the best refined pose (score = fine score)."""
        best = None
        for c in candidates:
            p = self.refine(gray, c)
            if p.fine_score <= 0:
                continue
            if best is None or p.fine_score > best[0].fine_score:
                best = (p, self.debug_pts)
        if best is None:
            return None
        self.debug_pts = best[1]
        best[0].score = best[0].fine_score
        return best[0]

    # ---- fine refinement -----------------------------------------------------
    def refine(self, gray, pose, margin=None, span=None):
        margin = REFINE_MARGIN_PX if margin is None else margin
        span = COARSE_ANGLE_STEP if span is None else span
        H, W = gray.shape
        k = self.kf
        lv = self.fine
        half_w = lv.max_w * k / 2.0 + margin + k
        half_h = lv.max_h * k / 2.0 + margin + k
        x0, y0, x1, y1 = align_crop(pose.cx, pose.cy, half_w, half_h, W, H, k)
        if x1 - x0 < k * lv.max_w or y1 - y0 < k * lv.max_h:
            return Pose(pose.cx, pose.cy, pose.angle, 0.0, 0.0)
        maps = image_maps(gray[y0:y1, x0:x1], k)
        idxs = [i for i, a in enumerate(self.f_angles) if abs(a - pose.angle) <= span]
        res = lv.search(maps, idxs)
        if not res:
            return Pose(pose.cx, pose.cy, pose.angle, 0.0, 0.0)
        res.sort(key=lambda r: r[1])
        best = max(res, key=lambda r: r[0])
        fine = self._to_pose(lv, best, x0, y0)
        # sub-step angle by parabola over neighbouring angle scores
        pos = [i for i, r in enumerate(res) if r[1] == best[1]][0]
        if 0 < pos < len(res) - 1:
            sm, s0, sp = res[pos - 1][0], res[pos][0], res[pos + 1][0]
            den = sm - 2 * s0 + sp
            if den < -1e-9:
                fine.angle += float(np.clip(0.5 * (sm - sp) / den, -0.5, 0.5)) * FINE_ANGLE_STEP
        fine.fine_score = fine.score
        fine.score = fine.fine_score
        self._debug_hits(lv, best, maps, x0, y0)
        return fine

    def _debug_hits(self, lv, best, maps, x0, y0):
        score, ai, loc, acc = best
        bank = lv.banks[ai]
        yy, xx = bank.iy + loc[1], bank.ix + loc[0]
        hit = np.zeros(len(yy), bool)
        for gi, _ in bank.groups:
            sel = (bank.bn // 4) == gi
            if sel.any():
                vals = maps[gi][yy[sel], xx[sel], bank.bn[sel] % 4]
                hit[sel] = vals >= 0.5
        self.debug_pts = (self._full(xx, lv.k, x0), self._full(yy, lv.k, y0), hit)


# ============================================================
# TRACKER (temporal logic)
# ============================================================

class PotTracker:
    def __init__(self, det):
        self.det = det
        self.reset()

    def reset(self):
        self.prev = None
        self.last = None
        self.hold = 0
        self.pending = None      # [Pose, count]
        self.since_global = 10 ** 6
        self.status = "LOST"
        self.local_score = 0.0

    def _dist(self, a, b):
        return math.hypot(a.cx - b.cx, a.cy - b.cy)

    def _accept(self, gray, pose, status):
        self.prev = self.last if status in ("TRACKING", "WEAK") else None
        self.last, self.hold, self.pending = pose, 0, None
        self.status = status
        return pose

    def update(self, gray):
        det = self.det
        last = self.last
        T = det.tsize
        radius = TRACK_RADIUS_FACTOR * T + self.hold * TRACK_GROWTH_FACTOR * T

        local = None
        if last is not None and self.hold == 0:
            # fast path: fine level only, around the extrapolated position
            vx = vy = 0.0
            if self.prev is not None:
                lim = 0.5 * T
                vx = float(np.clip(last.cx - self.prev.cx, -lim, lim))
                vy = float(np.clip(last.cy - self.prev.cy, -lim, lim))
            guess = Pose(last.cx + vx, last.cy + vy, last.angle, 0.0)
            fast = det.refine(gray, guess, margin=FAST_MARGIN_FACTOR * T, span=FAST_ANGLE_SPAN)
            if fast.score >= GOOD_SCORE:
                return self._accept(gray, fast, "TRACKING")
        if last is not None:
            local = det.best_of(gray, det.search_local(gray, last.cx, last.cy, radius, last.angle))
        self.local_score = local.score if local else 0.0

        if local is not None and local.score >= GOOD_SCORE:
            return self._accept(gray, local, "TRACKING")

        glob = None
        self.since_global += 1
        if last is None or self.since_global >= GLOBAL_EVERY:
            glob = det.best_of(gray, det.search_global(gray))
            self.since_global = 0

            if glob is not None and glob.score >= GLOBAL_MIN:
                if last is None:
                    if glob.score >= ACQUIRE_SCORE:
                        return self._accept(gray, glob, "ACQUIRED")
                elif self._dist(glob, last) <= radius:
                    return self._accept(gray, glob, "TRACKING")
                else:
                    local_s = local.score if local else 0.0
                    if glob.score >= FAR_SCORE and glob.score >= local_s + FAR_MARGIN:
                        if self.pending and self._dist(glob, self.pending[0]) <= 0.6 * T:
                            self.pending[0], self.pending[1] = glob, self.pending[1] + 1
                        else:
                            self.pending = [glob, 1]
                        if self.pending[1] >= CONFIRM_FRAMES or glob.score >= FAR_SURE_SCORE:
                            return self._accept(gray, glob, "JUMPED")
                    else:
                        self.pending = None
            else:
                self.pending = None

        if local is not None and local.score >= LOCAL_MIN:
            return self._accept(gray, local, "WEAK")

        if last is not None:
            self.hold += 1
            if self.hold <= HOLD_MAX:
                self.status = f"HOLD {self.hold}"
                return last
            self.last = None
        self.status = "LOST"
        return None


# ============================================================
# MAIN
# ============================================================

def main():
    from pipewire_capture import PortalCapture, CaptureStream

    gray_t, alpha_t = load_template()
    det = Detector(gray_t, alpha_t)
    tracker = PotTracker(det)
    th, tw = gray_t.shape
    print(f"Template {tw}x{th}; coarse 1/{det.kc} ({len(det.c_angles)} angles, "
          f"{det.coarse.banks[0].n} pts), fine 1/{det.kf} ({len(det.f_angles)} angles, "
          f"{det.fine.banks[0].n} pts)")

    print("\nPlease select the Getting Over It window.\n")
    session = PortalCapture().select_window()
    if session is None:
        print("Screen selection cancelled.")
        raise SystemExit
    print(f"Capture size: {session.width} x {session.height}")

    stream = CaptureStream(session.fd, session.node_id, session.width, session.height,
                           capture_interval=0)
    stream.start()
    Path("captures").mkdir(exist_ok=True)
    n_saved = 0

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

            t0 = time.perf_counter()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
            pose = tracker.update(gray)
            ms = (time.perf_counter() - t0) * 1000

            if pose is not None:
                ax, ay = anchor_point(pose.cx, pose.cy, pose.angle)
                print(f"\r{tracker.status:<9} pot=({pose.cx:7.1f},{pose.cy:7.1f}) "
                      f"anchor=({ax:7.1f},{ay:7.1f}) angle={pose.angle:+6.1f} "
                      f"score={pose.score:.2f} {ms:5.1f}ms   ", end="", flush=True)
            else:
                print(f"\r{tracker.status:<9}" + " " * 70, end="", flush=True)

            if SHOW_WINDOW:
                display = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                ok = tracker.status in ("TRACKING", "ACQUIRED", "JUMPED")
                color = ((0, 255, 0) if ok else (0, 165, 255) if pose is not None else (0, 0, 255))
                if pose is not None:
                    hw, hh = tw / 2, th / 2
                    corners = []
                    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                        ox, oy = rot_ccw(sx * hw, sy * hh, pose.angle)
                        corners.append((int(pose.cx + ox), int(pose.cy + oy)))
                    cv2.polylines(display, [np.array(corners, np.int32)], True, color, 2)
                    cv2.circle(display, (int(pose.cx), int(pose.cy)), 5, color, -1)
                    cv2.line(display, (int(pose.cx), int(pose.cy)), (int(ax), int(ay)), color, 2)
                    cv2.circle(display, (int(ax), int(ay)), 8, (255, 255, 0), -1)
                    if det.debug_pts is not None and tracker.status[:4] != "HOLD":
                        px, py, hit = det.debug_pts      # green = template edge point found, red = missing
                        for x, y, h in zip(px, py, hit):
                            cv2.circle(display, (int(x), int(y)), 2,
                                       (0, 255, 0) if h else (0, 0, 255), -1)
                cv2.putText(display, f"{tracker.status} score={pose.score if pose else 0:.2f} "
                                     f"angle={pose.angle if pose else 0:+.0f} {ms:.0f}ms",
                            (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("Getting Over It - Pot Tracker", display)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    tracker.reset()
                if key == ord("t"):
                    cv2.imwrite("debug_frame.png", display)
                    print("\nSaved debug_frame.png")
                if key == ord("k"):
                    cv2.imwrite(f"captures/frame_{n_saved:04d}.png",
                                cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR))
                    n_saved += 1
                    print(f"\nSaved captures/frame_{n_saved - 1:04d}.png")
    finally:
        stream.stop()
        session.close()
        cv2.destroyAllWindows()
        print("\nStopped.")


if __name__ == "__main__":
    main()