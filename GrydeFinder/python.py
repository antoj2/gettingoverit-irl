"""
Pot tracker v3 for "Getting Over It" (Wayland / PipeWire capture).

What changed from v2, and why (diagnosed on real screenshots)
-------------------------------------------------------------
v2 scored a candidate by "what fraction of the template's edge points land on a
matching image edge".  On dense, noisy rock that fraction is ~0.55-0.62 *by
pure chance*, which is as high as a real, partly hidden pot (0.52-0.67).  So a
rock could beat the pot, and because v2 accepted anything >= 0.55 without ever
re-checking the whole screen, it then stayed glued to the rock.

v3 fixes both problems:

1. CHANCE-CORRECTED EDGE SCORE.  For every position we also estimate how many
   template points would match *by luck*, from the local density of edges of
   the right orientations around it (E).  The score is (S - E) / (1 - E):
   0 = "no better than clutter", 1 = perfect.  Busy rock now scores ~0.

2. DARKNESS CHECK.  The pot interior is near-black; rock almost never is.  The
   score is multiplied by the fraction of the pot's interior that is dark.  The
   darkness thresholds follow the pot's actual brightness, which adapts as the
   lighting drifts while you climb.

3. TORSO CHECK.  The player's skin-coloured torso always sticks out of the rim.
   The score is multiplied by a soft factor (0.4..1) for how much of the area
   above the rim is skin coloured.  (Soft, so a hidden or tinted torso only
   costs you some score instead of killing the match.)  Set USE_TORSO_CUE=False
   to disable.

4. TRACK IS CONTINUOUSLY CHALLENGED.  A background thread keeps searching the
   whole screen.  If it finds something clearly better than what we follow, we
   switch (after confirmation).  A weak or lost track is retried immediately.

Everything else (edge-orientation matching, +-45 deg rotation, coarse-to-fine,
no Kalman filter) is as in v2.

Keys:  q quit | r reset | t save debug_frame.png | k save raw frame to captures/
Offline test of a saved frame (no capture needed):
       python pot_tracker.py --image captures/frame_0000.png
"""

import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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
COARSE_ANGLE_STEP = 6.0
FINE_ANGLE_STEP = 1.5
GLOBAL_ANGLE_STRIDE = 2        # whole-screen search tries every Nth coarse angle
NUM_CANDIDATES = 6             # coarse peaks re-scored at the fine level
LOCAL_ANGLE_SPAN = 24.0        # tracking: +/- degrees around the last angle

# --- scales ------------------------------------------------
COARSE_TARGET_PX = 64          # coarse level shrinks the frame so the template is ~this big
REFINE_MARGIN_PX = 14          # full-res px the fine step may move a coarse result

# --- edge features -----------------------------------------
NUM_BINS = 16                  # orientation bins over 180 deg (multiple of 4)
IMG_BLUR_SIGMA = 0.8
MAG_THRESHOLD = 40.0
TEMPLATE_MAG_MIN = 40.0
SPREAD_PX = 3
NEIGHBOR_BIN_WEIGHT = 0.3
TARGET_POINTS = 150

# --- darkness cue ------------------------------------------
USE_DARK_CUE = True
DARK_LO_FACTOR = 1.2           # fully "dark" below pot_gray * this
DARK_HI_FACTOR = 2.1           # fully "not dark" above pot_gray * this
ADAPT_DARK_RATE = 0.05         # how fast the pot brightness estimate follows the video
ADAPT_MIN_SCORE = 0.30         # ...only from confident detections

# --- torso (skin) cue --------------------------------------
USE_TORSO_CUE = True
SKIN_H = (3, 24)               # OpenCV hue range (0..179) of the player's skin
SKIN_S_MIN = 70
SKIN_V_MIN = 100
TORSO_HALF_W = 0.27            # box above the rim, as fractions of the template size
TORSO_TOP = 0.88               # box top    = this x template height above the pot centre
TORSO_BOTTOM = 0.43            # box bottom = this x template height above the pot centre
TORSO_FLOOR = 0.40             # score factor when there is no skin at all above the pot
TORSO_FULL = 0.50              # skin fraction that gives the full factor 1.0

