#include <Arduino.h>
#include <Servo.h>

constexpr uint8_t motorRightIn1 = 6;
constexpr uint8_t motorRightIn2 = 7;
constexpr uint8_t motorRightPwm = 11;
constexpr uint8_t motorLeftIn1 = 8;
constexpr uint8_t motorLeftIn2 = 9;
constexpr uint8_t motorLeftPwm = 10;

constexpr int motorPower = 255;
constexpr int motorSlowPower = 90;
constexpr unsigned long motorTimeoutMs = 500;

struct ArmServo {
  const char* name;
  uint8_t pin;
  int speed;
  unsigned long stopAtMs;
  Servo servo;
};

ArmServo servos[] = {
  {"base", 22, 90, 0},
  {"shoulder", 23, 90, 0},
  {"elbow", 24, 90, 0},
  {"gripper", 25, 90, 0},
};

constexpr size_t servoCount = sizeof(servos) / sizeof(servos[0]);
constexpr size_t baseIndex = 0;
constexpr size_t shoulderIndex = 1;
constexpr size_t elbowIndex = 2;
constexpr size_t gripperIndex = 3;
constexpr int stopSpeed = 90;
constexpr unsigned long baseStepDelayMs = 8;
constexpr int armForwardSpeed = 150;
constexpr int armSlowForwardSpeed = 120;
constexpr int armReverseSpeed = 50;
constexpr int armSlowReverseSpeed = 60;
constexpr int gripperOpenSpeed = 65;
constexpr int gripperCloseSpeed = 120;
constexpr unsigned long gripperOpenMs = 300;
constexpr unsigned long gripperCloseMs = 400;

String usbBuffer;
String esp32Buffer;
unsigned long lastMotorCommandMs = 0;
unsigned long motorStopAtMs = 0;
unsigned long messageSequence = 0;

void blinkCommandLed() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(60);
  digitalWrite(LED_BUILTIN, LOW);
}

void printBoth(const String& text) {
  ++messageSequence;
  String line = "#";
  line += messageSequence;
  line += " ";
  line += text;
  Serial.println(line);
  Serial3.println(line);
}

void stopMotors() {
  motorStopAtMs = 0;
  analogWrite(motorLeftPwm, 0);
  analogWrite(motorRightPwm, 0);
}

void motorWriting(int leftPower, int rightPower) {
  if (leftPower >= 0) {
    digitalWrite(motorLeftIn1, LOW);
    digitalWrite(motorLeftIn2, HIGH);
  } else {
    digitalWrite(motorLeftIn1, HIGH);
    digitalWrite(motorLeftIn2, LOW);
  }

  if (rightPower >= 0) {
    digitalWrite(motorRightIn1, HIGH);
    digitalWrite(motorRightIn2, LOW);
  } else {
    digitalWrite(motorRightIn1, LOW);
    digitalWrite(motorRightIn2, HIGH);
  }

  analogWrite(motorLeftPwm, constrain(abs(leftPower), 0, 255));
  analogWrite(motorRightPwm, constrain(abs(rightPower), 0, 255));
}

int directionToPower(const String& direction) {
  if (direction == "0") {
    return motorPower;
  }
  if (direction == "1") {
    return -motorPower;
  }
  if (direction == "3") {
    return motorSlowPower;
  }
  if (direction == "4") {
    return -motorSlowPower;
  }
  return 0;
}

int wheelWordToPower(const String& direction) {
  if (direction.equalsIgnoreCase("forward") || direction.equalsIgnoreCase("f")) {
    return motorPower;
  }
  if (direction.equalsIgnoreCase("backward") || direction.equalsIgnoreCase("back")) {
    return -motorPower;
  }
  if (direction.equalsIgnoreCase("slow-forward") || direction.equalsIgnoreCase("slow")) {
    return motorSlowPower;
  }
  if (direction.equalsIgnoreCase("slow-backward")) {
    return -motorSlowPower;
  }
  return 0;
}

