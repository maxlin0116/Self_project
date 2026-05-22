#!/usr/bin/env bash
set -euo pipefail

echo "[1/5] Updating package index..."
sudo apt update

echo "[2/5] Installing tools from the Raspberry Pi slides..."
sudo apt install -y \
  vim \
  git \
  i2c-tools \
  python3-smbus \
  python3-venv \
  python3-pip \
  gpiod \
  libgpiod-dev \
  python3-gpiozero \
  python3-opencv \
  python3-picamera2 \
  ffmpeg \
  rpicam-apps

echo "[3/5] Enabling I2C..."
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_i2c 0
else
  echo "raspi-config not found; skipping automatic I2C enable."
fi

echo "[4/5] Creating LCD Python virtual environment..."
mkdir -p "$HOME/lcd_project"
cd "$HOME/lcd_project"
python3 -m venv env
source env/bin/activate
python -m pip install --upgrade pip
python -m pip install RPLCD smbus2
deactivate

echo "[5/5] Quick checks..."
echo "git: $(git --version)"
echo "gpiodetect:"
gpiodetect || true
echo "I2C scan command to run after wiring LCD: i2cdetect -y 1"
echo "Camera check command: rpicam-hello --list-cameras"

echo "Done. Rebooting is recommended if I2C was just enabled:"
echo "sudo reboot"
