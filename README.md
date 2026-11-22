# Setup guide: Linux, Windows and macOS

This is the start-to-finish guide. Each component also has its own README with the details; they are linked
where they matter.

## What runs where

| Component                               | What it does                                                                      | Needed?  |
| --------------------------------------- | --------------------------------------------------------------------------------- | -------- |
| **GoiBridge** (`input-handler/`)        | BepInEx plugin inside the game: reads and moves the hammer                        | Yes      |
| **skeleton-tracking** (Python)          | Webcam hand tracking, drives the plugin, draws hints in the overlay               | Yes      |
| **goi-overlay** (`goi-overlay/`)        | Transparent window with hints on top of the game                                  | Optional |
| **CodeCell** (`VinkelFraJordCodecell/`) | Tilt sensor held in the hand, sent over Wi-Fi, disambiguates the hammer direction | Optional |

The pieces talk over local UDP: tracker ⇄ game on ports 9876/9877, tracker → overlay on 45454, CodeCell →
tracker on 4210.

## Status

| OS                                 | State                                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------------ |
| Linux (GNOME/Wayland, IL2CPP game) | Tested.                                                                                    |
| Windows                            | **Not tested yet.** Written from the code and the platform's documented behaviour.         |
| macOS                              | **Not tested yet.** Same caveat, and the game build and BepInEx launch are open questions. |

Expect to hit a snag or two on Windows and macOS. That is why every step below ends with a way to check that
piece _on its own_ before you connect it to the next one.

## The game has two flavours

Look in the game folder:

| You see                                                             | The game is | Plugin build             | BepInEx |
| ------------------------------------------------------------------- | ----------- | ------------------------ | ------- |
| `GameAssembly.so` / `.dll` / `.dylib`                               | IL2CPP      | default (`dotnet build`) | 6       |
| `GettingOverIt_Data/Managed/Assembly-CSharp.dll`, no `GameAssembly` | Mono        | `-p:Backend=Mono`        | 5       |

Linux (the tested setup) is IL2CPP. The Steam build on Windows is Mono; on macOS check.

## Linux

