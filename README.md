# gettingoverit-irl

This repository is client-side and controller-side code that let's you control the hammer in the game "Getting Over It" using a codecell and/or skeleton-tracking.

---

# Codecell

coming soon

---

# Skeleton-tracking

coming soon

---

# GoiBridge

To process the controller input, you need a [BepInEx 6](https://github.com/BepInEx/BepInEx) plugin "GoiBridge" for **Getting Over It with Bennett Foddy** (Linux, IL2CPP build) that exposes the game's cursor and hammer over a local UDP socket.

It works by reading and writing the game's own `PlayerControl` script (`fakeCursor`, `hj`, `sj`, `tip`) and has been tested on: Linux x86_64, Steam (snap), Unity **2020.3.6f1** (IL2CPP), BepInEx **6.0.0-be.789**.
Other Linux builds or Unity versions are untested; the Windows/Mono builds are not supported.

---

## 1. Install BepInEx

Before you can use the plugin you need the following:

- Getting Over It for Linux (the IL2CPP build: the game folder contains `GameAssembly.so`).
- .NET SDK 6.0 or newer (`dotnet --version`) to build the plugin.
- BepInEx 6 **bleeding edge**, "Unity IL2CPP linux x64", from <https://builds.bepinex.dev/projects/bepinex_be>.

When that has been installed you need to

1. Unpack the BepInEx archive into the game folder so that `BepInEx/core/BepInEx.Unity.IL2CPP.dll` exists next to `GettingOverIt.x86_64`. The archive also provides `run_bepinex.sh`, `libdoorstop.so` and `.doorstop_version`.
2. Make the launcher executable: `chmod +x run_bepinex.sh`.
3. In `run_bepinex.sh`, `executable_name` must be `GettingOverIt.x86_64` (it is by default for this game if you used the preconfigured script).
4. Start the game once to let BepInEx generate the interop assemblies (see "3. Running" below for the correct command).\
   The first start takes a while, may show a black window, and **may crash once** after generation. That is normal; start it again.\
   Afterwards `BepInEx/interop/` contains `Assembly-CSharp.dll`.

## 2. Build the plugin

From this folder "gettingoverit-irl/input-handler" run:

```sh
dotnet build -c Release /p:GameDir="/path/to/Getting Over It"
```

The default for `GameDir` is `$HOME/Getting Over It`, so you can omit it if your game is there.\
Copy the result into the plugins folder:

```sh
cp bin/Release/net6.0/GoiBridge.dll "/path/to/Getting Over It/BepInEx/plugins/"
```

The build references `BepInEx/interop/*.dll`, so step 2.4 has to be done first.

## 3. Running

> **Important: start the game with `setarch -R`.**
> On the tested setup the game segfaulted a few seconds after start (black window, then crash) whenever it ran through BepInEx normally, even with no plugins installed. Disabling address-space randomisation fixes it, if `./run_bepinex.sh` results in a crash, use:
>
> ```sh
> cd "/path/to/Getting Over It"
> setarch "$(uname -m)" -R ./run_bepinex.sh
> ```
>
> For Steam, set the launch options of the game to:
>
> ```
> setarch x86_64 -R ./run_bepinex.sh %command%
> ```

In the log (console, or `BepInEx/LogOutput.log`) you should see:

```
Loading [GoiBridge 0.2.0]
GoiBridge 0.2.0 loaded.
Found PlayerControl on 'Player'        <- appears once you are in the game
heartbeat: packets sent=...            <- every 5 seconds
```

**Keep the game window focused.** Unity pauses an unfocused game, so no packets are sent while it is in the background.
(`Application.runInBackground` cannot be set in this stripped build.)

<!-- If it still crashes, try in this order:

1. set `UnityLogListening = false` in `BepInEx/config/BepInEx.cfg` (section `[Logging]`);
2. another BepInEx bleeding-edge build (delete `BepInEx/interop` and `BepInEx/cache` first, then run once to regenerate);
3. run under gdb with the script below to get a real backtrace.

<details><summary>gdb launcher (for crash reports)</summary>

Copy `run_bepinex.sh` to `run_gdb.sh` and replace the final `exec "$executable_path" "$@"` (Linux branch) with:

```sh
PRE="$LD_PRELOAD"; unset LD_PRELOAD
exec gdb -batch -ex "handle SIGPWR nostop noprint pass" -ex "handle SIGXCPU nostop noprint pass" \
  -ex "set environment LD_PRELOAD $PRE" -ex run -ex bt --args "$executable_path" "$@"
```

`SIGPWR`/`SIGXCPU` are used by Unity's garbage collector and are not crashes.

</details> -->

## 4. Documentation

<details><summary>This section is documentation for how to run custom scripts to overide hammer target direction using the plugin.</summary>

## 4.1. How it works / notes for developers

- The plugin is a single injected `MonoBehaviour` (`Bridge`) with no Harmony patches. (A patch on `Input.mousePosition` was tried early on and is unnecessary: the game reads the mouse through Rewired into `PlayerControl.mouseInput`.)
- Relevant fields found in `Assembly-CSharp`: `PlayerControl.fakeCursor` (Transform), `fakeCursorRB`, `mw`, `tip`, `hj` (HingeJoint2D on `Hub`), `sj` (SliderJoint2D on `Slider`), `mouseInput`, `mouseSensitivity`.
- The game ships a **stripped** Unity: only Unity calls that the game itself uses exist at runtime. The plugin deliberately avoids e.g. `HingeJoint2D.anchor/limits/useMotor`, `Joint2D.connectedBody`, `Mathf`, `Object.FindObjectOfType<T>()`
  and `Application.runInBackground` setter. Before using another Unity API, check that its internal call appears in `strings GameAssembly.so | grep "UnityEngine.<Type>::<method>"`.
- The cursor is found once per second until `PlayerControl` exists (after the menu has loaded); there is no scene-change handling beyond that.

## 4.2. Protocol

Everything is UDP on `127.0.0.1`.

### Game -> Program: port 9877

One JSON object per rendered frame (can be well over 60 per second):

| key                    | meaning                                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `t`                    | `Time.unscaledTime` (seconds)                                                                                                   |
| `sw`, `sh`             | screen width / height in pixels                                                                                                 |
| `ov`                   | `true` while an override is active                                                                                              |
| `mx`, `my`             | **in-game cursor in screen pixels** (origin bottom-left)                                                                        |
| `fx`, `fy`             | in-game cursor in world coordinates                                                                                             |
| `wx`, `wy`             | `PlayerControl.mw`; always equals the cursor, read-only for practical purposes                                                  |
| `tx`, `ty`             | hammer tip, world                                                                                                               |
| `cx`, `cy`             | **hammer centre of rotation** (the `Hub` object, which carries the hinge), world                                                |
| `st`                   | slider joint translation; **distance pivot-to-tip = 2.25 + `st`** (measured range about 1.65 to 2.95)                           |
| `ha`                   | hinge joint angle in degrees. It is _not wrapped_ (it counts total turns) and is not a world angle. Do not use it for the angle |
| `bx`, `by`, `br`       | position and z-rotation (degrees) of the hinge object                                                                           |
| `rx`, `ry`, `vx`, `vy` | cursor rigidbody position / velocity (diagnostics)                                                                              |
| `mt`                   | override method in use (see `X`)                                                                                                |

Hammer angle in world space (0 degrees = +x, counter-clockwise): `atan2(ty - cy, tx - cx)`.
Keys other than `t`, `sw`, `sh`, `ov` are missing until you are in the game (before `PlayerControl` exists).

### Program -> game: port 9876

Plain text, one command per datagram:

| command     | effect                                                                                                            |
| ----------- | ----------------------------------------------------------------------------------------------------------------- |
| `M sx sy`   | move the cursor to screen pixels (origin bottom-left)                                                             |
| `W x y`     | move the cursor to world coordinates                                                                              |
| `H deg ext` | aim the hammer: world angle in degrees and distance from the pivot (world units, usable range about 1.65 to 2.95) |
| `X n`       | select the override method (default `3`, which is the only one you normally want)                                 |
| `off`       | release the override; the real mouse works again                                                                  |

While an override is active, send commands continuously (e.g. 50 Hz); the last command is re-applied every physics step.
Overriding works by moving the cursor; the game's own motors then drive the hammer towards it, so the hammer **trails the target slightly**
(about 2 to 3 degrees at 24 degrees per second, about 0.16 world units at 0.3 units per second of extension change). A static target is reached accurately.

### Reading correctly (important)

The game sends faster than most loops read. If you read one packet per iteration, the socket buffer fills with old packets and your
data becomes seconds out of date. Always take the **newest** packet. Python example:

```python
def latest(sock):
    sock.settimeout(5); last = json.loads(sock.recv(65536))
    sock.setblocking(False)
    try:
        while True: last = json.loads(sock.recv(65536))
    except BlockingIOError: pass
    return last
```

## 4.3 Configuration

`BepInEx/config/dev.goibridge.cfg` is created on first start:

| setting                        | default | meaning                                                                                                                                                                                       |
| ------------------------------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Network.SendPort`             | 9877    | UDP port state is sent to                                                                                                                                                                     |
| `Network.ListenPort`           | 9876    | UDP port commands are received on                                                                                                                                                             |
| `Pivot.LocalX`, `Pivot.LocalY` | 0, 0    | offset of the pivot in the hinge object's local space (the joint anchor is not readable in this build). The default was verified: the tip stays at constant distance from `Hub` when rotating |
| `Debug.DumpKey`                | F9      | writes `BepInEx/goi_dump.txt` with the current `PlayerControl` state                                                                                                                          |

</details>

<!-- ## 5. Troubleshooting

| symptom                                          | likely cause / fix                                                                                                   |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| Segfault a few seconds after start, black window | start with `setarch "$(uname -m)" -R` (section 4)                                                                    |
| `0 plugins to load`                              | `GoiBridge.dll` is not in `BepInEx/plugins/`, or you launched with `--doorstop-enabled false`                        |
| Build error about missing `BepInEx/interop`      | run the game once with BepInEx first (section 2.4), and check `GameDir`                                              |
| Client receives nothing                          | game not running through BepInEx, game window not focused, or something else holds UDP 9876 (`ss -ulpn \| grep 987`) |
| Values are seconds behind                        | you are reading old packets; take the newest (section 5)                                                             |
| Override has no effect                           | check `ov` is `true` in the packets and that you keep sending commands; use method `X 3`                             |
| Hammer does not reach the target                 | terrain is blocking it, or the target is outside the reach range 1.65 to 2.95                                        | -->