String nextToken(String& text) {
  text.trim();
  const int spaceIndex = text.indexOf(' ');
  if (spaceIndex == -1) {
    String token = text;
    text = "";
    return token;
  }

  String token = text.substring(0, spaceIndex);
  text = text.substring(spaceIndex + 1);
  return token;
}

bool parseMotorCommand(const String& command, String& rightDirection, int& rightTime,
                       String& leftDirection, int& leftTime) {
  String text = command;
  rightDirection = nextToken(text);
  String rightTimeText = nextToken(text);
  leftDirection = nextToken(text);
  String leftTimeText = nextToken(text);
  text.trim();

  if (rightDirection.length() == 0 || rightTimeText.length() == 0 ||
      leftDirection.length() == 0 || leftTimeText.length() == 0 || text.length() > 0) {
    return false;
  }

  if ((rightDirection != "0" && rightDirection != "1" && rightDirection != "2" &&
       rightDirection != "3" && rightDirection != "4") ||
      (leftDirection != "0" && leftDirection != "1" && leftDirection != "2" &&
       leftDirection != "3" && leftDirection != "4")) {
    return false;
  }

  rightTime = rightTimeText.toInt();
  leftTime = leftTimeText.toInt();
  return true;
}

void runMotors(const String& rightDirection, int rightTime,
               const String& leftDirection, int leftTime) {
  const int rightPower = directionToPower(rightDirection);
  const int leftPower = directionToPower(leftDirection);
  const int durationMs = max(rightPower == 0 ? 0 : rightTime, leftPower == 0 ? 0 : leftTime);

  lastMotorCommandMs = millis();
  if (durationMs <= 0) {
    stopMotors();
    return;
  }

  printBoth(String("motor power left=") + leftPower + " right=" + rightPower +
            " duration=" + durationMs + "ms");
  motorWriting(leftPower, rightPower);
  motorStopAtMs = millis() + static_cast<unsigned long>(durationMs);
}

void runSingleWheel(const String& side, const String& direction, unsigned long durationMs) {
  const int power = wheelWordToPower(direction);
  if (power == 0 || durationMs == 0) {
    stopMotors();
    return;
  }

  lastMotorCommandMs = millis();
  if (side.equalsIgnoreCase("right") || side.equalsIgnoreCase("r")) {
    printBoth(String("motor power left=0 right=") + power +
              " duration=" + durationMs + "ms");
    motorWriting(0, power);
  } else if (side.equalsIgnoreCase("left") || side.equalsIgnoreCase("l")) {
    printBoth(String("motor power left=") + power + " right=0 duration=" +
              durationMs + "ms");
    motorWriting(power, 0);
  } else {
    printBoth("Unknown wheel. Use: left or right");
    return;
  }

  motorStopAtMs = millis() + durationMs;
}

int findServoIndex(const String& name) {
  for (size_t index = 0; index < servoCount; ++index) {
    if (name.equalsIgnoreCase(servos[index].name)) {
      return static_cast<int>(index);
    }
  }
  return -1;
}

void writeServoSpeed(size_t index, int speed) {
  ArmServo& armServo = servos[index];
  armServo.speed = constrain(speed, 0, 180);
  armServo.servo.write(armServo.speed);
}

void stopServo(size_t index) {
  servos[index].stopAtMs = 0;
  writeServoSpeed(index, stopSpeed);
}

void setServoAngle(size_t index, int angle) {
  ArmServo& armServo = servos[index];
  armServo.stopAtMs = 0;
  const int targetAngle = constrain(angle, 0, 180);

  if (index == baseIndex) {
    const int step = targetAngle >= armServo.speed ? 1 : -1;
    while (armServo.speed != targetAngle) {
      armServo.speed += step;
      armServo.servo.write(armServo.speed);
      delay(baseStepDelayMs);
    }
  } else {
    armServo.speed = targetAngle;
    armServo.servo.write(armServo.speed);
  }

  printBoth(String(armServo.name) + " = " + angle);
}

