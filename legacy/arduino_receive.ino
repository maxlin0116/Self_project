#include <WiFi.h>
#include <WebServer.h>

const char* ssid = "Max"; // Wi-Fi SSID
const char* password = "maxlin1161"; // Wi-Fi password

WebServer server(80);  // HTTP 服务器的端口号为80


const int motorPin1 = 16; // IN1
const int motorPin2 = 17; // IN2
const int motorPin3 = 0; // IN1
const int motorPin4 = 4; // IN2



unsigned long lastCommandTime = 0;  // 記錄最後一次接收指令的時間
const unsigned long timeoutDuration = 500;  // 設定超時時間 (1秒)

void stopMotors() {
    digitalWrite(motorPin1, LOW);
    digitalWrite(motorPin2, LOW);
    digitalWrite(motorPin3, LOW);
    digitalWrite(motorPin4, LOW);
}



void setup() {
  pinMode(motorPin1, OUTPUT);
  pinMode(motorPin2, OUTPUT);
  pinMode(motorPin3, OUTPUT);
  pinMode(motorPin4, OUTPUT);


  digitalWrite(motorPin1, LOW);
  digitalWrite(motorPin2, LOW);
  digitalWrite(motorPin3, LOW);
  digitalWrite(motorPin4, LOW);

  Serial.begin(115200);

  // 设置ESP32为Wi-Fi热点模式
    WiFi.begin(ssid, password);
while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    Serial.println("Connecting to WiFi...");
    Serial.print("Current WiFi status: ");
    Serial.println(WiFi.status());
    
}

  Serial.print("ESP32的IP地址是：");
  Serial.println(WiFi.localIP());








  // 配置HTTP服务器处理函数
  server.on("/", HTTP_GET, []() {
    server.send(200, "text/plain", "Send POST data with angle to /data");
  });

  server.on("/data", HTTP_POST, []() {
    if (server.hasArg("plain")) {
      lastCommandTime = millis(); 
      String data = server.arg("plain");
          // 找到空格的位置
    int space1 = data.indexOf(" ");  // 找到第一個空格
    int space2 = data.indexOf(" ", space1 + 1);  // 找到第二個空格
    int space3 = data.indexOf(" ", space2 + 1);  // 找到第三個空格找到第四個空格
//這裡要依據你們的傳訊格式決定









    
    if (space1 != -1 && space2 != -1 && space3 != -1) {
        String part1 = data.substring(0, space1);
        String part2 = data.substring(space1 + 1, space2);
        String part3 = data.substring(space2 + 1, space3);
        String part4 = data.substring(space3 + 1); // 最後一部分到結尾
  
        int right_motor_time=part2.toInt();  
        int left_motor_time=part4.toInt();  
        if (part1=="2"){
          digitalWrite(motorPin1, LOW);
          digitalWrite(motorPin2, LOW);
          digitalWrite(motorPin3, LOW);
          digitalWrite(motorPin4, LOW);
          }
        if (right_motor_time>left_motor_time){
        
        if (part1=="0"){
            digitalWrite(motorPin1, LOW);
            digitalWrite(motorPin2, HIGH);
        }
        if (part1=="1"){
            digitalWrite(motorPin1, HIGH);
            digitalWrite(motorPin2, LOW);
        }
        delay(right_motor_time-left_motor_time);
        if (part3=="0"){
            digitalWrite(motorPin3, HIGH);
            digitalWrite(motorPin4, LOW);
        } 
        
        if (part3=="1"){
            digitalWrite(motorPin3, LOW);
            digitalWrite(motorPin4, HIGH);
        }
            delay(left_motor_time); //
            server.send(204, "text/plain", "");  // 204 No 
            digitalWrite(motorPin1, LOW);
            digitalWrite(motorPin2, LOW);
            digitalWrite(motorPin3, LOW);
            digitalWrite(motorPin4, LOW);
            
            
        }
        if (left_motor_time>=right_motor_time){
        
        if (part3=="0"){
            digitalWrite(motorPin3, HIGH);
            digitalWrite(motorPin4, LOW);
        }
        if (part3=="1"){
            digitalWrite(motorPin3, LOW);
            digitalWrite(motorPin4, HIGH);
        }
        delay(left_motor_time-right_motor_time);
        if (part1=="0"){
            digitalWrite(motorPin1, LOW);
            digitalWrite(motorPin2, HIGH);
        } 
        
        if (part1=="1"){
            digitalWrite(motorPin1, HIGH);
            digitalWrite(motorPin2, LOW);
        }
            delay(right_motor_time); //
            server.send(204, "text/plain", "");  // 204 No 
            digitalWrite(motorPin1, LOW);
            digitalWrite(motorPin2, LOW);
            digitalWrite(motorPin3, LOW);
            digitalWrite(motorPin4, LOW);
        }        
    }
      
     
    else {
      server.send(400, "text/plain", "No data received");
    }
  }
  });

  server.begin();  // 启动HTTP服务器
}


void loop() {
  server.handleClient();  // 处理HTTP客户端请求
    // 檢查是否超時
    if (millis() - lastCommandTime > timeoutDuration) {
        stopMotors();  // 超時後停止馬達
    }
}
