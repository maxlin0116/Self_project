#include <Arduino.h>

constexpr uint32_t baudRate = 115200;

String usbLine;
String esp32Line;

void printHelp() {
  Serial.println("Mega command relay ready.");
  Serial.println("Type a car command and press Enter.");
  Serial.println("Examples: open, close, grip 120 400, stop all");
  Serial.println("Mega TX1=18 -> ESP32 RX2 GPIO16, Mega RX1=19 <- ESP32 TX2 GPIO17.");
}

void forwardCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  if (command.equalsIgnoreCase("help")) {
    printHelp();
    return;
  }

  Serial.print("PC -> ESP32 -> car: ");
  Serial.println(command);
  Serial1.println(command);
}

void pollUsb() {
  while (Serial.available()) {
    const char value = static_cast<char>(Serial.read());
    if (value == '\n' || value == '\r') {
      forwardCommand(usbLine);
      usbLine = "";
    } else {
      usbLine += value;
      if (usbLine.length() > 160) {
        usbLine = "";
        Serial.println("Input too long; cleared.");
      }
    }
  }
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

void setup() {
  Serial.begin(baudRate);
  Serial1.begin(baudRate);
  delay(300);
  printHelp();
}

void loop() {
  pollUsb();
  pollEsp32();
}