void pulseServo(size_t index, int speed, unsigned long durationMs) {
  writeServoSpeed(index, speed);
  servos[index].stopAtMs = millis() + durationMs;
  printBoth(String(servos[index].name) + " pulse " + servos[index].speed + " " + durationMs + "ms");
}

void pulsePair(size_t firstIndex, int firstSpeed, size_t secondIndex, int secondSpeed,
               unsigned long durationMs, const String& label) {
  writeServoSpeed(firstIndex, firstSpeed);
  writeServoSpeed(secondIndex, secondSpeed);
  const unsigned long stopAtMs = millis() + durationMs;
  servos[firstIndex].stopAtMs = stopAtMs;
  servos[secondIndex].stopAtMs = stopAtMs;
  printBoth(label + " pair " + servos[firstIndex].name + "=" + servos[firstIndex].speed +
            " " + servos[secondIndex].name + "=" + servos[secondIndex].speed +
            " " + durationMs + "ms");
}

void reachUp(unsigned long durationMs) {
  (void)durationMs;
  setServoAngle(shoulderIndex, 50);
}

void reachDown(unsigned long durationMs) {
  (void)durationMs;
  setServoAngle(shoulderIndex, 100);
}

void updateTimedStops() {
  const unsigned long now = millis();
  for (size_t index = 0; index < servoCount; ++index) {
    if (servos[index].stopAtMs > 0 && now >= servos[index].stopAtMs) {
      stopServo(index);
      printBoth(String(servos[index].name) + " timed stop");
    }
  }

  if (motorStopAtMs > 0 && now >= motorStopAtMs) {
    stopMotors();
  }

  if (now - lastMotorCommandMs > motorTimeoutMs) {
    stopMotors();
  }
}

void printHelp() {
  printBoth("Mega2560 car + arm controller ready.");
  printBoth("Wheel command: right_direction right_ms left_direction left_ms");
  printBoth("Wheel directions: 0=forward 1=backward 2=stop 3=slow_forward 4=slow_backward");
  printBoth("Single wheel: wheel <left|right> <forward|backward|slow-forward> <ms>");
  printBoth("Arm pins: base=22 shoulder=23 elbow=24 gripper=25");
  printBoth("Arm commands: status, set base <angle>, move <servo> <speed> <ms>");
  printBoth("Arm commands: pair shoulder <speed> elbow <speed> <ms>, reach <dir> <ms>");
  printBoth("Arm commands: open, close, grip <speed> <ms>, stop <servo|all>");
}

void printStatus() {
  printBoth("Wheel pins: R_IN1=6 R_IN2=7 R_PWM=11 L_IN1=8 L_IN2=9 L_PWM=10");
  for (size_t index = 0; index < servoCount; ++index) {
    printBoth(String(servos[index].name) + " pin " + servos[index].pin +
              " speed " + servos[index].speed);
  }
}

