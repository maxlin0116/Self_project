#include <Arduino.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiUdp.h>

const char* ssid = "Max";
const char* password = "maxlin1161";
const char* apSsid = "TrashCar-ESP32";
const char* apPassword = "trashcar123";
const char* hostName = "trashcar-esp32";

constexpr int megaRxPin = 16;  // ESP32 RX2, optional Mega TX3 -> ESP32 RX2
constexpr int megaTxPin = 17;  // ESP32 TX2 -> Mega RX3 pin 15
constexpr uint32_t megaBaud = 115200;
constexpr unsigned long stationConnectTimeoutMs = 12000;
constexpr unsigned long reconnectIntervalMs = 10000;
constexpr uint16_t discoveryPort = 4210;

WebServer server(80);
WiFiUDP discoveryUdp;
unsigned long lastReconnectAttempt = 0;
String megaLineBuffer;
String lastMegaLine = "no Mega response yet";

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

String wifiStatusText() {
  switch (WiFi.status()) {
    case WL_CONNECTED:
      return "connected";
    case WL_NO_SSID_AVAIL:
      return "ssid_not_available";
    case WL_CONNECT_FAILED:
      return "connect_failed";
    case WL_CONNECTION_LOST:
      return "connection_lost";
    case WL_DISCONNECTED:
      return "disconnected";
    default:
      return String("status_") + WiFi.status();
  }
}

String statusJson() {
  String json = "{";
  json += "\"name\":\"trashcar-esp32\",";
  json += "\"sta_status\":\"" + wifiStatusText() + "\",";
  json += "\"sta_ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"ap_ip\":\"" + WiFi.softAPIP().toString() + "\",";
  json += "\"rssi\":" + String(WiFi.RSSI());
  json += "}";
  return json;
}

void setupRoutes() {
  server.on("/", HTTP_GET, []() {
    server.send(200, "text/plain", "Send POST data with angle to /data");
  });
  server.on("/status", HTTP_GET, []() {
    server.send(200, "application/json", statusJson());
  });
  server.on("/mega-log", HTTP_GET, []() {
    server.send(200, "text/plain", lastMegaLine);
  });
  server.on("/data", HTTP_POST, handleData);
  server.begin();
}

void setupWifi() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);
  WiFi.setHostname(hostName);

  const bool apStarted = WiFi.softAP(apSsid, apPassword);
  Serial.print("ESP32 AP: ");
  Serial.println(apStarted ? apSsid : "failed");
  Serial.print("ESP32 AP IP: ");
  Serial.println(WiFi.softAPIP());

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  const unsigned long startTime = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - startTime < stationConnectTimeoutMs) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("ESP32 STA IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("STA WiFi not connected. Use the ESP32 AP instead.");
  }

  discoveryUdp.begin(discoveryPort);
  Serial.print("UDP discovery port: ");
  Serial.println(discoveryPort);
}

void reconnectStationIfNeeded() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  const unsigned long now = millis();
  if (now - lastReconnectAttempt < reconnectIntervalMs) {
    return;
  }

  lastReconnectAttempt = now;
  Serial.println("Reconnecting STA WiFi...");
  WiFi.disconnect();
  WiFi.begin(ssid, password);
}

void handleDiscoveryUdp() {
  const int packetSize = discoveryUdp.parsePacket();
  if (packetSize <= 0) {
    return;
  }

  char buffer[64];
  const int length = discoveryUdp.read(buffer, sizeof(buffer) - 1);
  if (length <= 0) {
    return;
  }
  buffer[length] = '\0';

  String request(buffer);
  request.trim();
  if (request != "trashcar-discover") {
    return;
  }

  String response = "trashcar-esp32 ";
  response += WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString()
                                            : WiFi.softAPIP().toString();
  response += " ";
  response += WiFi.softAPIP().toString();

  discoveryUdp.beginPacket(discoveryUdp.remoteIP(), discoveryUdp.remotePort());
  discoveryUdp.print(response);
  discoveryUdp.endPacket();
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(megaBaud, SERIAL_8N1, megaRxPin, megaTxPin);

  setupWifi();
  setupRoutes();

  Serial.println("ESP32 Wi-Fi bridge ready.");
}

void loop() {
  server.handleClient();
  handleDiscoveryUdp();
  reconnectStationIfNeeded();

  while (Serial2.available()) {
    const char value = static_cast<char>(Serial2.read());
    Serial.write(value);
    if (value == '\n' || value == '\r') {
      megaLineBuffer.trim();
      if (megaLineBuffer.length() > 0) {
        lastMegaLine = megaLineBuffer;
      }
      megaLineBuffer = "";
    } else {
      megaLineBuffer += value;
      if (megaLineBuffer.length() > 160) {
        megaLineBuffer.remove(0, megaLineBuffer.length() - 160);
      }
    }
  }
}
