#include <Arduino.h>
#include <Servo.h>

/*
* MeArm continuous-servo control test on Mega 2560
* Connect servos to pins 8-11, and use Serial or Serial3 to send commands.
* Commands:
  help
  status
  set base <angle>
  move <shoulder|elbow|gripper> <speed> <milliseconds>
  pair shoulder <speed> elbow <speed> <milliseconds>
  reach <up|down|forward|back> <milliseconds>
  stop <servo>
  stop all
  open
  close
  grip <speed> <milliseconds>
* Example: set base 60
* Speed: 90 stops, farther from 90 is faster.
*/

// struct to hold servo information and state
struct ArmServo {
  const char* name;
  uint8_t pin;
  int speed;
  unsigned long stopAtMs;
  Servo servo;
};

// define the servos with their names and pins
ArmServo servos[] = {
  {"base", 11, 90, 0},
  {"shoulder", 10, 90, 0},
  {"elbow", 9, 90, 0},
  {"gripper", 8, 90, 0},
};

// constants for servo control
constexpr size_t servoCount = sizeof(servos) / sizeof(servos[0]); // index constants for easier reference
constexpr size_t baseIndex = 0;                                   // base uses angle control, so speed is not directly used for it
constexpr size_t shoulderIndex = 1;                               // shoulder and elbow can be controlled together for coordinated movement, so they have some predefined speeds for reach commands
constexpr size_t elbowIndex = 2;                                  // gripper can be opened and closed with predefined speeds and durations, or controlled with custom speed and duration
constexpr size_t gripperIndex = 3;                                // stopSpeed is the speed that stops the servo, and the other speeds are defined relative to it
constexpr int stopSpeed = 90;                                     // for continuous rotation servos, 90 is typically the stop position, less than 90 is one direction, and greater than 90 is the other direction
constexpr unsigned long baseStepDelayMs = 15;                     // delay between speed steps for base angle control, to make it smoother and avoid skipping the target angle
constexpr int armForwardSpeed = 150;                              // these speeds are chosen to be reasonably fast but not too fast for the arm to handle, and can be adjusted as needed
constexpr int armReverseSpeed = 30;                               // gripper speeds and durations are defined for simple open and close commands, but can also be customized with the grip command
constexpr int armSlowReverseSpeed = 60;
constexpr int gripperOpenSpeed = 65;                              // these values can be adjusted based on the specific servos used and the desired speed of opening and closing
constexpr int gripperCloseSpeed = 120;                            // gripper speeds should be on opposite sides of the stopSpeed (90) to ensure they move in opposite directions
constexpr unsigned long gripperOpenMs = 300;                      // gripper duration can be adjusted based on the specific servos and the desired speed of opening and closing
constexpr unsigned long gripperCloseMs = 400;                     // buffers for incoming serial data from USB and ESP32

String usbBuffer;
String esp32Buffer;
unsigned long messageSequence = 0;

void printBoth(const String& text) {
  ++messageSequence;
  String line = "#";
  line += messageSequence;
  line += " ";
  line += text;
  Serial.println(line);
  Serial3.println(line);
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

  String line = armServo.name;
  line += " = ";
  line += armServo.speed;
  printBoth(line);
}

void pulseServo(size_t index, int speed, unsigned long durationMs) {
  writeServoSpeed(index, speed);
  servos[index].stopAtMs = millis() + durationMs;

  String line = servos[index].name;
  line += " pulse ";
  line += servos[index].speed;
  line += " ";
  line += durationMs;
  line += "ms";
  printBoth(line);
}

void pulsePair(size_t firstIndex, int firstSpeed, size_t secondIndex, int secondSpeed,
               unsigned long durationMs, const String& label) {
  writeServoSpeed(firstIndex, firstSpeed);
  writeServoSpeed(secondIndex, secondSpeed);
  const unsigned long stopAtMs = millis() + durationMs;
  servos[firstIndex].stopAtMs = stopAtMs;
  servos[secondIndex].stopAtMs = stopAtMs;

  String line = label;
  line += " pair ";
  line += servos[firstIndex].name;
  line += "=";
  line += servos[firstIndex].speed;
  line += " ";
  line += servos[secondIndex].name;
  line += "=";
  line += servos[secondIndex].speed;
  line += " ";
  line += durationMs;
  line += "ms";
  printBoth(line);
}

void updateTimedStops() {
  const unsigned long now = millis();
  for (size_t index = 0; index < servoCount; ++index) {
    if (servos[index].stopAtMs > 0 && now >= servos[index].stopAtMs) {
      stopServo(index);
      printBoth(String(servos[index].name) + " timed stop");
    }
  }
}