void handleArmCommand(String command) {
  String action = nextToken(command);
  action.toLowerCase();

  if (action == "help") {
    printHelp();
    return;
  }
  if (action == "status") {
    printStatus();
    return;
  }
  if (action == "wheel") {
    const String side = nextToken(command);
    const String direction = nextToken(command);
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());
    runSingleWheel(side, direction, durationMs);
    return;
  }
  if (action == "set") {
    const int servoIndex = findServoIndex(nextToken(command));
    if (servoIndex < 0) {
      printBoth("Unknown servo. Use: base, shoulder, elbow, gripper");
      return;
    }
    setServoAngle(static_cast<size_t>(servoIndex), nextToken(command).toInt());
    return;
  }
  if (action == "open") {
    pulseServo(gripperIndex, gripperOpenSpeed, gripperOpenMs);
    return;
  }
  if (action == "close") {
    pulseServo(gripperIndex, gripperCloseSpeed, gripperCloseMs);
    return;
  }
  if (action == "grip") {
    pulseServo(gripperIndex, nextToken(command).toInt(),
               static_cast<unsigned long>(nextToken(command).toInt()));
    return;
  }
  if (action == "move") {
    const int servoIndex = findServoIndex(nextToken(command));
    if (servoIndex < 0) {
      printBoth("Unknown servo. Use: base, shoulder, elbow, gripper");
      return;
    }
    pulseServo(static_cast<size_t>(servoIndex), nextToken(command).toInt(),
               static_cast<unsigned long>(nextToken(command).toInt()));
    return;
  }
  if (action == "pair") {
    const int firstIndex = findServoIndex(nextToken(command));
    const int firstSpeed = nextToken(command).toInt();
    const int secondIndex = findServoIndex(nextToken(command));
    const int secondSpeed = nextToken(command).toInt();
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());

    if (firstIndex < 0 || secondIndex < 0) {
      printBoth("Unknown servo in pair command");
      return;
    }
    pulsePair(static_cast<size_t>(firstIndex), firstSpeed,
              static_cast<size_t>(secondIndex), secondSpeed, durationMs, "custom");
    return;
  }
  if (action == "reach") {
    const String direction = nextToken(command);
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());
    if (direction.equalsIgnoreCase("up")) {
      reachUp(durationMs);
      return;
    }
    if (direction.equalsIgnoreCase("down")) {
      reachDown(durationMs);
      return;
    }
    if (direction.equalsIgnoreCase("forward")) {
      pulsePair(shoulderIndex, armForwardSpeed, elbowIndex, armForwardSpeed, durationMs, "reach forward");
      return;
    }
    if (direction.equalsIgnoreCase("back")) {
      pulsePair(shoulderIndex, armReverseSpeed, elbowIndex, armReverseSpeed, durationMs, "reach back");
      return;
    }
    printBoth("Unknown reach direction. Use: up, down, forward, back");
    return;
  }
  if (action == "stop") {
    String target = nextToken(command);
    if (target.equalsIgnoreCase("all")) {
      stopMotors();
      for (size_t index = 0; index < servoCount; ++index) {
        stopServo(index);
      }
      printBoth("all stop");
      return;
    }

    const int servoIndex = findServoIndex(target);
    if (servoIndex < 0) {
      printBoth("Unknown stop target. Use: base, shoulder, elbow, gripper, all");
      return;
    }
    stopServo(static_cast<size_t>(servoIndex));
    printBoth(String(servos[servoIndex].name) + " stop");
    return;
  }

  printBoth("Unknown command. Type: help");
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  blinkCommandLed();

  String rightDirection;
  String leftDirection;
  int rightTime = 0;
  int leftTime = 0;
  if (parseMotorCommand(command, rightDirection, rightTime, leftDirection, leftTime)) {
    printBoth(String("wheel command: ") + command);
    runMotors(rightDirection, rightTime, leftDirection, leftTime);
    return;
  }

  printBoth(String("arm command: ") + command);
  handleArmCommand(command);
}

void pollStream(Stream& stream, String& buffer) {
  while (stream.available()) {
    const char value = static_cast<char>(stream.read());
    if (value == '\n' || value == '\r') {
      handleCommand(buffer);
      buffer = "";
    } else {
      buffer += value;
    }
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  pinMode(motorRightIn1, OUTPUT);
  pinMode(motorRightIn2, OUTPUT);
  pinMode(motorRightPwm, OUTPUT);
  pinMode(motorLeftIn1, OUTPUT);
  pinMode(motorLeftIn2, OUTPUT);
  pinMode(motorLeftPwm, OUTPUT);
  stopMotors();

  Serial.begin(115200);
  Serial3.begin(115200);

  for (size_t index = 0; index < servoCount; ++index) {
    servos[index].servo.attach(servos[index].pin);
    stopServo(index);
  }

  delay(500);
  printHelp();
}

void loop() {
  pollStream(Serial, usbBuffer);
  pollStream(Serial3, esp32Buffer);
  updateTimedStops();
}
