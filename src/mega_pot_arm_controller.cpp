#include <Arduino.h>

constexpr uint32_t baudRate = 115200;
constexpr unsigned long sampleIntervalMs = 80;
constexpr int analogSamples = 8;
constexpr uint8_t controlPin = A0;
constexpr int reachThreshold = 640;

String esp32Line;
unsigned long lastSampleMs = 0;
int lastReachState = -1;

int readAveragedAnalog(uint8_t pin) {
  long total = 0;
  for (int index = 0; index < analogSamples; ++index) {
    total += analogRead(pin);
    delayMicroseconds(300);
  }
  return static_cast<int>(total / analogSamples);
}

void sendCommand(const String& command) {
  Serial.print("Mega pot -> ESP32 -> car: ");
  Serial.println(command);
  Serial1.println(command);
}

void updateReachCommand() {
  const int analogValue = readAveragedAnalog(controlPin);
  const int reachState = analogValue > reachThreshold ? 1 : 0;

  Serial.print("A0=");
  Serial.print(analogValue);
  Serial.print(" state=");
  Serial.println(reachState == 1 ? "reach down" : "reach up");

  if (reachState == lastReachState) {
    return;
  }

  lastReachState = reachState;
  sendCommand(reachState == 1 ? "reach down" : "reach up");
}

void pollEsp32() {
  while (Serial1.available()) {
    const char value = static_cast<char>(Serial1.read());
    if (value == '\n' || value == '\r') {
      esp32Line.trim();
      if (esp32Line.length() > 0) {
        Serial.print("ESP32: ");
        Serial.println(esp32Line);
      }
      esp32Line = "";
    } else {
      esp32Line += value;
      if (esp32Line.length() > 200) {
        esp32Line = "";
      }
    }
  }
}

void printHelp() {
  Serial.println("Mega potentiometer arm controller ready.");
  Serial.println("A0 > 640 sends reach down once.");
  Serial.println("A0 <= 640 sends reach up once.");
  Serial.println("Mega TX1=18 -> ESP32 RX2 GPIO16, Mega RX1=19 <- ESP32 TX2 GPIO17.");
}

void setup() {
  Serial.begin(baudRate);
  Serial1.begin(baudRate);
  delay(300);
  printHelp();
}

void loop() {
  pollEsp32();

  const unsigned long now = millis();
  if (now - lastSampleMs < sampleIntervalMs) {
    return;
  }
  lastSampleMs = now;

  updateReachCommand();
}
