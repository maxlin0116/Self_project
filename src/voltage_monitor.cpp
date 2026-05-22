#include <Arduino.h>

constexpr uint8_t voltagePin = A0;
constexpr uint8_t changeLedPin = LED_BUILTIN;

constexpr unsigned long sampleIntervalMs = 50;
constexpr unsigned long reportIntervalMs = 500;
constexpr float adcReferenceVoltage = 5.0f;
constexpr float dividerTopOhms = 10000.0f;
constexpr float dividerBottomOhms = 10000.0f;
constexpr float changeThresholdVolts = 0.10f;
constexpr float smoothingAlpha = 0.20f;

float filteredVoltage = 0.0f;
float lastReportedVoltage = 0.0f;
bool hasReading = false;
unsigned long lastSampleMs = 0;
unsigned long lastReportMs = 0;
unsigned long ledOffAtMs = 0;

float rawToInputVoltage(int rawValue) {
  const float pinVoltage = rawValue * adcReferenceVoltage / 1023.0f;
  const float dividerRatio = (dividerTopOhms + dividerBottomOhms) / dividerBottomOhms;
  return pinVoltage * dividerRatio;
}

void printReading(const char* label, int rawValue, float voltage, float delta) {
  Serial.print(label);
  Serial.print(", raw=");
  Serial.print(rawValue);
  Serial.print(", voltage=");
  Serial.print(voltage, 3);
  Serial.print(" V, delta=");
  Serial.print(delta, 3);
  Serial.println(" V");
}

void setup() {
  pinMode(changeLedPin, OUTPUT);
  digitalWrite(changeLedPin, LOW);

  Serial.begin(115200);
  delay(300);
  Serial.println("Arduino voltage monitor ready.");
  Serial.println("A0 reads voltage through a resistor divider.");
  Serial.println("Default divider: 10k from signal to A0, 10k from A0 to GND.");
}

void loop() {
  const unsigned long now = millis();

  if (now - lastSampleMs >= sampleIntervalMs) {
    lastSampleMs = now;

    const int rawValue = analogRead(voltagePin);
    const float voltage = rawToInputVoltage(rawValue);

    if (!hasReading) {
      filteredVoltage = voltage;
      lastReportedVoltage = voltage;
      hasReading = true;
    } else {
      filteredVoltage += smoothingAlpha * (voltage - filteredVoltage);
    }

    const float delta = filteredVoltage - lastReportedVoltage;
    if (abs(delta) >= changeThresholdVolts) {
      lastReportedVoltage = filteredVoltage;
      digitalWrite(changeLedPin, HIGH);
      ledOffAtMs = now + 120;
      printReading("CHANGE", rawValue, filteredVoltage, delta);
    }
  }

  if (hasReading && now - lastReportMs >= reportIntervalMs) {
    lastReportMs = now;
    const int rawValue = analogRead(voltagePin);
    printReading("READ", rawValue, filteredVoltage, filteredVoltage - lastReportedVoltage);
  }

  if (ledOffAtMs > 0 && now >= ledOffAtMs) {
    ledOffAtMs = 0;
    digitalWrite(changeLedPin, LOW);
  }
}