void printHelp() {
  printBoth("");
  printBoth("MeArm continuous-servo control ready.");
  printBoth("Pins: base=11 shoulder=10 elbow=9 gripper=8");
  printBoth("Commands:");
  printBoth("  help");
  printBoth("  status");
  printBoth("  set base <angle>");
  printBoth("  move <shoulder|elbow|gripper> <speed> <milliseconds>");
  printBoth("  pair shoulder <speed> elbow <speed> <milliseconds>");
  printBoth("  reach <up|down|forward|back> <milliseconds>");
  printBoth("  stop <servo>");
  printBoth("  stop all");
  printBoth("  open");
  printBoth("  close");
  printBoth("  grip <speed> <milliseconds>");
  printBoth("Example: set base 60");
  printBoth("Example: move shoulder 60 400");
  printBoth("Speed: 90 stops, farther from 90 is faster.");
  printBoth("");
}

void printStatus() {
  printBoth("Current servo speeds:");
  for (size_t index = 0; index < servoCount; ++index) {
    String line = "  ";
    line += servos[index].name;
    line += " pin ";
    line += servos[index].pin;
    line += " speed ";
    line += servos[index].speed;
    printBoth(line);
  }
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

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  printBoth(String("ACK command: ") + command);

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

  if (action == "set") {
    String servoName = nextToken(command);
    if (!servoName.equalsIgnoreCase("base")) {
      printBoth("Only base angle control is enabled now. Use: set base <angle>");
      return;
    }

    const int angle = nextToken(command).toInt();
    setServoAngle(baseIndex, angle);
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
    const int speed = nextToken(command).toInt();
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());
    pulseServo(gripperIndex, speed, durationMs);
    return;
  }

  if (action == "move") {
    String servoName = nextToken(command);
    const int servoIndex = findServoIndex(servoName);
    if (servoIndex < 0) {
      printBoth("Unknown servo. Use: base, shoulder, elbow, gripper");
      return;
    }
    if (static_cast<size_t>(servoIndex) == baseIndex) {
      printBoth("Base uses angle control now. Use: set base <angle>");
      return;
    }

    const int speed = nextToken(command).toInt();
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());
    pulseServo(static_cast<size_t>(servoIndex), speed, durationMs);
    return;
  }

  if (action == "pair") {
    const String firstName = nextToken(command);
    const int firstIndex = findServoIndex(firstName);
    const int firstSpeed = nextToken(command).toInt();
    const String secondName = nextToken(command);
    const int secondIndex = findServoIndex(secondName);
    const int secondSpeed = nextToken(command).toInt();
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());

    if (firstIndex < 0 || secondIndex < 0) {
      printBoth("Unknown servo in pair command");
      return;
    }

    pulsePair(static_cast<size_t>(firstIndex), firstSpeed,
              static_cast<size_t>(secondIndex), secondSpeed,
              durationMs, "custom");
    return;
  }

  if (action == "reach") {
    const String direction = nextToken(command);
    const unsigned long durationMs = static_cast<unsigned long>(nextToken(command).toInt());

    if (direction.equalsIgnoreCase("up")) {
      pulsePair(shoulderIndex, armReverseSpeed, elbowIndex, armSlowReverseSpeed, durationMs, "reach up");
      return;
    }
    if (direction.equalsIgnoreCase("down")) {
      pulsePair(shoulderIndex, armForwardSpeed, elbowIndex, armForwardSpeed, durationMs, "reach down");
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
    String servoName = nextToken(command);
    if (servoName.equalsIgnoreCase("all")) {
      for (size_t index = 0; index < servoCount; ++index) {
        stopServo(index);
      }
      printBoth("all stop");
      return;
    }

    const int servoIndex = findServoIndex(servoName);
    if (servoIndex < 0) {
      printBoth("Unknown servo. Use: base, shoulder, elbow, gripper, or all");
      return;
    }

    stopServo(static_cast<size_t>(servoIndex));
    printBoth(String(servos[servoIndex].name) + " stop");
    return;
  }

  printBoth("Unknown command. Type: help");
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
  Serial.begin(115200);
  Serial3.begin(115200);

  for (size_t index = 0; index < servoCount; ++index) {
    servos[index].servo.attach(servos[index].pin);
    stopServo(index);
  }

  delay(500);
  printHelp();
  printBoth("Ready. Use move/grip with speed and time.");
}

void loop() {
  pollStream(Serial, usbBuffer);
  pollStream(Serial3, esp32Buffer);
  updateTimedStops();
}