# --- decisions (scores are chance-corrected confidences, 0..1) ---
TRACK_OK = 0.18                # follow the pot normally
WEAK_MIN = 0.10                # follow, but flag WEAK and keep searching the whole screen
ACQUIRE_SCORE = 0.22           # start tracking from nothing
ACQUIRE_WEAK = 0.14            # ...or this, if it is also a clear winner on the whole screen:
ACQUIRE_RATIO = 2.0            #    its score is this many times the runner-up's
GLOBAL_MIN = 0.15              # whole-screen results below this are ignored
FAR_MIN = 0.26                 # to leave the current track for a far-away candidate...
FAR_RATIO = 1.5                # ...it must also be this many times better than the current track
FAR_SURE = 0.40                # a far candidate this good is followed immediately
CONFIRM_FRAMES = 2             # otherwise it must win this many global searches in a row
VERIFY_INTERVAL_S = 1.0        # background whole-screen search interval while tracking

# --- tracking geometry -------------------------------------
TRACK_RADIUS_FACTOR = 0.6      # coarse local search radius, x template size
TRACK_GROWTH_FACTOR = 0.25     # extra radius per held frame, x template size
TRACK_RADIUS_MAX = 1.4         # ...but never more than this x template size (the whole-screen thread covers the rest)
FAST_MARGIN_FACTOR = 0.2       # fast path may move this x template size per frame...
FAST_ANGLE_SPAN = 3.0          # ...and rotate this many degrees
HOLD_MAX = 20                  # frames to hold the last pose before LOST

# --- output ------------------------------------------------
ANCHOR_UP_PX = 80.0            # px from the pot centre to your point, along the pot's "up"

THREADS = max(1, min(8, (os.cpu_count() or 2)))


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
    ang = np.mod(np.arctan2(gy, gx), np.pi)
    return np.floor(ang / np.pi * NUM_BINS + 0.5).astype(np.int32) % NUM_BINS


