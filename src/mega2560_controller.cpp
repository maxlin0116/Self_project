#include <Arduino.h>

constexpr uint8_t motorRightIn1 = 6;
constexpr uint8_t motorRightIn2 = 7;
constexpr uint8_t motorRightPwm = 11;
constexpr uint8_t motorLeftIn1 = 8;
constexpr uint8_t motorLeftIn2 = 9;
constexpr uint8_t motorLeftPwm = 10;

constexpr int motorPower = 180;
constexpr unsigned long timeoutDuration = 500;

String usbBuffer;
String esp32Buffer;
unsigned long lastCommandTime = 0;

void stopMotors() {
  analogWrite(motorLeftPwm, 0);
  analogWrite(motorRightPwm, 0);
  digitalWrite(motorRightIn1, LOW);
  digitalWrite(motorRightIn2, LOW);
  digitalWrite(motorLeftIn1, LOW);
  digitalWrite(motorLeftIn2, LOW);
}

void motorWriting(int leftPower, int rightPower) {
  if (leftPower > 0) {
    digitalWrite(motorLeftIn1, LOW);
    digitalWrite(motorLeftIn2, HIGH);
  } else if (leftPower < 0) {
    digitalWrite(motorLeftIn1, HIGH);
    digitalWrite(motorLeftIn2, LOW);
  } else {
    digitalWrite(motorLeftIn1, LOW);
    digitalWrite(motorLeftIn2, LOW);
  }

  if (rightPower > 0) {
    digitalWrite(motorRightIn1, HIGH);
    digitalWrite(motorRightIn2, LOW);
  } else if (rightPower < 0) {
    digitalWrite(motorRightIn1, LOW);
    digitalWrite(motorRightIn2, HIGH);
  } else {
    digitalWrite(motorRightIn1, LOW);
    digitalWrite(motorRightIn2, LOW);
  }

  analogWrite(motorLeftPwm, constrain(abs(leftPower), 0, 255));
  analogWrite(motorRightPwm, constrain(abs(rightPower), 0, 255));
}

int directionToSign(const String& direction) {
  if (direction == "0") {
    return 1;
  }
  if (direction == "1") {
    return -1;
  }
  return 0;
}

void runMotors(const String& rightDirection, int rightTime, const String& leftDirection, int leftTime) {
  const int rightPower = directionToSign(rightDirection) * motorPower;
  const int leftPower = directionToSign(leftDirection) * motorPower;
  const int durationMs = max(rightPower == 0 ? 0 : rightTime, leftPower == 0 ? 0 : leftTime);

  if (durationMs <= 0) {
    stopMotors();
    return;
  }

  motorWriting(leftPower, rightPower);
  delay(durationMs);
  stopMotors();
}

bool parseCommand(const String& command, String& rightDirection, int& rightTime, String& leftDirection, int& leftTime) {
  int space1 = command.indexOf(' ');
  int space2 = command.indexOf(' ', space1 + 1);
  int space3 = command.indexOf(' ', space2 + 1);

  if (space1 == -1 || space2 == -1 || space3 == -1) {
    return false;
  }

  rightDirection = command.substring(0, space1);
  rightTime = command.substring(space1 + 1, space2).toInt();
  leftDirection = command.substring(space2 + 1, space3);
  leftTime = command.substring(space3 + 1).toInt();
  return true;
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  String rightDirection;
  String leftDirection;
  int rightTime = 0;
  int leftTime = 0;

  Serial.print("Command: ");
  Serial.println(command);

  if (!parseCommand(command, rightDirection, rightTime, leftDirection, leftTime)) {
    Serial.println("Invalid command. Expected: right_direction right_time left_direction left_time");
    return;
  }

  lastCommandTime = millis();
  runMotors(rightDirection, rightTime, leftDirection, leftTime);
}

void pollStream(Stream& stream, String& buffer) {
  while (stream.available()) {
    char value = static_cast<char>(stream.read());
    if (value == '\n' || value == '\r') {
      handleCommand(buffer);
      buffer = "";
    } else {
      buffer += value;
    }
  }
}

void setup() {
  pinMode(motorRightIn1, OUTPUT);
  pinMode(motorRightIn2, OUTPUT);
  pinMode(motorRightPwm, OUTPUT);
  pinMode(motorLeftIn1, OUTPUT);
  pinMode(motorLeftIn2, OUTPUT);
  pinMode(motorLeftPwm, OUTPUT);
  stopMotors();

  Serial.begin(115200);
  Serial3.begin(115200);

  Serial.println("Mega2560 motor controller ready.");
  Serial.println("Command format: right_direction right_time left_direction left_time");
}

void loop() {
  pollStream(Serial, usbBuffer);
  pollStream(Serial3, esp32Buffer);

  if (millis() - lastCommandTime > timeoutDuration) {
    stopMotors();
  }
}
