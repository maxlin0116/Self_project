#include <Arduino.h>
#include <WebServer.h>
#include <WiFi.h>

const char* ssid = "Max";
const char* password = "maxlin1161";

constexpr int megaRxPin = 16;  // ESP32 RX2, optional Mega TX3 -> ESP32 RX2
constexpr int megaTxPin = 17;  // ESP32 TX2 -> Mega RX3 pin 15
constexpr uint32_t megaBaud = 115200;

WebServer server(80);

void forwardToMega(const String& command) {
  Serial.print("Forward to Mega: ");
  Serial.println(command);
  Serial2.println(command);
}

void handleData() {
  if (!server.hasArg("plain")) {
    server.send(400, "text/plain", "No data received");
    return;
  }

  String command = server.arg("plain");
  command.trim();

  if (command.length() == 0) {
    server.send(400, "text/plain", "Empty command");
    return;
  }

  forwardToMega(command);
  server.send(204, "text/plain", "");
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(megaBaud, SERIAL_8N1, megaRxPin, megaTxPin);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    Serial.println("Connecting to WiFi...");
    Serial.print("Current WiFi status: ");
    Serial.println(WiFi.status());
  }

  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());

  server.on("/", HTTP_GET, []() {
    server.send(200, "text/plain", "Send POST data with angle to /data");
  });
  server.on("/data", HTTP_POST, handleData);
  server.begin();

  Serial.println("ESP32 Wi-Fi bridge ready.");
}

void loop() {
  server.handleClient();

  while (Serial2.available()) {
    Serial.write(Serial2.read());
  }
}
