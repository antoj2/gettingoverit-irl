# Getting Over It stick controller

```
 CodeCell (ESP32) --Wi-Fi/UDP 4210--> goi_bridge (PC) --UDP 9876 "H angle ext"--> GoiBridge plugin (in game)
   orientation only                     angle from IMU        <--UDP 9877 game state--
                                        extension from camera (optional)
```

* **Angle** of the hammer = where the stick points, from the IMU's absolute orientation. No integration, no drift.
* **Extension** (how far the hammer reaches) = optional camera: hand-to-shoulder distance / shoulder width.
  Without the camera it is a constant (`--ext-mult`, default 1.0 x the hammer's rest length).

## Layout
| Path | What |
|---|---|
| `firmware/MotionSensorESP32/` | New CodeCell firmware (streams the quaternion, 40-byte binary packets) |
| `src/goi_bridge/` | The PC bridge (`goi-bridge`, `goi-monitor`, `sim`, `vision`) |
| `plugin/` | `Plugin.cs` (+watchdog, NaN guards, optional LateUpdate), portable `.csproj`, your test scripts |
| `models/` | `pose_landmarker_full.task` |
| `tests/test_all.py` | Unit + end-to-end tests with a fake ESP32 and fake game (no hardware needed) |

## 1. Flash the firmware
Open `MotionSensorESP32.ino` in the Arduino IDE (CodeCell library installed), flash. SSID/password are the same as before.
The CodeCell is the Wi-Fi access point and now *broadcasts* to `192.168.4.255`, so the PC's IP no longer matters.
If packets never arrive, set `USE_BROADCAST 0` (sends to `192.168.4.2`).

## 2. Connect the PC (Linux)
```bash
nmcli device wifi connect SchimonsTuffESP32 password VerityErTuff      # or use your network applet
sudo ufw allow 4210/udp                                                  # only if ufw is active
uv sync                      # core only;   uv sync --extra camera   adds MediaPipe/OpenCV (use Python 3.11)
uv run goi-monitor           # hardware check: ~100 Hz, lost ~0
```
Keep internet on Ethernet while the Wi-Fi is on the CodeCell.

## 3. Build / install the plugin
```bash
cd plugin
dotnet build -c Release -p:GameDir="$HOME/Getting Over It"      # Windows: -p:GameDir="C:\...\Getting Over It"
# copy bin/Release/net6.0/GoiBridge.dll to <game>/BepInEx/plugins/
```
New config (BepInEx/config/dev.goibridge.cfg): `WatchdogMs` (500) releases the cursor if the bridge stops sending;
`ApplyInLateUpdate` (false) - try `true` if the cursor fights the game. Also set `Pivot` LocalX/LocalY as before.

## 4. Run
Start the game and load into a level, hands off the mouse, then:
```bash
uv run goi-bridge -v                          # IMU only
uv run goi-bridge -v --camera auto            # + camera extension (add --hand left if left-handed)
```
Calibration wizard (4 s countdown each, stick must be still):
1. stick **straight up** - measures how the sensor sits on the stick (saved; redo with `--recalibrate` or `u`)
2. stick **to your right**, parallel to the screen - defines "screen right" (asked every session, or `r` to recenter)
3. camera only: hand **tucked in**, then arm **fully extended** (saved; `c` to redo)

Commands (type in the terminal + Enter, or press the key in the camera window): `r` recenter, `u` redo vertical,
`c` camera range, `e` re-measure hammer length, `p` pause (mouse works), `s` status, `q` quit.
Global hotkeys while the game is focused: `uv sync --extra hotkeys`, then `--hotkeys`: **F8** recenter, **F9** pause, **F10** quit
(X11 only; not Wayland).

## Try it without hardware
`uv run python tests/test_all.py` runs the whole pipeline against a fake ESP32 and a fake game (scripted calibration,
a 300 deg swing, stick unplugged -> cursor released, camera extension). `uv run python -m goi_bridge.sim imu|game`
start the fakes by hand. The camera and the real hardware are the parts only you can test.

## Tuning / troubleshooting
* Hammer goes up when stick goes down -> `--flip-vertical`. Constant angle error -> `--angle-offset DEG` or `r`.
* Too laggy -> raise `--min-cutoff` (e.g. 6). Too jittery -> lower it (e.g. 1.5).
* Extension too small/large -> `--ext-min`, `--ext-max` (multiples of the rest length) or `--r0`.
* Heading drifts over minutes (magnetic interference) -> press `r`/F8 occasionally.
* Camera not found -> `--camera N`; Linux permissions: add yourself to the `video` group.
* "cannot bind 9877" -> close `test_client.py` / `sweep.py` (only one listener on that port).
