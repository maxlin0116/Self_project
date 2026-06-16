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

## 常用測試指令

這一段是目前實際測試最常用的指令。`COM10` 和 `10.201.150.168` 都可能會
因為重新插線或換網路而改變，所以每次測試前先確認一次。

### 1. 先確認序列埠和 ESP32 IP

列出目前接到電腦的板子：

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe device list
```

確認車子 ESP32 是否在線，並找出目前 IP：

```powershell
python .\mediapipe_wifi_control.py --ip auto --test-ip
```

如果已經知道 IP，可以直接測：

```powershell
python .\mediapipe_wifi_control.py --ip 10.201.150.168 --test-ip
```

### 2. 燒錄手套讀值程式

把 Mega/Arduino 燒成手套讀值程式，輸出 `A0/A1/A2` 對應
`index/middle/ring`：

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e glove_arduino_mega -t upload --upload-port COM10
```

如果只想燒「純看 A0/A1/A2 原始值」的測試程式：

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e pot_threshold_tester -t upload --upload-port COM10
```

燒完 `pot_threshold_tester` 後看數值：

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe device monitor -p COM10 -b 115200
```

### 3. 看手套數值和閾值

只看食指 `index/A0` 電壓和 ADC 值：

```powershell
python .\glove_voltage_viewer.py --serial-port COM10 --channel index
```

看三根手指電壓和 ADC 值：

```powershell
python .\glove_voltage_viewer.py --serial-port COM10
```

目前預設判斷閾值：

```text
A0 / index  食指：> 630 算彎曲
A1 / middle 中指：< 420 算彎曲
A2 / ring   無名指：< 380 算彎曲
```

### 4. 單獨測手套控制手臂

先乾跑，只看手套判斷和會送出的指令，不真的控制車/手臂：

```powershell
python .\glove_serial_arm_test.py --serial-port COM10 --dry-run
```

真的送到 ESP32 控制手臂：

```powershell
python .\glove_serial_arm_test.py --serial-port COM10 --car-ip 10.201.150.168
```

目前常用的肩膀 jog 測試設定：

```powershell
python .\glove_serial_arm_test.py --serial-port COM10 --car-ip 10.201.150.168 --middle-jog-speed 20 --middle-jog-ms 100 --index-jog-speed 120 --index-jog-ms 1
```

對應動作：

```text
中指剛彎曲一次 -> jog shoulder 20 100
食指剛彎曲一次 -> jog shoulder 120 1
無名指彎曲      -> close
無名指放開      -> open
```

### 5. 單獨測影像辨識控制車子

只開影像辨識，不送 ESP32 指令：

```powershell
python .\mediapipe_wifi_control.py --offline --camera-index 0
```

真的用影像辨識控制車子：

```powershell
python .\mediapipe_wifi_control.py --ip 10.201.150.168 --camera-index 0
```

影像辨識手勢：

```text
左手舉起 -> 左轉
右手舉起 -> 右轉
兩手舉起 -> 前進
都沒舉   -> 停止
```

如果相機打不開，可以試：

```powershell
python .\mediapipe_wifi_control.py --offline --camera-index 1
python .\mediapipe_wifi_control.py --offline --camera-backend dshow
```

### 6. 影像辨識加手套手臂整合測試

影像辨識控制車子，手套控制手臂：

```powershell
python .\vision_glove_arm_control.py --ip 10.201.150.168 --serial-port COM10 --camera-index 0
```

不要讓無名指控制爪子時，加 `--no-gripper`：

```powershell
python .\vision_glove_arm_control.py --ip 10.201.150.168 --serial-port COM10 --camera-index 0 --no-gripper
```

整合程式的目前預設：

```text
影像辨識控制車輪
中指彎曲一次 -> jog shoulder 20 100
食指彎曲一次 -> jog shoulder 120 1
無名指彎曲   -> close
無名指放開   -> open
```

### 7. 手動輸入手臂指令

直接開互動模式：

```powershell
python .\arm_wifi_control.py --ip 10.201.150.168 --no-confirm
```

常用手臂指令：

