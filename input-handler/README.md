# GoiBridge

A small plugin for **Getting Over It with Bennett Foddy** that lets another program on the same computer
control the hammer and some of the game's behaviour.

The game sends its state (hammer position, hinge angle, …) to your program 60+ times a second, and your
program sends short text commands back ("put the hammer at 90° and 3 units long", "pause the game", …).
It was built to make a custom physical game controller, but it works for any program that can send UDP packets.

Everything happens on your own computer (`127.0.0.1`). Nothing goes over the internet.

---

## 1. Platform support

| OS                 | Status                                                                                                                                                                                              |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Linux** (Ubuntu) | Tested, works.                                                                                                                                                                                      |
| **Windows**        | **Not tested by the author**, but BepInEx officially supports IL2CPP games on Windows, so it should work. Follow section 4A.                                                                        |
| **macOS**          | **Experimental, likely difficult.** BepInEx 6 has no official macOS support for IL2CPP games. There is an unofficial community build, and it has never been tried with this plugin. See section 4B. |

---

## 2. What you need

1. **Getting Over It** on Steam, installed.
2. **BepInEx 6, IL2CPP "bleeding edge" build** (section 4 tells you which file to download).
3. **`GoiBridge.dll`**: the plugin itself.
4. **Python 3** (only for the example/controller scripts, not for the plugin).

---

## 3. First, check that your game is an IL2CPP game

This plugin only works with the IL2CPP version of the game.

1. In Steam: right-click the game → **Manage** → **Browse local files**. This is the **game folder**.
2. Look inside it:
   - **Windows:** you should see `GameAssembly.dll` and a folder called `GettingOverIt_Data` (or similar) containing an `il2cpp_data` folder. That means IL2CPP, which is good.
   - If instead you see a `Managed` folder inside the `_Data` folder and no `GameAssembly.dll`, the game is the **Mono** version and this plugin will **not** work.
3. Also note whether the game exe is 32-bit or 64-bit. Almost certainly 64-bit; if the game doesn't start with the 64-bit BepInEx, try the `x86` one.

---

## 4. Installation

### 4A. Windows

