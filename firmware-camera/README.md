# firmware-camera: HAIL-E's camera (XIAO ESP32-S3 Sense)

Firmware for the **Seeed XIAO ESP32-S3 Sense** with its camera/mic expansion board. It is Part 4's first step: the board joins the brain on ai1 as role `camera` and streams QVGA JPEGs. The brain shows them in its live view (http://localhost:8766 on ai1, through an SSH tunnel), and Rocky gets a frame when a question is about seeing. `look` and face tracking need the neck, which comes later.

| | |
|---|---|
| Version | cam-fw 0.1.0 |
| Board | XIAO ESP32-S3 Sense: ESP32-S3R8 (8 MB octal PSRAM), 8 MB flash, OV2640 or OV3660 camera |
| Brain | `ws://192.168.1.99:8765`, hello `{"who":"xiao-camera","roles":["camera"]}` |
| Sends | binary `0x02` + JPEG at the rate the brain asks for (`stream on`, 10 fps); `temp` every 10 s |
| LED | the orange user LED is on while it's connected to the brain |

## Flash the prebuilt image
`bin/cam-fw-0.1.0-factory.bin` is a complete image (bootloader, partitions, app). Take the factory backup first (once):
```
esptool --port COMx read-flash 0 ALL xiao-factory.bin
esptool --port COMx write-flash 0x0 bin\cam-fw-0.1.0-factory.bin
```
If the port doesn't show up or the flash fails to connect: hold **BOOT**, plug in USB-C (or tap RESET), release BOOT.
Restore: `esptool --port COMx write-flash 0x0 xiao-factory.bin`.

## Settings (serial console, 115200; PuTTY is fine)
Saved on the board in NVS:
```
token <ROBOT_TOKEN>            the value in ~/desk-robot/server/.env on ai1
wifi IOTNSFW <password>        saves and reboots
brain 192.168.1.99             (the default; only needed to change it)
net                            WiFi + brain state, token fingerprint
```
Then `status` in the brain console should list `xiao-camera (fw 0.1.0, ...) [camera]`.

## Console
```
info                     version, sensor, MAC, PSRAM, frames captured/sent
stats on|off             captured/sent per second, frame size, RSSI, chip temp every 5 s
snap                     grab one frame, report its size
stream on|off [fps]      the brain turns this on itself when it connects
flip none|v|h|both       picture orientation (saved); both = rotated 180 degrees
temp   reboot   help
```

## Build
Same toolchain as `firmware-face`: pioarduino 55.03.311 (Arduino core 3.3.11). `pio run`, then the image is `.pio/build/camera/firmware.factory.bin`. Everything downloads from GitHub except PlatformIO's own `tool-scons`, which comes from the registry; where that is blocked, unpack the `scons` wheel from PyPI into `~/.platformio/packages/tool-scons` with a `scons.py` launcher, a `package.json` and a `.piopm` (version 4.41101.0).

## Not used yet
The Sense's PDM mic (the Yahboom is the robot's mic) and the SD slot (its CS pin is the LED's GPIO21).
