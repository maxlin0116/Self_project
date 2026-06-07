#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

const char* ssid = "Max";
const char* password = "maxlin1161";
const char* hostName = "trashcar-glove";

constexpr uint16_t glovePort = 4211;
constexpr int arduinoRxPin = 16;
constexpr int arduinoTxPin = 17;
constexpr uint32_t arduinoBaud = 115200;

WiFiUDP udp;
unsigned long lastSendMs = 0;
String arduinoLine;

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setHostname(hostName);
  WiFi.begin(ssid, password);

  Serial.print("Connecting glove ESP32 to Wi-Fi: ");
  Serial.println(ssid);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Glove ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(arduinoBaud, SERIAL_8N1, arduinoRxPin, arduinoTxPin);

  connectWifi();
  udp.begin(glovePort);
  Serial.print("Broadcasting glove data on UDP port ");
  Serial.println(glovePort);
  Serial.println("Waiting for Arduino glove JSON on Serial2 RX=GPIO16 TX=GPIO17.");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  while (Serial2.available()) {
    const char value = static_cast<char>(Serial2.read());
    if (value == '\n' || value == '\r') {
      arduinoLine.trim();
      if (arduinoLine.startsWith("{\"name\":\"trashcar-glove\"")) {
        udp.beginPacket(IPAddress(255, 255, 255, 255), glovePort);
        udp.print(arduinoLine);
        udp.endPacket();
        Serial.println(arduinoLine);
      }
      arduinoLine = "";
    } else {
      arduinoLine += value;
      if (arduinoLine.length() > 256) {
        arduinoLine = "";
      }
    }
  }
}
