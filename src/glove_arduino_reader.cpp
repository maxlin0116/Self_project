#include <Arduino.h>

#if defined(ARDUINO_AVR_MEGA2560)
#define gloveSerial Serial1
#else
#include <SoftwareSerial.h>
constexpr uint8_t esp32RxPin = 10;
constexpr uint8_t esp32TxPin = 11;
SoftwareSerial gloveSerial(esp32RxPin, esp32TxPin);
#endif

constexpr uint32_t baudRate = 115200;
constexpr unsigned long sendIntervalMs = 40;

const int sensorPins[] = {A0, A1, A2};
const char* sensorNames[] = {"index", "middle", "ring"};
constexpr size_t sensorCount = sizeof(sensorPins) / sizeof(sensorPins[0]);

unsigned long lastSendMs = 0;
unsigned long sequenceNumber = 0;

void writeGloveJson(Stream& output) {
  output.print("{\"name\":\"trashcar-glove\",\"seq\":");
  output.print(sequenceNumber++);
  for (size_t index = 0; index < sensorCount; ++index) {
    output.print(",\"");
    output.print(sensorNames[index]);
    output.print("\":");
    output.print(analogRead(sensorPins[index]));
  }
  output.println("}");
}

void setup() {
  Serial.begin(baudRate);
  gloveSerial.begin(baudRate);

  Serial.println("Arduino glove reader ready.");
#if defined(ARDUINO_AVR_MEGA2560)
  Serial.println("Sending glove JSON to ESP32 on Serial1: TX1=18 RX1=19.");
#else
  Serial.println("Sending glove JSON to ESP32 on SoftwareSerial: TX=D11 RX=D10.");
#endif
}

void loop() {
  const unsigned long now = millis();
  if (now - lastSendMs < sendIntervalMs) {
    return;
  }
  lastSendMs = now;

  writeGloveJson(Serial);
  writeGloveJson(gloveSerial);
}
