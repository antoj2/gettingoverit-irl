# goi-overlay

Transparent, click-through overlay for drawing text/lines/shapes over a game.
C++17 / Qt 6 (Widgets + Network). Commands arrive as JSON over UDP on 127.0.0.1:45454.

## Build

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build
    ./build/goi-overlay --demo
    # if you have GNOME use instead:
    QT_QPA_PLATFORMTHEME=generic ./build/goi-overlay --demo

Linux/Wayland needs layer-shell-qt (Debian/Ubuntu: liblayershellqtinterface-dev, Fedora: layer-shell-qt-devel, Arch: layer-shell-qt). Without it the overlay still works, but can't sit above fullscreen windows.

Options: --port N, --screen N, --rect x,y,w,h (Win/mac/X11 only), --demo

## Protocol (one JSON object per line/datagram)

Coordinates are fractions: x/w of screen width, y/h/sizes/r/width of screen HEIGHT.
Colors: "#rrggbb" or "#aarrggbb". Items with the same "id" are replaced. "ttl" = ms until auto-removed.

    {"id":"hint","type":"text","x":0.5,"y":0.1,"size":0.04,"align":"center","color":"#ffd400","bg":"#a0000000","bold":true,"text":"Raise your arm\nslowly","ttl":4000}
    {"id":"a","type":"line","x1":.5,"y1":.5,"x2":.7,"y2":.3,"width":0.008,"color":"#ff3b30","arrow":true}
    {"id":"r","type":"rect","x":.02,"y":.85,"w":.25,"h":.1,"radius":.01,"color":"#fff","fill":"#80000000"}
    {"id":"c","type":"circle","x":.5,"y":.5,"r":.04,"color":"#30d158","fill":"#4030d158"}
    {"cmd":"remove","id":"a"}  {"cmd":"clear"}  {"cmd":"hide"}  {"cmd":"show"}
    {"cmd":"batch","items":[ ... ]}

Test: `echo '{"type":"text","x":.5,"y":.5,"text":"hi","align":"center"}' | nc -u -w0 127.0.0.1 45454`

## Platform notes

- Windows: topmost + WS_EX_TRANSPARENT, re-asserted every second. Works over windowed and borderless-fullscreen. It CANNOT draw over _exclusive_ fullscreen (DirectX owns the screen) - run the game in windowed/borderless mode.
- macOS: window level above the menu bar, joins all Spaces, accessory app, so it appears over native fullscreen games. Grant nothing special; no permissions needed.
- Linux/Wayland: uses wlr-layer-shell, OVERLAY layer, empty input region. Works on KDE Plasma, Sway, Hyprland, other wlroots compositors.
