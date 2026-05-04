# Self Project

## Architecture

The car uses Wi-Fi wirelessly between the PC and ESP32. The Mega2560 still needs
a short UART wire to the ESP32 because Mega2560 does not have Wi-Fi.

```text
PC Python + MediaPipe
        -> Wi-Fi HTTP
ESP32 bridge
        -> UART Serial2 to Serial3
Mega2560 motor controller
        -> motors
```

## Wiring

Minimum one-way control wiring:

```text
ESP32 GND      -> Mega GND
ESP32 TX2 GPIO17 -> Mega RX3 pin 15
```

Optional return/debug wiring:

```text
Mega TX3 pin 14 -> level shifter / resistor divider -> ESP32 RX2 GPIO16
```

Do not connect Mega TX directly to ESP32 RX without level shifting; Mega TX is
5V and ESP32 RX is 3.3V.

## Project Structure

```text
Self_project/
├─ mediapipe_wifi_control.py      PC-side MediaPipe + Wi-Fi control program
├─ whole_bode.py                  MediaPipe holistic tracking helper
├─ platformio.ini                 PlatformIO board/build configuration
├─ hardware/
│  └─ mearm/v1/                   MeArm v1 DXF and PDF hardware files
├─ README.md                      Project notes and commands
├─ compile_commands.json          VS Code C/C++ IntelliSense helper
├─ src/
│  ├─ esp32_bridge.cpp            Upload to ESP32; receives Wi-Fi commands
│  │                              and forwards them to Mega2560 by UART
│  └─ mega2560_controller.cpp     Upload to Mega2560; receives commands
│                                 and controls the motors
└─ legacy/
   └─ arduino_receive.ino         Old ESP32-only sketch kept for reference
```

Current firmware files are in `src/`. The `legacy/` folder is not used for the
current ESP32 + Mega2560 architecture.

## MeArm v1 Files

MeArm v1 laser cutting and assembly files are organized under:

```text
hardware/mearm/v1/
```

Open the PDF files for assembly instructions. Open the DXF files with LibreCAD,
Inkscape, AutoCAD, Fusion 360, LightBurn, or the laser cutter software.

## PlatformIO

Build Mega2560 firmware:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e mega2560
```

Upload Mega2560 firmware:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e mega2560 --target upload
```

Build ESP32 Wi-Fi bridge firmware:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e esp32_bridge
```

Upload ESP32 Wi-Fi bridge firmware:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e esp32_bridge --target upload
```

Open Serial Monitor:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe device monitor
```

In VS Code, you can also use the PlatformIO sidebar:

- `Project Tasks -> mega2560 -> General -> Build`
- `Project Tasks -> mega2560 -> General -> Upload`
- `Project Tasks -> esp32_bridge -> General -> Build`
- `Project Tasks -> esp32_bridge -> General -> Upload`

## Test ESP32 IP

Search for the ESP32 automatically without opening the camera:

```powershell
python ".\mediapipe_wifi_control.py" --test-ip
```

Test a different IP:

```powershell
python ".\mediapipe_wifi_control.py" --test-ip --ip 192.168.137.76
```

## Run Without ESP32

Force offline mode:

```powershell
python ".\mediapipe_wifi_control.py" --offline
```

If the ESP32 IP is offline, the main program automatically uses offline mode so
MediaPipe can still be tested without ESP32.

## Run With ESP32

The program searches for the ESP32 automatically:

```powershell
python ".\mediapipe_wifi_control.py"
```

You can still force a known ESP32 IP:

```powershell
python ".\mediapipe_wifi_control.py" --ip 192.168.137.76
```