1. **Download BepInEx.** Go to the BepInEx "bleeding edge" builds page (search for _"BepInEx bleeding edge builds"_, it's hosted at `builds.bepinex.dev`) and download the newest build of
   **`BepInEx-Unity.IL2CPP-win-x64`** (a `.zip`).
   Make sure it's _Unity.IL2CPP_, not _Unity.Mono_ or _NET.Framework_.
2. **Extract the zip into the game folder** (the folder that contains the game's `.exe`, found in section 3).
   After this you should have, next to the exe: `BepInEx/` folder, `winhttp.dll`, `doorstop_config.ini` and `.doorstop_version` (names may differ slightly).
3. **Start the game once from Steam**, then let it fully reach the main menu.
   The first start takes a **few minutes** (BepInEx is generating files). A window may flash or stay black for a while. That's normal, so don't close it.
   Afterwards, quit the game.
   - If `BepInEx/interop/` (with lots of `.dll` files) now exists, BepInEx works.
4. **Install the plugin.** Copy `GoiBridge.dll` into:
   ```
   <game folder>\BepInEx\plugins\
   ```
   (create the `plugins` folder if it doesn't exist).
5. **Start the game from Steam as normal.** No Steam launch options are needed on Windows.
6. Check section 5 to confirm it works.

### 4B. macOS (experimental)

BepInEx's official builds do not support IL2CPP games on macOS. A third-party macOS port of BepInEx IL2CPP exists in the
Football Manager modding community (search for _"BepInEx Unity IL2CPP macOS"_). It was made for a different game and I haven't verified it with Getting Over It, so treat everything here as "try it and see".

General shape of what you'd do:

1. Confirm the game is IL2CPP (section 3; on macOS look inside the `.app` bundle, _Show Package Contents_, for an IL2CPP `GameAssembly` library and `il2cpp_data`).
2. Download the community macOS BepInEx IL2CPP build matching your Mac (Intel/x64 or Apple Silicon/arm64) and macOS version, and follow the `README.txt` that comes inside its zip. It gives you a `run_bepinex.sh`.
3. Put the extracted files in the game folder (Steam → Manage → Browse local files).
4. In Steam, set the game's **Launch Options** to the full path of `run_bepinex.sh` followed by `%command%`, for example:
   ```
   "/Users/<you>/Library/Application Support/Steam/steamapps/common/Getting Over It/run_bepinex.sh" %command%
   ```
5. macOS will probably block an unsigned library (`libdoorstop.dylib`) the first time. Open **System Settings → Privacy & Security** and click **Open Anyway** next to it, then start the game again.
6. Start the game once, wait for `BepInEx/interop/` to appear, quit, copy `GoiBridge.dll` into `BepInEx/plugins/`, and start the game again.
7. On macOS there's no console window; check `BepInEx/LogOutput.log` instead (section 5).

If this doesn't work, there's nothing in this plugin you can change to fix it; the problem will be in getting BepInEx to load at all.

### 4C. Linux (the setup the author used)

1. Download **`BepInEx-Unity.IL2CPP-linux-x64`** (bleeding edge) and extract it into the game folder.
2. In Steam, set the game's launch options to:
   ```
   setarch x86_64 -R ./run_bepinex.sh %command%
   ```
3. Start the game once, wait for `BepInEx/interop/` to appear, quit, copy `GoiBridge.dll` to `BepInEx/plugins/`, start again.

---

## 5. Check that it works

Start the game. Then:

1. Open `BepInEx/LogOutput.log` (or `LogOutput.txt`) in the game folder. You should see:
   ```
   GoiBridge 0.2.0 loaded.
   ```
   and, every few seconds, a line starting with `heartbeat: packets sent=`.
2. Load into the game (past the main menu, so you can see the hammer). You should then see `Found PlayerControl on '...'`.
3. In a terminal, in the folder with the scripts, run:
   ```
   python test_client.py
   ```
   (`python3` on Linux/macOS). You should see a stream of lines of data. Try `python test_client.py --circle` and the in-game cursor should move in a circle.

**Nothing in the log at all** → BepInEx isn't loading (go back to the install steps; check the plugin is in `BepInEx/plugins`).
**Log says "loaded" but no data** → see Troubleshooting (section 8).

---

## 6. Using it from your own program

Communication is plain **UDP on `127.0.0.1`** (the same computer):

| Direction             | Port     | Content                                                           |
| --------------------- | -------- | ----------------------------------------------------------------- |
| Plugin → your program | **9877** | A JSON packet every frame (your program must listen/bind on 9877) |
| Your program → plugin | **9876** | One text command per UDP packet                                   |

The ports can be changed in the plugin's config file (section 7).

### Commands

Commands are plain text. Numbers use `.` as decimal separator.

| Command                         | What it does                                                                                                                                       |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `H <deg> <ext>`                 | Put the hammer at an angle (degrees, 0 = pointing right, counter-clockwise) and a distance from the hinge pivot (in world units, e.g. `H 90 3.0`). |
| `M <x> <y>`                     | Put the in-game cursor at a screen pixel (origin bottom-left).                                                                                     |
| `W <x> <y>`                     | Put the in-game cursor at a world position.                                                                                                        |
| `off`                           | Stop overriding. The game's own mouse input takes over again.                                                                                      |
| `X <n>`                         | Choose _how_ the override is applied (0–5). Default and recommended is `3`. Only change this if you're experimenting.                              |
| `pause 1` / `pause 0`           | Freeze / unfreeze the game silently (no menu is shown).                                                                                            |
| `pause_menu 1` / `pause_menu 0` | Open / close the game's own pause menu, like pressing Esc.                                                                                         |
| `set block_pause 1` / `0`       | `1`: the player **cannot** pause (Esc does nothing). `0` (default): the player can pause normally.                                                 |

`1`/`0` can also be written `true`/`false` or `on`/`off`.

Good to know:

- **Settings are not remembered.** Everything is reset when the game closes, so your program should send its settings (e.g. `set block_pause 1`) every time it starts.
- `pause` and `pause_menu` still work when `block_pause` is on; the controller can always pause, the player can't.
- With `block_pause 1` and the menu opened by `pause_menu 1`, the player can't close it, so only `pause_menu 0` does.
- While the game is paused it doesn't move, so hammer commands only take effect once it's unpaused. Avoid pausing in the middle of a swing, or the hammer will jump when the game resumes.

### The state packet

Every packet is a JSON object. Some fields only appear once you're in the game (marked _in game_).

| Field                  | Meaning                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `t`                    | Time in seconds since the game started                                  |
| `sw`, `sh`             | Screen/window width and height in pixels                                |
| `ov`                   | `true` if a command (`H`/`M`/`W`) is currently overriding the cursor    |
| `bp`                   | The `block_pause` setting                                               |
| `pz`                   | `true` if the controller has paused the game (`pause 1`)                |
| `pm`                   | `true` if the controller opened the pause menu (`pause_menu 1`)         |
| `ts`                   | The game's time scale (`0` = frozen, `1` = normal)                      |
| `mp`                   | _in game._ The game's own "menu is open" flag                           |
| `fo`                   | Is the game window focused?                                             |
| `bg`                   | Does the game keep running when not focused? (the plugin turns this on) |
| `fx`, `fy`             | _in game._ In-game cursor position, world units                         |
| `mx`, `my`             | _in game._ In-game cursor position, screen pixels                       |
| `tx`, `ty`             | _in game._ Hammer tip, world units                                      |
| `cx`, `cy`             | _in game._ Centre of rotation (the hinge pivot), world units            |
| `ha`                   | _in game._ Hinge angle, degrees                                         |
| `st`                   | _in game._ Hammer extension (slider)                                    |
| `bx`, `by`, `br`       | _in game._ Position and rotation of the object carrying the hinge       |
| `rx`, `ry`, `vx`, `vy` | _in game._ Cursor rigidbody position and velocity                       |
| `wx`, `wy`             | _in game._ The game's own cursor target                                 |
| `mt`                   | The currently selected `X` method                                       |

### Minimal Python example

```python
import socket, json, time

rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.bind(("127.0.0.1", 9877))                 # state comes in here
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send = lambda msg: tx.sendto(msg.encode(), ("127.0.0.1", 9876))

# wait until we are in the game
while "fx" not in json.loads(rx.recv(65536)):
    pass

send("X 3")
send("set block_pause 1")                    # the player can't pause
for deg in range(0, 360, 2):
    send(f"H {deg} 3.0")                     # sweep the hammer around
    time.sleep(0.03)
send("off")
send("set block_pause 0")
```

`Sweep.py` in this folder is a longer working example, and `test_client.py` prints the state.

**Tip for macOS/Windows:** use `sw`/`sh` from the packet instead of assuming your screen resolution, because high-DPI
screens (Retina) report different pixel sizes than you might expect.

---

## 7. Configuration file

After the first run with the plugin, a config file appears at `BepInEx/config/dev.goibridge.cfg`:

| Setting                 | Default | Meaning                                                                              |
| ----------------------- | ------- | ------------------------------------------------------------------------------------ |
| `Network → SendPort`    | `9877`  | Where the plugin sends state to                                                      |
| `Network → ListenPort`  | `9876`  | Where the plugin listens for commands                                                |
| `Debug → DumpKey`       | `F9`    | Press this in-game to write `BepInEx/goi_dump.txt` with debugging info               |
| `Pivot → LocalX/LocalY` | `0`     | Offset of the hinge pivot (only change if the hammer rotates around the wrong point) |

Edit it with the game closed.

---

## 8. Troubleshooting

| Problem                                                                     | Likely cause / fix                                                                                                                                                                   |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| No `BepInEx/LogOutput.log` after starting                                   | BepInEx isn't loading. Windows: check the zip was extracted **next to the game exe**, and that antivirus didn't quarantine `winhttp.dll`. Mac/Linux: check the launch option is set. |
| The game starts but nothing happens for minutes the first time              | Normal. BepInEx generates files on the first run. Wait.                                                                                                                              |
| No `GoiBridge 0.2.0 loaded.` line                                           | `GoiBridge.dll` isn't in `BepInEx/plugins/`, or it was built against a different game/BepInEx version. See section 9 to rebuild it.                                                  |
| Errors mentioning `TypeLoadException` / `MissingMethodException` in the log | The plugin was built against different game files than yours. Rebuild it (section 9) on your machine.                                                                                |
| Loaded, but your program gets no data                                       | Make sure your program **binds to port 9877** before/while the game runs, and that nothing else is using 9876/9877 (another copy of your script still running is the usual culprit). |
| Data arrives but there's no `fx`/`fy`                                       | You aren't in the game yet. Load past the main menu until you can see the hammer.                                                                                                    |
| Esc still pauses the game                                                   | Your program hasn't sent `set block_pause 1` since the game started (settings reset every launch).                                                                                   |
| The game freezes and Esc doesn't unpause                                    | A previous `pause 1` is still active. Send `pause 0`.                                                                                                                                |
| Game window stalls when you click another window                            | Shouldn't happen with this version (it sets _run in background_). Check `fo`/`bg` in the state packet and report it.                                                                 |

---

## 9. Building the plugin yourself (optional)

You only need this if the supplied `GoiBridge.dll` doesn't load.

1. Install the **.NET SDK** (version 6 or newer).
2. Run the game once with BepInEx installed (section 4) so that `BepInEx/interop/` exists.
3. In the folder containing `GoiBridge.csproj` and `Plugin.cs`, run:
   ```
   dotnet build -c Release -p:GameDir="<your game folder>"
   ```
   For example on Windows: `-p:GameDir="C:\Program Files (x86)\Steam\steamapps\common\Getting Over It"`.
4. The result is `bin/Release/net6.0/GoiBridge.dll`. Copy it to `BepInEx/plugins/`.

The project downloads the BepInEx libraries from `nuget.bepinex.dev` while building, so you need an internet connection for this step.
