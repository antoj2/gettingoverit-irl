# CodeCell (VinkelFraJordCodecell)

The CodeCell measures its tilt relative to gravity and sends it to the computer over Wi-Fi (UDP, port 4210,
about 20 packets/s). It is its own Wi-Fi **access point** (`SchimonsTuffESP32v2`), so the computer has to join
that network. The network has no internet.

Packet format: `sequence,millis,tilt,battery` (older firmware: `tilt,battery`; the controller understands both).

## How the computer is found

The CodeCell cannot know the computer's address in advance. Every time the controller (or
`ComputerReciever.py`) runs it sends a small `hello` packet to the CodeCell (`192.168.4.1`, once a second), and
the CodeCell sends its data to whoever said hello last. This works whatever address the CodeCell's DHCP server
gave the computer. It is unicast, on purpose: broadcast frames are held back until the access point's next DTIM
beacon and sent at the lowest rate, which would add latency.

Until the first hello arrives the firmware sends to `192.168.4.2`, so older Python versions that don't say hello
still work as long as the computer actually has that address.

**After changing the firmware, flash it again** (the hello handling is in `VinkelFraJordCodecell.ino`).

## Test the connection

1. Power the CodeCell and join the `SchimonsTuffESP32v2` Wi-Fi network on the computer.
2. `python ComputerReciever.py` (`python3` on Linux/macOS). You should see a line per second like
   `Packets/s: 20 | Avg: 50.0 ms | ... | Missing: 0`. After 3 s without packets it prints what to check.

## Windows

- **Firewall.** The first time Python listens on port 4210 Windows asks whether to allow it. If it was dismissed,
  or the CodeCell's network was classed as _Public_ (Windows does that for networks without internet), incoming
  UDP is dropped silently. Either set the network to **Private** (Settings → Network & internet → Wi-Fi →
  the network → Network profile type), or add a rule in an _administrator_ Command Prompt:
  ```
  netsh advfirewall firewall add rule name="Getting Over It CodeCell" dir=in action=allow protocol=UDP localport=4210 profile=any
  ```
- **Wi-Fi power saving** adds latency. In an administrator Command Prompt:
  ```
  powercfg /setacvalueindex SCHEME_CURRENT 19cbb8fa-5279-450e-9fac-8a3d5fedd0c1 12bbebe6-58d6-4636-95bb-3217ef867c1a 0
  powercfg /setactive SCHEME_CURRENT
  ```
  (This is Power Options → Change plan settings → Advanced → Wireless Adapter Settings → Power Saving Mode →
  Maximum Performance; use the dialog if the command is rejected.) Also check Device Manager → your Wi-Fi
  adapter → Properties → Advanced for a "Power Saving Mode" / "MIMO Power Save" setting and turn it off, and
  untick "Allow the computer to turn off this device to save power" on the Power Management tab.
- **Internet.** With one Wi-Fi adapter you lose internet while connected to the CodeCell. An Ethernet cable (or a
  second adapter) keeps it; the CodeCell traffic stays on its own subnet.

## macOS

- **Firewall prompt.** If the macOS firewall is on, the first run asks whether `python` may accept incoming
  network connections: choose **Allow**. If it was denied, remove/re-allow it under System Settings → Network →
  Firewall → Options. "Block all incoming connections" must be off.
- **Local Network permission (macOS 15 and later).** Allow the program you start it from (Terminal, VS Code,
  ...) under System Settings → Privacy & Security → Local Network. Without it the hello cannot be sent.
- **Power saving.** macOS has no Wi-Fi power-saving setting to change. Turn off Low Power Mode (battery) while
  playing.
- **Auto-Join.** macOS may switch back to a network with internet. Keep the CodeCell network joined, or turn off
  Auto-Join for your normal network while playing.

## Linux

```
sudo nmcli connection modify "SchimonsTuffESP32v2" 802-11-wireless.powersave 2
```

Then reconnect. Allow incoming UDP port 4210 if you use a firewall.

## Troubleshooting

| Symptom                                        | Likely cause                                                                                          |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `No packets from the CodeCell` forever         | Not joined to the CodeCell's Wi-Fi; firewall (Windows: network is Public); macOS Local Network off    |
| Works with `ComputerReciever.py`, not the game | Another program already uses UDP port 4210 (close old copies), or the controller isn't started        |
| Packets arrive but `Max:` is several 100 ms    | Wi-Fi power saving on the computer; other 2.4 GHz networks nearby (the CodeCell picks channel 1/6/11) |
| `Missing:` is often above 0                    | Weak signal: move the CodeCell closer to the computer, keep it away from USB 3 cables                 |
| Worked until the computer reconnected          | Old firmware without hello support: reflash, or the computer got another address than `192.168.4.2`   |
