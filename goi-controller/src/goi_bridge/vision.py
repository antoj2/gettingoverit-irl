"""Optional camera: MediaPipe pose -> hand-to-shoulder ratio used as hammer extension.

ratio = |hand_centre - same_side_shoulder| / |shoulder_left - shoulder_right|   (image pixels)
Dividing by shoulder width makes it independent of how far you stand from the camera.
Limitation: it is a 2-D measurement, so reaching *towards* the camera is foreshortened.
"""
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# MediaPipe pose landmark indices
SIDES = {
    "right": {"shoulder": 12, "other": 11, "hand": (16, 18, 20)},   # wrist, pinky, index
    "left":  {"shoulder": 11, "other": 12, "hand": (15, 17, 19)},
}
MODEL_NAME = "pose_landmarker_full.task"


def hand_center(lms, ids):
    return (sum(lms[i].x for i in ids) / len(ids), sum(lms[i].y for i in ids) / len(ids))


def extension_ratio(lms, w, h, hand="right", min_visibility=0.5):
    """Returns (ratio, hand_xy, shoulder_xy) in normalized coords, or None if not reliably visible."""
    side = SIDES[hand]
    needed = (side["shoulder"], side["other"], side["hand"][0])
    for i in needed:
        if getattr(lms[i], "visibility", 1.0) < min_visibility:
            return None
    hx, hy = hand_center(lms, side["hand"])
    sx, sy = lms[side["shoulder"]].x, lms[side["shoulder"]].y
    ox, oy = lms[side["other"]].x, lms[side["other"]].y
    width = math.hypot((sx - ox) * w, (sy - oy) * h)
    if width < 10.0:
        return None
    dist = math.hypot((hx - sx) * w, (hy - sy) * h)
    return dist / width, (hx, hy), (sx, sy)


def find_model(explicit=None):
    cands = [explicit] if explicit else []
    here = Path(__file__).resolve()
    cands += [Path.cwd() / MODEL_NAME, here.parents[2] / "models" / MODEL_NAME, here.parent / MODEL_NAME]
    for c in cands:
        if c and Path(c).is_file():
            return str(c)
    raise FileNotFoundError(f"{MODEL_NAME} not found (use --model PATH). Looked in: "
                            + ", ".join(str(c) for c in cands if c))


def open_camera(cv2, index, width, height, fps):
    def try_open(i):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2) if sys.platform.startswith("linux") else cv2.VideoCapture(i)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(10):                  # some cameras need a few frames to wake up
            ok, _f = cap.read()
            if ok:
                return cap
            time.sleep(0.05)
        cap.release()
        return None

    if index == "auto" or index is None:
        for i in range(6):
            cap = try_open(i)
            if cap is not None:
                return cap, i
        raise RuntimeError("no working camera found at indices 0-5 (use --camera N).")
    cap = try_open(int(index))
    if cap is None:
        raise RuntimeError(f"cannot open camera {index}.")
    return cap, int(index)


class HandTracker:
    def __init__(self, cfg, out=print):
        self.cfg, self.out = cfg, out
        self.cap = None
        self.detector = None
        self.cv2 = None
        self.mp = None
        self.preview = not cfg.no_preview
        self.last_key = -1
        self._last_ts = 0
        self._fail = 0
        self.last_ratio = None

    def open(self):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
        self.cv2, self.mp = cv2, mp
        model = find_model(self.cfg.model)
        opts = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model),
            running_mode=mp_vision.RunningMode.VIDEO,       # tracks between frames (faster, smoother)
            num_poses=1,
            min_pose_detection_confidence=self.cfg.cam_confidence,
            min_tracking_confidence=self.cfg.cam_confidence,
        )
        self.detector = mp_vision.PoseLandmarker.create_from_options(opts)
        self.cap, idx = open_camera(cv2, self.cfg.camera, self.cfg.cam_width, self.cfg.cam_height,
                                    self.cfg.cam_fps)
        self.out(f"Camera {idx} opened. Window keys: u r c p q (same as the terminal commands).")

    def step(self):
        """Grab + process one frame. Returns the hand ratio, or None if not visible."""
        cv2 = self.cv2
        ok, frame = self.cap.read()
        if not ok:
            self._fail += 1
            if self._fail > 30:
                raise RuntimeError("camera stopped delivering frames")
            time.sleep(0.01)
            return None
        self._fail = 0
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        ts = int(time.monotonic() * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1               # VIDEO mode needs strictly increasing timestamps
        self._last_ts = ts
        res = self.detector.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), ts)

        ratio, hand_xy, sh_xy = None, None, None
        if res.pose_landmarks:
            lms = res.pose_landmarks[0]
            if self.preview:
                for lm in lms:
                    cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 4, (255, 0, 0), cv2.FILLED)  # blue (BGR)
            r = extension_ratio(lms, w, h, self.cfg.hand)
            if r is not None:
                ratio, hand_xy, sh_xy = r
        self.last_ratio = ratio

        if self.preview:
            if hand_xy is not None:
                hp = (int(hand_xy[0] * w), int(hand_xy[1] * h))
                sp = (int(sh_xy[0] * w), int(sh_xy[1] * h))
                cv2.line(frame, sp, hp, (0, 255, 0), 2)
                cv2.circle(frame, hp, 7, (0, 0, 255), cv2.FILLED)                                   # red hand centre
            frame = cv2.flip(frame, 1)                                                              # mirror view
            txt = "ratio --" if ratio is None else f"ratio {ratio:.2f}"
            cv2.putText(frame, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            try:
                cv2.imshow("GoiBridge camera", frame)
                self.last_key = cv2.waitKey(1)
            except cv2.error:
                self.out("Preview window not available (headless OpenCV?); continuing without it.")
                self.preview = False
        return ratio

    def pressed(self):
        k, self.last_key = self.last_key, -1
        return chr(k & 0xFF) if k != -1 and 0 <= (k & 0xFF) < 128 else ""

    def close(self):
        try:
            if self.cap is not None:
                self.cap.release()
            if self.preview:
                self.cv2.destroyAllWindows()
            if self.detector is not None:
                self.detector.close()
        except Exception:
            pass


def main():
    """Standalone camera test:  python -m goi_bridge.vision [--camera N]"""
    import argparse
    from .config import Config
    ap = argparse.ArgumentParser(description="Show the hand/shoulder extension ratio.")
    ap.add_argument("--camera", default="auto")
    ap.add_argument("--hand", default="right", choices=["right", "left"])
    ap.add_argument("--model")
    a = ap.parse_args()
    cam = a.camera if a.camera == "auto" else int(a.camera)
    t = HandTracker(Config(camera=cam, hand=a.hand, model=a.model))
    t.open()
    try:
        last = 0
        while t.pressed() != "q":
            r = t.step()
            if time.monotonic() - last > 0.25:
                last = time.monotonic()
                print("ratio:", "--" if r is None else f"{r:.3f}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        t.close()


if __name__ == "__main__":
    main()