1. Game + BepInEx 6 + plugin: [README.md → GoiBridge](README.md#goibridge) (remember `setarch -R`).
2. Overlay: [goi-overlay/README.md](goi-overlay/README.md) (on GNOME use the bundled Shell extension instead).
3. Tracker: `cd skeleton-tracking && uv sync && uv run skeleton-tracking`
4. CodeCell: [VinkelFraJordCodecell/README.md](VinkelFraJordCodecell/README.md), including the `nmcli` power-saving
   command.

## Windows

You need: Git, [uv](https://docs.astral.sh/uv/), the .NET SDK (6 or newer), CMake 3.21+, a C++ compiler
(Visual Studio Build Tools with the "Desktop development with C++" workload) and Qt 6.5+ (MSVC 64-bit kit, from
the [Qt installer](https://www.qt.io/download-qt-installer-oss)). Most of these are one `winget install` each
(`astral-sh.uv`, `Kitware.CMake`, `Microsoft.DotNet.SDK.8`, `Git.Git`). Commands below are for PowerShell.

```powershell
git clone https://github.com/antoj2/gettingoverit-irl
cd gettingoverit-irl
```

**1. Game plugin (Mono build, BepInEx 5)**

1. Download `BepInEx_win_x64_5.4.*.zip` from <https://github.com/BepInEx/BepInEx/releases> and extract it into
   the game folder (next to the game's `.exe`). No launch options are needed on Windows.
2. Start the game once and quit. `BepInEx\LogOutput.log` now exists.
3. Build and install the plugin:
   ```powershell
   cd input-handler
   dotnet build -c Release -p:Backend=Mono
   copy bin\Release\net472\GoiBridge.dll "C:\Program Files (x86)\Steam\steamapps\common\Getting Over It\BepInEx\plugins\"
   ```
   If the game is not in the default Steam library, add `"-p:GameDir=D:\Games\Getting Over It"` to the build.
4. Start the game, get into the game (not just the menu). In `BepInEx\LogOutput.log` you should see
   `GoiBridge 0.2.0 loaded.` and, once in the game, `Found PlayerControl on 'Player'`.

Problems: [input-handler/README.md](input-handler/README.md) sections 4D and 8.

**2. Overlay (optional)**

```powershell
cd ..\goi-overlay
cmake -S . -B build -DCMAKE_PREFIX_PATH="C:\Qt\6.8.0\msvc2022_64"
cmake --build build --config Release
build\Release\goi-overlay.exe --demo
```

Use your own Qt path. `--demo` should show some text and shapes on screen with clicks passing through. Run the game
in **windowed or borderless** mode: the overlay cannot draw over exclusive fullscreen.

**3. Tracker**

```powershell
cd ..\skeleton-tracking
uv sync
uv run skeleton-tracking --camera 0
```

`uv` downloads Python 3.11 by itself (MediaPipe has no wheels for newer Pythons). Windows on ARM is not supported.
If the camera is slow to open or gives no picture, try `--backend msmf` or `--backend dshow`. Without
`--camera` you are asked for an index. Success looks like `Kamera 0 åbnet (dshow).`

**4. CodeCell (optional)**

Flash `VinkelFraJordCodecell.ino` (again, if you flashed an older version: the computer is now found with a
`hello` packet). Join the `SchimonsTuffESP32v2` Wi-Fi network, then allow Python through the firewall, turn off
Wi-Fi power saving, and check with `python ComputerReciever.py`. All of it, with the exact commands, is in
[VinkelFraJordCodecell/README.md](VinkelFraJordCodecell/README.md#windows). The most common problem is the
firewall: the CodeCell network is classed as _Public_, which drops incoming UDP silently.

## macOS

You need: [Homebrew](https://brew.sh), then `brew install uv cmake qt` and the .NET SDK
(`brew install --cask dotnet-sdk`). Xcode's command line tools provide the compiler (`xcode-select --install`).
The tracker needs macOS 13+ on Apple Silicon or 14+ on Intel.

```sh
git clone https://github.com/antoj2/gettingoverit-irl
cd gettingoverit-irl
```

**1. Game plugin**

First check which flavour the game is (table above): look inside the app bundle (right-click the game in Steam →
Manage → Browse local files, then _Show Package Contents_).

- **Mono:** BepInEx 5 (`BepInEx_macos_x64_5.4.*.zip`), plugin built with `-p:Backend=Mono` (the build reads the
  game's `Managed` folder from `Getting Over It.app/Contents/Resources/Data/Managed`; if yours is elsewhere
  pass `-p:ManagedDir=...`). Steps and the macOS launch (`run_bepinex.sh`) are in
  [input-handler/README.md](input-handler/README.md) sections 4D and 4B.
- **IL2CPP:** follow section 4B of the same file (experimental).

Open questions, not yet answered by anyone: macOS may block the injected library (Gatekeeper, code signing: see
**Privacy & Security → Open Anyway**), and on Apple Silicon the game and BepInEx may have to run under Rosetta.

**2. Overlay (optional)**

```sh
cd goi-overlay
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$(brew --prefix qt)" \
      -DCMAKE_CXX_COMPILER=/usr/bin/clang++ -DCMAKE_OBJCXX_COMPILER=/usr/bin/clang++
cmake --build build
./build/goi-overlay --demo
```

Use Apple's compiler as shown (Homebrew's LLVM clang can break Qt/Objective-C++ builds). Native fullscreen games
are fine; no permissions are needed.

**3. Tracker**

```sh
cd ../skeleton-tracking
uv sync
uv run skeleton-tracking --camera 0
```

The first run asks for **camera access** for the app you started it from (Terminal, VS Code, ...). If you
refused, enable it in System Settings → Privacy & Security → Camera and start again. Camera 0 can be an iPhone
(Continuity Camera); try 1 or 2.

**4. CodeCell (optional)**

Same as on Windows, but with the macOS notes in [VinkelFraJordCodecell/README.md](VinkelFraJordCodecell/README.md#macos):
answer **Allow** to the firewall prompt, and allow Terminal under Privacy & Security → **Local Network**
(macOS 15 and later).

## Running it

1. **CodeCell** (if used): power it and join its Wi-Fi. You have no internet on that adapter while connected.
2. **Game**, started the way your OS needs it (Linux: through `run_bepinex.sh`; Windows: normally). Get into the
   game, windowed or borderless.
3. **Overlay** (if used): `goi-overlay` (add `--screen N` if the game is on another monitor).
4. **Tracker**: `uv run skeleton-tracking --camera N` from `skeleton-tracking/`. At startup it prints which
   camera it opened and whether it found the CodeCell (`Bruger CodeCell til at korrigere vinkler.` or
   `Ingen CodeCell fundet, kører uden den.`). Stop it with Ctrl+C.

## If something doesn't work: test one piece at a time

| Piece    | Test on its own                                          | Healthy result                                         | If not                                                              |
| -------- | -------------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------- |
| Plugin   | Look at `BepInEx/LogOutput.log`                          | `GoiBridge 0.2.0 loaded.`, later `Found PlayerControl` | Wrong plugin build for the game flavour; BepInEx not loading        |
| Overlay  | `goi-overlay --demo`                                     | Text and shapes over the screen, mouse passes through  | Exclusive fullscreen (Windows); wrong `--screen`                    |
| CodeCell | `python ComputerReciever.py` in `VinkelFraJordCodecell/` | About 20 `Packets/s`, `Missing: 0`                     | Not on the CodeCell's Wi-Fi; firewall; macOS Local Network          |
| Camera   | `uv run skeleton-tracking --camera 0`                    | `Kamera 0 åbnet (...)`                                 | Camera permission (macOS/Windows); try another index or `--backend` |
| Whole    | Tracker running, game open                               | Hammer follows your hands                              | Plugin not in-game yet (it needs the game scene, not the menu)      |

Per-component details:
[input-handler/README.md](input-handler/README.md) ·
[goi-overlay/README.md](goi-overlay/README.md) ·
[skeleton-tracking/README.md](skeleton-tracking/README.md) ·
[VinkelFraJordCodecell/README.md](VinkelFraJordCodecell/README.md)

If you try this on Windows or macOS, please note what worked and what didn't. Most of the open questions above
can only be answered by someone running it.
