#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>

const char* ssid = "Max";
const char* password = "maxlin1161";
const char* hostName = "trashcar-command-sender";
const char* carIp = "10.237.165.168";

constexpr int megaRxPin = 16;
constexpr int megaTxPin = 17;
constexpr uint32_t megaBaud = 115200;
constexpr unsigned long reconnectIntervalMs = 5000;
constexpr unsigned long requestTimeoutMs = 2500;

String megaLine;
unsigned long lastReconnectAttempt = 0;

String carDataUrl() {
  return String("http://") + carIp + "/data";
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setHostname(hostName);
  WiFi.begin(ssid, password);

  Serial.print("Connecting command sender ESP32 to Wi-Fi: ");
  Serial.println(ssid);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Command sender ESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial2.println(String("wifi connected ip=") + WiFi.localIP().toString());
}

void postToCar(const String& command) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi offline; command skipped.");
    Serial2.println("wifi offline");
    return;
  }

  HTTPClient http;
  const String url = carDataUrl();
  http.setTimeout(requestTimeoutMs);
  http.begin(url);
  http.addHeader("Content-Type", "text/plain");

  Serial.print("POST ");
  Serial.print(url);
  Serial.print(": ");
  Serial.println(command);

  const int statusCode = http.POST(command);
  http.end();

  Serial.print("Car HTTP status: ");
  Serial.println(statusCode);
  Serial2.println(String("car status ") + statusCode);
}

void pollMega() {
  while (Serial2.available()) {
    const char value = static_cast<char>(Serial2.read());
    if (value == '\n' || value == '\r') {
      megaLine.trim();
      if (megaLine.length() > 0) {
        postToCar(megaLine);
      }
      megaLine = "";
    } else {
      megaLine += value;
      if (megaLine.length() > 160) {
        megaLine = "";
        Serial.println("Command too long; cleared.");
        Serial2.println("command too long");
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(megaBaud, SERIAL_8N1, megaRxPin, megaTxPin);
  connectWifi();
  Serial.print("Forwarding Mega commands to car ESP32: ");
  Serial.println(carDataUrl());
  Serial2.println("command sender ready");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED &&
      millis() - lastReconnectAttempt >= reconnectIntervalMs) {
    lastReconnectAttempt = millis();
    connectWifi();
  }

  pollMega();
}