def downscale(img, k):
    if k == 1:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (w // k, h // k), interpolation=cv2.INTER_AREA)


def edge_maps(gray_u8):
    """NUM_BINS/4 float32 images with 4 channels each: 1 where an edge of that orientation is
    within SPREAD_PX, NEIGHBOR_BIN_WEIGHT for the neighbouring orientation bin."""
    g = gray_u8.astype(np.float32)
    if IMG_BLUR_SIGMA > 0:
        g = cv2.GaussianBlur(g, (0, 0), IMG_BLUR_SIGMA)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    bins = orientation_bins(gx, gy)
    valid = mag > MAG_THRESHOLD
    kernel = np.ones((SPREAD_PX, SPREAD_PX), np.uint8)
    spread = [cv2.dilate(((bins == b) & valid).astype(np.uint8), kernel).astype(np.float32)
              for b in range(NUM_BINS)]
    soft = []
    for b in range(NUM_BINS):
        nb = np.maximum(spread[(b - 1) % NUM_BINS], spread[(b + 1) % NUM_BINS])
        soft.append(np.maximum(spread[b], nb * NEIGHBOR_BIN_WEIGHT))
    return [cv2.merge(soft[i:i + 4]) for i in range(0, NUM_BINS, 4)]


def subpixel(sm, x, y):
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


def shift_slice(K, dy, dx, h, w):
    """out[y, x] = K[y + dy, x + dx], zero where that falls outside K."""
    out = np.zeros((h, w), np.float32)
    ys0, ys1 = max(0, -dy), min(h, K.shape[0] - dy)
    xs0, xs1 = max(0, -dx), min(w, K.shape[1] - dx)
    if ys1 > ys0 and xs1 > xs0:
        out[ys0:ys1, xs0:xs1] = K[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx]
    return out


def rotated_rect_mask(k, angle_deg, x_rng, y_rng):
    """Rectangle given in full-res px relative to the pot centre, rotated with the pot and drawn
    at level scale.  Returns (mask float32, ox, oy): where the pot centre lies relative to the
    mask array's top-left (may be outside the array)."""
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    corners = ((x_rng[0], y_rng[0]), (x_rng[1], y_rng[0]), (x_rng[1], y_rng[1]), (x_rng[0], y_rng[1]))
    pts = np.array([((x * c + y * s) / k, (-x * s + y * c) / k) for x, y in corners], np.float32)
    mn = np.floor(pts.min(0)).astype(int) - 1
    mx = np.ceil(pts.max(0)).astype(int) + 1
    W, H = int(mx[0] - mn[0] + 1), int(mx[1] - mn[1] + 1)
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.rint((pts - mn) * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    return m.astype(np.float32), int(-mn[0]), int(-mn[1])


# ============================================================
# PER-IMAGE FEATURES
# ============================================================

class Features:
    """Everything the scorer needs from one image (or crop) at scale 1/k."""

    def __init__(self, bgr, k, dark_lo, dark_hi):
        H, W = bgr.shape[:2]
        h, w = H // k * k, W // k * k
        small = downscale(np.ascontiguousarray(bgr[:h, :w]), k)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        self.k = k
        self.shape = gray.shape
        self.maps = edge_maps(gray)
        g = gray.astype(np.float32)
        self.dark = np.clip((dark_hi - g) / max(dark_hi - dark_lo, 1.0), 0, 1).astype(np.float32)
        self.skin = None
        if USE_TORSO_CUE:
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            self.skin = ((hsv[:, :, 0] >= SKIN_H[0]) & (hsv[:, :, 0] <= SKIN_H[1]) &
                         (hsv[:, :, 1] >= SKIN_S_MIN) & (hsv[:, :, 2] >= SKIN_V_MIN)).astype(np.float32)
        self._box = {}

    def box(self, bw, bh):
        key = (bw, bh)
        if key not in self._box:
            self._box[key] = [cv2.boxFilter(m, -1, (bw, bh), normalize=True,
                                            borderType=cv2.BORDER_REPLICATE) for m in self.maps]
        return self._box[key]


# ============================================================
# TEMPLATE LEVELS
# ============================================================

@dataclass
class Bank:
    angle: float
    groups: list            # [(group_index, template(hb, wb, 4))]
    n: int
    ox: int                 # pot centre inside the template array
    oy: int
    hb: int
    wb: int
    iy: np.ndarray
    ix: np.ndarray
    bn: np.ndarray
    hist: np.ndarray = None     # orientation histogram of the template points (NUM_BINS,)
    dmask: np.ndarray = None    # rotated pot interior (hb, wb)
    dsum: float = 1.0
    kmask: np.ndarray = None    # rotated torso box
    ksum: float = 1.0
    kox: int = 0
    koy: int = 0


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
    """Template at scale 1/k, pre-rotated for a list of angles."""

    def __init__(self, gray_full, alpha_full, k, angles):
        self.k = k
        self.angles = list(angles)
        th, tw = gray_full.shape
        self.tw_full, self.th_full = tw, th
        wl, hl = tw // k, th // k
        wl -= (wl % 2 == 0)
        hl -= (hl % 2 == 0)
        x0, y0 = (tw - wl * k) // 2, (th - hl * k) // 2
        g = downscale(np.ascontiguousarray(gray_full[y0:y0 + hl * k, x0:x0 + wl * k]), k)
        a = downscale(np.ascontiguousarray(alpha_full[y0:y0 + hl * k, x0:x0 + wl * k]), k)
        g = g.astype(np.float32)
        inside = a > 127
        self.mask = inside.astype(np.float32)
        fill = g[inside].mean() - 40.0
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
        self.box_w, self.box_h = wl, hl
        self.banks = [self._bank(a_) for a_ in self.angles]

        # extents (full-res px) used to cut crops that contain the whole template AND the torso box
        self.side_px = k * max(max(b.ox, b.wb - b.ox, b.kox, b.kmask.shape[1] - b.kox) for b in self.banks)
        self.up_px = k * max(max(b.oy, b.koy) for b in self.banks)
        self.down_px = k * max(max(b.hb - b.oy, b.kmask.shape[0] - b.koy, 0) for b in self.banks)
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
        bank = Bank(angle_deg, groups, len(ix), -minx, -miny, hb, wb, iy - miny, ix - minx, bn)
        bank.hist = (np.bincount(bn, minlength=NUM_BINS) / len(bn)).astype(np.float32)

        # rotated pot interior, aligned with the template array
        hl, wl = self.size_level
        cx, cy = (wl - 1) / 2.0, (hl - 1) / 2.0
        M = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
        M[0, 2] += bank.ox - cx
        M[1, 2] += bank.oy - cy
        dm = cv2.warpAffine(self.mask, M, (wb, hb), flags=cv2.INTER_LINEAR)
        bank.dmask = np.ascontiguousarray((dm > 0.5).astype(np.float32))
        bank.dsum = max(float(bank.dmask.sum()), 1.0)

        # rotated torso box above the rim
        tw, th = self.tw_full, self.th_full
        km, kox, koy = rotated_rect_mask(self.k, angle_deg,
                                         (-TORSO_HALF_W * tw, TORSO_HALF_W * tw),
                                         (-TORSO_TOP * th, -TORSO_BOTTOM * th))
        bank.kmask, bank.kox, bank.koy = km, kox, koy
        bank.ksum = max(float(km.sum()), 1.0)
        return bank

    # ------------------------------------------------------------------
    def search(self, F, angle_idxs, pool=None):
        """Score maps for the given angles.  Returns [(peak, angle_idx, (x, y), conf_map)], where
        conf_map is indexed by the template array's top-left corner."""
        H, W = F.shape
        idxs = [i for i in angle_idxs if H >= self.banks[i].hb and W >= self.banks[i].wb]
        if not idxs:
            return []

        # chance level E for every angle at once (position-wise density of matching edges)
        hist = np.stack([self.banks[i].hist for i in idxs], axis=1)            # (NUM_BINS, A)
        boxes = F.box(self.box_w, self.box_h)
        Eall = None
        for gi, bx in enumerate(boxes):
            part = bx.reshape(-1, 4) @ hist[gi * 4:(gi + 1) * 4]
            Eall = part if Eall is None else Eall + part
        Eall = Eall.reshape(H, W, len(idxs))

        def one(j, ai):
            bank = self.banks[ai]
            acc = None
            for gi, T in bank.groups:
                r = cv2.matchTemplate(F.maps[gi], T, cv2.TM_CCORR)
                acc = r if acc is None else acc + r
            acc = acc * (1.0 / bank.n)
            h, w = acc.shape
            E = Eall[bank.oy:bank.oy + h, bank.ox:bank.ox + w, j]
            conf = np.maximum((acc - E) / (1.0 - E + 1e-3), 0.0)
            if USE_DARK_CUE:
                D = cv2.matchTemplate(F.dark, bank.dmask, cv2.TM_CCORR) * (1.0 / bank.dsum)
                conf *= D
            if USE_TORSO_CUE and F.skin is not None:
                kh, kw = bank.kmask.shape
                if H >= kh and W >= kw:
                    K = cv2.matchTemplate(F.skin, bank.kmask, cv2.TM_CCORR) * (1.0 / bank.ksum)
                    K = shift_slice(K, bank.oy - bank.koy, bank.ox - bank.kox, h, w)
                else:
                    K = np.zeros((h, w), np.float32)
                conf *= TORSO_FLOOR + (1.0 - TORSO_FLOOR) * np.minimum(1.0, K / TORSO_FULL)
            conf = np.ascontiguousarray(conf, dtype=np.float32)
            _, mx, _, loc = cv2.minMaxLoc(conf)
            return float(mx), ai, loc, conf

        if pool is not None and len(idxs) > 2:
            return list(pool.map(lambda ja: one(*ja), list(enumerate(idxs))))
        return [one(j, ai) for j, ai in enumerate(idxs)]


def top_candidates(level, results, shape, k_peaks, nms_radius):
    H, W = shape
    best = np.full((H, W), -1.0, np.float32)
    best_ai = np.zeros((H, W), np.int32)
    for score, ai, loc, conf in results:
        bank = level.banks[ai]
        h, w = conf.shape
        view = best[bank.oy:bank.oy + h, bank.ox:bank.ox + w]
        better = conf > view
        view[better] = conf[better]
        best_ai[bank.oy:bank.oy + h, bank.ox:bank.ox + w][better] = ai
    out = []
    r = int(max(1, nms_radius))
    for _ in range(k_peaks):
        _, mx, _, loc = cv2.minMaxLoc(best)
        if mx <= 0:
            break
        x, y = loc
        out.append((float(mx), int(best_ai[y, x]), (x, y)))
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
    dbg: tuple = field(default=None, repr=False)     # (xs, ys, hit) of template points, for the overlay
    ratio: float = 0.0                               # best coarse score / runner-up elsewhere (whole-screen results)


def align_box(x0, y0, x1, y1, W, H, k):
    x0 = int(max(0, x0)) // k * k
    y0 = int(max(0, y0)) // k * k
    x1 = int(min(W, x1)) // k * k
    y1 = int(min(H, y1)) // k * k
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
        self.pool = ThreadPoolExecutor(max_workers=THREADS) if THREADS > 1 else None
        self.pot_gray = float(np.clip(np.median(gray_t[alpha_t > 127]), 20, 80))
        self.th_full, self.tw_full = th, tw

    # ---- darkness model ------------------------------------------------------
    @property
    def dark_lo(self):
        return self.pot_gray * DARK_LO_FACTOR

    @property
    def dark_hi(self):
        return self.pot_gray * DARK_HI_FACTOR

    def adapt_darkness(self, bgr, pose):
        """Follow the pot's brightness (lighting changes while climbing)."""
        r = int(0.18 * self.tsize)
        cx, cy = int(pose.cx), int(pose.cy + 0.1 * self.tsize)
        H, W = bgr.shape[:2]
        x0, x1, y0, y1 = max(0, cx - r), min(W, cx + r), max(0, cy - r), min(H, cy + r)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        g = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        v = float(np.percentile(g, 30))          # low percentile: arms / hammer are brighter
        v = float(np.clip(v, 20, 80))
        self.pot_gray += ADAPT_DARK_RATE * (v - self.pot_gray)

    # ---- conversions ---------------------------------------------------------
    @staticmethod
    def _full(v_level, k, origin):
        return origin + (v_level + 0.5) * k - 0.5

    def _pose_from(self, level, ai, loc, conf, x0, y0):
        bank = level.banks[ai]
        sx, sy = subpixel(conf, loc[0], loc[1])
        cxl = loc[0] + sx + bank.ox
        cyl = loc[1] + sy + bank.oy
        return Pose(self._full(cxl, level.k, x0), self._full(cyl, level.k, y0),
                    bank.angle, float(conf[loc[1], loc[0]]))

    # ---- coarse searches -----------------------------------------------------
    def _candidates(self, F, idxs, x0, y0, k_peaks, pool=None):
        res = self.coarse.search(F, idxs, pool)
        if not res:
            return []
        peaks = top_candidates(self.coarse, res, F.shape, k_peaks, 0.35 * self.tsize / self.kc)
        return [Pose(self._full(x, self.kc, x0), self._full(y, self.kc, y0),
                     self.coarse.banks[ai].angle, sc) for sc, ai, (x, y) in peaks]

    def search_global(self, bgr, k_peaks=None):
        k = self.kc
        F = Features(bgr, k, self.dark_lo, self.dark_hi)
        idxs = list(range(0, len(self.c_angles), GLOBAL_ANGLE_STRIDE))
        return self._candidates(F, idxs, 0, 0, k_peaks or NUM_CANDIDATES, self.pool)

    def search_local(self, bgr, cx, cy, radius, angle, k_peaks=2):
        H, W = bgr.shape[:2]
        k, lv = self.kc, self.coarse
        x0, y0, x1, y1 = align_box(cx - radius - lv.side_px - k, cy - radius - lv.up_px - k,
                                   cx + radius + lv.side_px + k, cy + radius + lv.down_px + k, W, H, k)
        if x1 - x0 < k * lv.max_w or y1 - y0 < k * lv.max_h:
            return []
        F = Features(bgr[y0:y1, x0:x1], k, self.dark_lo, self.dark_hi)
        idxs = [i for i, a in enumerate(self.c_angles) if abs(a - angle) <= LOCAL_ANGLE_SPAN]
        return self._candidates(F, idxs, x0, y0, k_peaks)

    def best_of(self, bgr, candidates):
        """Re-score coarse candidates at the fine level; return the best (or None).
        pose.ratio = how much better the winner's coarse score is than any other candidate that is
        a different place (a clear winner has a high ratio)."""
        best, best_c = None, None
        for c in candidates:
            p = self.refine(bgr, c)
            if p.score > 0 and (best is None or p.score > best.score):
                best, best_c = p, c
        if best is None:
            return None
        others = [c.score for c in candidates
                  if c is not best_c and math.hypot(c.cx - best_c.cx, c.cy - best_c.cy) > 0.6 * self.tsize]
        best.ratio = float(min(20.0, best_c.score / max(max(others, default=0.0), 0.03)))
        return best

    # ---- fine refinement -----------------------------------------------------
    def refine(self, bgr, pose, margin=None, span=None):
        margin = REFINE_MARGIN_PX if margin is None else margin
        span = COARSE_ANGLE_STEP if span is None else span
        H, W = bgr.shape[:2]
        k, lv = self.kf, self.fine
        x0, y0, x1, y1 = align_box(pose.cx - lv.side_px - margin - k, pose.cy - lv.up_px - margin - k,
                                   pose.cx + lv.side_px + margin + k, pose.cy + lv.down_px + margin + k,
                                   W, H, k)
        zero = Pose(pose.cx, pose.cy, pose.angle, 0.0)
        if x1 - x0 < k * lv.max_w or y1 - y0 < k * lv.max_h:
            return zero
        F = Features(bgr[y0:y1, x0:x1], k, self.dark_lo, self.dark_hi)
        idxs = [i for i, a in enumerate(self.f_angles) if abs(a - pose.angle) <= span]
        res = lv.search(F, idxs)
        if not res:
            return zero
        res.sort(key=lambda r: r[1])
        pos = max(range(len(res)), key=lambda i: res[i][0])
        _, ai, loc, conf = res[pos]
        fine = self._pose_from(lv, ai, loc, conf, x0, y0)
        if 0 < pos < len(res) - 1:               # sub-step angle
            sm, s0, sp = res[pos - 1][0], res[pos][0], res[pos + 1][0]
            den = sm - 2 * s0 + sp
            if den < -1e-9:
                fine.angle += float(np.clip(0.5 * (sm - sp) / den, -0.5, 0.5)) * FINE_ANGLE_STEP
        fine.dbg = self._debug_hits(lv, ai, loc, F, x0, y0)
        return fine

    @staticmethod
    def _debug_hits(lv, ai, loc, F, x0, y0):
        bank = lv.banks[ai]
        yy, xx = bank.iy + loc[1], bank.ix + loc[0]
        hit = np.zeros(len(yy), bool)
        for gi, _ in bank.groups:
            sel = (bank.bn // 4) == gi
            if sel.any():
                hit[sel] = F.maps[gi][yy[sel], xx[sel], bank.bn[sel] % 4] >= 0.5
        return (Detector._full(xx, lv.k, x0), Detector._full(yy, lv.k, y0), hit)


# ============================================================
# BACKGROUND WHOLE-SCREEN SEARCH
# ============================================================

class GlobalWorker:
    def __init__(self, det):
        self.det = det
        self.ex = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.t_done = -1e9

    def busy(self):
        return self.future is not None and not self.future.done()

    def submit(self, bgr):
        if self.busy():
            return False
        frame = bgr.copy()
        self.future = self.ex.submit(lambda: self.det.best_of(frame, self.det.search_global(frame)))
        return True

    def poll(self):
        """(finished, pose_or_None).  finished=False when nothing new."""
        if self.future is None or not self.future.done():
            return False, None
        fut, self.future = self.future, None
        self.t_done = time.perf_counter()
        try:
            return True, fut.result()
        except Exception as e:                     # never let the worker kill the tracker
            print(f"\n[global search failed: {e!r}]")
            return True, None


# ============================================================
# TRACKER (temporal logic)
# ============================================================

class PotTracker:
    def __init__(self, det, use_worker=True):
        self.det = det
        self.worker = GlobalWorker(det) if use_worker else None
        self.reset()

    def reset(self):
        self.prev = None
        self.last = None
        self.hold = 0
        self.pending = None
        self.cur_conf = 0.0
        self.t_global = -1e9
        self.status = "LOST"

    def _dist(self, a, b):
        return math.hypot(a.cx - b.cx, a.cy - b.cy)

    def _accept(self, bgr, pose, status, moving=True):
        self.prev = self.last if (moving and status in ("TRACKING", "WEAK")) else None
        self.last, self.hold, self.pending = pose, 0, None
        self.cur_conf = pose.score if status in ("ACQUIRED", "JUMPED") else \
            0.6 * self.cur_conf + 0.4 * pose.score if self.cur_conf > 0 else pose.score
        self.status = status
        if pose.score >= ADAPT_MIN_SCORE:
            self.det.adapt_darkness(bgr, pose)
        return pose

    def update(self, bgr):
        det, T = self.det, self.det.tsize
        now = time.perf_counter()
        last = self.last

        # ---------- 1. follow the current track ----------
        local = None
        if last is not None:
            if self.hold == 0:
                vx = vy = 0.0
                if self.prev is not None:
                    lim = 0.5 * T
                    vx = float(np.clip(last.cx - self.prev.cx, -lim, lim))
                    vy = float(np.clip(last.cy - self.prev.cy, -lim, lim))
                guess = Pose(last.cx + vx, last.cy + vy, last.angle, 0.0)
                fast = det.refine(bgr, guess, margin=FAST_MARGIN_FACTOR * T, span=FAST_ANGLE_SPAN)
                if fast.score >= TRACK_OK:
                    local = fast
            if local is None:
                radius = min(TRACK_RADIUS_FACTOR + self.hold * TRACK_GROWTH_FACTOR, TRACK_RADIUS_MAX) * T
                local = det.best_of(bgr, det.search_local(bgr, last.cx, last.cy, radius, last.angle))
        local_score = local.score if local is not None else 0.0
        track_ok = local is not None and local_score >= TRACK_OK

        # ---------- 2. whole-screen results from the background thread ----------
        accepted = None
        if self.worker is not None:
            done, g = self.worker.poll()
        else:                                          # synchronous mode (used by tests)
            done, g = False, None
            if last is None or not track_ok or now - self.t_global >= VERIFY_INTERVAL_S:
                g = det.best_of(bgr, det.search_global(bgr))
                done, self.t_global = True, time.perf_counter()
        if done:
            self.t_global = now
            if g is not None and g.score >= GLOBAL_MIN:
                # the frame has moved on while the search ran: re-check the candidate on this frame
                v = det.best_of(bgr, det.search_local(bgr, g.cx, g.cy, 0.5 * T, g.angle))
                if v is not None:
                    if last is None or (not track_ok and local_score < WEAK_MIN):
                        if v.score >= ACQUIRE_SCORE or (v.score >= ACQUIRE_WEAK and g.ratio >= ACQUIRE_RATIO):
                            accepted = (v, "ACQUIRED" if last is None else "JUMPED")
                    elif self._dist(v, last) > 0.6 * T:
                        better = (v.score >= FAR_MIN and v.score >= FAR_RATIO * max(local_score, self.cur_conf * 0.5))
                        if better:
                            if self.pending and self._dist(v, self.pending[0]) <= 0.6 * T:
                                self.pending[0], self.pending[1] = v, self.pending[1] + 1
                            else:
                                self.pending = [v, 1]
                            if self.pending[1] >= CONFIRM_FRAMES or v.score >= FAR_SURE:
                                accepted = (v, "JUMPED")
                        else:
                            self.pending = None
            elif last is not None:
                self.pending = None

        if accepted is not None:
            return self._accept(bgr, accepted[0], accepted[1], moving=False)

        # ---------- 3. ask for a new whole-screen search when needed ----------
        if self.worker is not None and not self.worker.busy():
            if last is None or not track_ok or now - self.t_global >= VERIFY_INTERVAL_S:
                self.worker.submit(bgr)

        # ---------- 4. decide the status of this frame ----------
        if track_ok:
            return self._accept(bgr, local, "TRACKING")
        if local is not None and local_score >= WEAK_MIN:
            return self._accept(bgr, local, "WEAK")
        if last is not None:
            self.hold += 1
            if self.hold <= HOLD_MAX:
                self.status = f"HOLD {self.hold}"
                return last
            self.last = None
        self.status = "LOST"
        return None


# ============================================================
# OFFLINE MODE:  python pot_tracker.py --image frame.png
# ============================================================

def run_image(path):
    gray_t, alpha_t = load_template()
    det = Detector(gray_t, alpha_t)
    bgr = cv2.imread(path)
    if bgr is None:
        raise SystemExit(f"Could not read {path}")
    t0 = time.perf_counter()
    cands = det.search_global(bgr, k_peaks=8)
    best = det.best_of(bgr, cands)
    print(f"{path}: whole-screen search {1000 * (time.perf_counter() - t0):.0f} ms")
    for p in cands:
        print(f"   coarse candidate ({p.cx:7.1f},{p.cy:7.1f}) angle {p.angle:+5.0f}  score {p.score:.3f}")
    out = bgr.copy()
    if best is not None:
        print(f"BEST: pot=({best.cx:.1f},{best.cy:.1f}) angle={best.angle:+.1f} score={best.score:.3f}")
        draw_pose(out, best, gray_t.shape[1], gray_t.shape[0], (0, 255, 0))
    cv2.imwrite("out_" + Path(path).name, out)
    print("wrote out_" + Path(path).name)


def draw_pose(display, pose, tw, th, color):
    hw, hh = tw / 2, th / 2
    corners = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        ox, oy = rot_ccw(sx * hw, sy * hh, pose.angle)
        corners.append((int(pose.cx + ox), int(pose.cy + oy)))
    ax, ay = anchor_point(pose.cx, pose.cy, pose.angle)
    cv2.polylines(display, [np.array(corners, np.int32)], True, color, 2)
    cv2.circle(display, (int(pose.cx), int(pose.cy)), 5, color, -1)
    cv2.line(display, (int(pose.cx), int(pose.cy)), (int(ax), int(ay)), color, 2)
    cv2.circle(display, (int(ax), int(ay)), 8, (255, 255, 0), -1)
    if pose.dbg is not None:
        px, py, hit = pose.dbg           # green = template edge point found, red = missing
        for x, y, h in zip(px, py, hit):
            cv2.circle(display, (int(x), int(y)), 2, (0, 255, 0) if h else (0, 0, 255), -1)


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
          f"{det.fine.banks[0].n} pts), {THREADS} threads")

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
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            pose = tracker.update(bgr)
            ms = (time.perf_counter() - t0) * 1000

            if pose is not None:
                ax, ay = anchor_point(pose.cx, pose.cy, pose.angle)
                print(f"\r{tracker.status:<9} pot=({pose.cx:7.1f},{pose.cy:7.1f}) "
                      f"anchor=({ax:7.1f},{ay:7.1f}) angle={pose.angle:+6.1f} "
                      f"score={pose.score:.2f} {ms:5.1f}ms   ", end="", flush=True)
            else:
                print(f"\r{tracker.status:<9}" + " " * 70, end="", flush=True)

            if SHOW_WINDOW:
                display = bgr.copy()
                ok = tracker.status in ("TRACKING", "ACQUIRED", "JUMPED")
                color = ((0, 255, 0) if ok else (0, 165, 255) if pose is not None else (0, 0, 255))
                if pose is not None:
                    draw_pose(display, pose, tw, th, color)
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
                    cv2.imwrite(f"captures/frame_{n_saved:04d}.png", bgr)
                    n_saved += 1
                    print(f"\nSaved captures/frame_{n_saved - 1:04d}.png")
    finally:
        stream.stop()
        session.close()
        cv2.destroyAllWindows()
        print("\nStopped.")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--image":
        run_image(sys.argv[2])
    else:
        main()