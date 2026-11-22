# skeleton-tracking

Tracks your hands with the webcam (MediaPipe pose) and turns them into hammer commands for Getting Over It,
optionally corrected by the CodeCell. See the repository README for the whole setup.

## Install and run

Needs [uv](https://docs.astral.sh/uv/). The project uses **Python 3.11** (MediaPipe 0.10.9 has no wheels for
3.12+); uv downloads it for you.

```sh
cd skeleton-tracking
uv sync
uv run skeleton-tracking
```

The first `uv sync` after pulling a change to `pyproject.toml` re-resolves `uv.lock` and needs internet.

Supported: Linux x86_64/aarch64, Windows x64, macOS 13+ (Apple Silicon) / 14+ (Intel). Not supported: Windows on
ARM (no MediaPipe wheel).

## Choosing the camera

Pick the camera with a flag (works the same in every shell) or an environment variable; without either you are
asked for an index.

| Shell      | Command                                    |
| ---------- | ------------------------------------------ |
| any        | `uv run skeleton-tracking --camera 1`      |
| bash / zsh | `CAMERA=1 uv run skeleton-tracking`        |
| PowerShell | `$env:CAMERA=1; uv run skeleton-tracking`  |
| cmd        | `set CAMERA=1 && uv run skeleton-tracking` |

OpenCV's camera backend is chosen per OS: DirectShow then Media Foundation on Windows, AVFoundation on macOS,
OpenCV's default on Linux. Force one with `--backend {any,dshow,msmf,avfoundation,v4l2}` or `CAMERA_BACKEND`.

- **macOS:** the first run asks for camera access for the app you started it from (Terminal, VS Code, ...). If
  you said no, enable it under System Settings → Privacy & Security → Camera and start again. Index 0 can be an
  iPhone (Continuity Camera); try 1 or 2.
- **Windows:** if the camera is slow to open or gives no picture, try `--backend msmf` or `--backend dshow`, and
  close other apps that use the camera.

## The pose model

`pose_landmarker_full.task` is found next to the package, in this folder, in the repository root, or in the
current directory, so the program can be started from anywhere. Set `POSE_MODEL` to use a file somewhere else.