```text
jog shoulder 20 100
jog shoulder 120 1
jog shoulder 110 80
set shoulder 90
open
close
stop all
log
quit
```

送單一指令後退出：

```powershell
python .\arm_wifi_control.py --ip 10.201.150.168 --command "jog shoulder 20 100" --no-confirm
```

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

## Arduino Voltage Monitor

`src/voltage_monitor.cpp` is a separate firmware for another Arduino board. It
reads voltage changes on `A0`, prints readings at 115200 baud, and flashes the
built-in LED when the filtered voltage changes by at least `0.10 V`.

Default wiring for measuring up to about 10V:

```text
measured voltage + -> 10k resistor -> A0
A0                 -> 10k resistor -> GND
measured voltage - -> Arduino GND
```

Do not connect a voltage higher than 5V directly to `A0`. Change
`dividerTopOhms`, `dividerBottomOhms`, and `changeThresholdVolts` in
`src/voltage_monitor.cpp` if you use a different resistor divider or threshold.

Build the voltage monitor:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e arduino_voltage_monitor
```

Upload the voltage monitor:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e arduino_voltage_monitor --target upload
```

## MeArm Servo Test

The Mega2560 arm test firmware is separate from the car motor controller.

Current working arm-control version:

- The four arm motors are controlled as continuous-rotation servos by speed
  and time, not by absolute angle.
- Wi-Fi commands go PC -> ESP32 -> Mega2560 Serial3.
- Working pins:

```text
base     signal -> D11
shoulder signal -> D10
elbow    signal -> D9
gripper  signal -> D8
```

Interactive PC control:

```powershell
python ".\arm_wifi_control.py" --ip 10.172.23.168
```

Useful commands:

```text
move base 60 400
move shoulder 120 400
move elbow 60 400
move gripper 120 400
grip 60 400
grip 120 400
stop all
```

Speed reference:

```text
90 = stop
less than 90 = one direction
greater than 90 = the other direction
farther from 90 = faster
```

Legacy A0-A3 wiring notes, kept only for reference:

```text
base     signal -> shield A0
shoulder signal -> shield A1
elbow    signal -> shield A2
gripper  signal -> shield A3
servo red wire   -> shield 5V
servo brown/black wire -> shield GND
```

If the shield headers are ordered `5V GND A0`, connect each SG90 as:

```text
red wire       -> 5V
brown/black wire -> GND
orange/yellow wire -> A0/A1/A2/A3
```

Build the arm test firmware:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e mega2560_arm_test
```

Upload the arm test firmware:

```powershell
C:\Users\Max\.platformio\penv\Scripts\platformio.exe run -e mega2560_arm_test --target upload
```

Open the serial monitor at 115200 baud, then type commands:

```text
help
status
move base 60 400
move shoulder 120 400
move elbow 60 400
move gripper 120 400
grip 60 400
stop all
```

Start with short durations like `200` or `400`, then increase slowly after
confirming the direction is correct.

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

## ESP32 Direct Wi-Fi AP

The ESP32 bridge also starts its own Wi-Fi network, so the PC can connect
directly without a phone hotspot/router.

```text
Wi-Fi name: TrashCar-ESP32
Password: trashcar123
ESP32 IP: 192.168.4.1
```

After connecting the PC to `TrashCar-ESP32`, test it:

```powershell
python ".\mediapipe_wifi_control.py" --test-ip --ip 192.168.4.1
```

Run control directly:

```powershell
python ".\mediapipe_wifi_control.py" --ip 192.168.4.1
```

## Phone Hotspot Mode

The ESP32 also tries to join the phone hotspot configured in
`src/esp32_bridge.cpp`:

```text
Wi-Fi name: Max
Password: maxlin1161
```

Connect the PC to the same phone hotspot, then test automatic discovery:

```powershell
python ".\mediapipe_wifi_control.py" --test-ip
```

If the phone shows an ESP32 IP, test it directly:

```powershell
python ".\mediapipe_wifi_control.py" --test-ip --ip 10.172.23.168
```

If direct IP testing times out while the phone still lists the ESP32 as
connected, the hotspot is probably isolating connected devices. Use
`TrashCar-ESP32` direct AP mode in that case.
