#include <Arduino.h>

constexpr uint32_t baudRate = 115200;
constexpr unsigned long reportIntervalMs = 150;
constexpr int analogSamples = 8;

const uint8_t pins[] = {A0, A1, A2};
const char* names[] = {"A0", "A1", "A2"};
constexpr size_t pinCount = sizeof(pins) / sizeof(pins[0]);

int readAveraged(uint8_t pin) {
  long total = 0;
  for (int index = 0; index < analogSamples; ++index) {
    total += analogRead(pin);
    delayMicroseconds(300);
  }
  return static_cast<int>(total / analogSamples);
}

void setup() {
  Serial.begin(baudRate);
  delay(300);
  Serial.println("Pot threshold tester ready.");
  Serial.println("Connect pot wipers to A0, A1, A2. Values are 0..1023.");
}

void loop() {
  for (size_t index = 0; index < pinCount; ++index) {
    if (index > 0) {
      Serial.print("  ");
    }
    Serial.print(names[index]);
    Serial.print("=");
    Serial.print(readAveraged(pins[index]));
  }
  Serial.println();
  delay(reportIntervalMs);
}
