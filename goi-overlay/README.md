# goi-overlay

Transparent, click-through overlay for drawing text/lines/shapes over a game.
C++17 / Qt 6 (Widgets + Network). Commands arrive as JSON over UDP on 127.0.0.1:45454.

## Build

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build
    # run:
    ./build/goi-overlay --demo

Linux/Wayland needs layer-shell-qt (Debian/Ubuntu: liblayershellqtinterface-dev,
Fedora: layer-shell-qt-devel, Arch: layer-shell-qt). Without it the overlay still
works, but can't sit above fullscreen windows.

Options: --port N, --event-port N, --screen N (screens are listed at startup), --rect x,y,w,h (Win/mac/X11 only), --demo

## Protocol (one JSON object per line/datagram)

Coordinates are fractions: x/w of screen width, y/h/sizes/r/width of screen HEIGHT.
Colors: "#rrggbb" or "#aarrggbb". Items with the same "id" are replaced. "ttl" = ms until auto-removed.

    {"id":"hint","type":"text","x":0.5,"y":0.1,"size":0.04,"align":"center","color":"#ffd400","bg":"#a0000000","bold":true,"text":"Raise your arm\nslowly","ttl":4000}
    {"id":"a","type":"line","x1":.5,"y1":.5,"x2":.7,"y2":.3,"width":0.008,"color":"#ff3b30","arrow":true}
    {"id":"r","type":"rect","x":.02,"y":.85,"w":.25,"h":.1,"radius":.01,"color":"#fff","fill":"#80000000"}
    {"id":"c","type":"circle","x":.5,"y":.5,"r":.04,"color":"#30d158","fill":"#4030d158"}
    {"cmd":"remove","id":"a"}  {"cmd":"clear"}  {"cmd":"hide"}  {"cmd":"show"}
    {"cmd":"batch","items":[ ... ]}
    {"cmd":"screen","index":1}               move to another monitor (Qt app: index as listed at startup)
    {"cmd":"interactive","enabled":true}     overlay receives mouse input instead of passing it through

While interactive, mouse events are sent as JSON lines to UDP 127.0.0.1:45455:
{"event":"mouse","type":"press|release|move","button":"left|right|middle|none","x":0.5,"y":0.3}
Listen with: nc -ul 45455 (x by width, y by height, same fractions as drawing)

Test: `echo '{"type":"text","x":.5,"y":.5,"text":"hi","align":"center"}' | nc -u -w0 127.0.0.1 45454`

## Platform notes

- Windows: topmost + WS_EX_TRANSPARENT, re-asserted every second. Works over windowed
  and borderless-fullscreen. It CANNOT draw over _exclusive_ fullscreen (DirectX owns
  the screen) - run the game in windowed/borderless mode.
- macOS: window level above the menu bar, joins all Spaces, accessory app, so it appears
  over native fullscreen games. Grant nothing special; no permissions needed.
- Linux/Wayland: uses wlr-layer-shell, OVERLAY layer, empty input region. Works on
  KDE Plasma, Sway, Hyprland, other wlroots compositors. On GNOME (Mutter has no
  layer-shell) the Qt app is just a normal window that is hidden whenever the game has
  focus - use the GNOME Shell extension below instead.

## GNOME (Wayland)

Use the bundled Shell extension instead of the Qt app. Same protocol, same ports;
colors must be #rrggbb / #aarrggbb. Supports GNOME 45-50.

    cp -r gnome-extension/goi-overlay@local ~/.local/share/gnome-shell/extensions/
    # log out and back in (Wayland can't reload the shell), then:
    gnome-extensions enable goi-overlay@local
    gnome-extensions info goi-overlay@local     # State must be ENABLED, not OUT OF DATE
    nc -u -w0 127.0.0.1 45454 < demo.jsonl
    # debugging: journalctl -f -o cat /usr/bin/gnome-shell

Monitor index = GNOME's monitor order (0 is usually primary). Don't run the Qt app and the
extension at the same time (same port).
