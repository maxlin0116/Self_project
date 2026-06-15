import argparse
import time

import requests


DEFAULT_IP = "10.71.160.168"
REQUEST_TIMEOUT_SECONDS = 6.0
MOVE_CONFIRM_TIMEOUT_SECONDS = 8.0

SERVOS = ("base", "shoulder", "elbow", "gripper")
TIME_SERVOS = ("base", "shoulder", "elbow", "gripper")
GRIPPER_OPEN_ANGLE = 60
GRIPPER_CLOSE_ANGLE = 120
GRIPPER_OPEN_MS = 400
GRIPPER_CLOSE_MS = 400
MIN_SERVO_PULSE_US = 500
MAX_SERVO_PULSE_US = 2500


def esp32_url(ip, path):
    return f"http://{ip}{path}"


def create_session():
    session = requests.Session()
    session.trust_env = False
    return session


def post_command(session, ip, command):
    response = session.post(
        esp32_url(ip, "/data"),
        data=command.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def read_mega_log(session, ip):
    response = session.get(
        esp32_url(ip, "/mega-log"),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.text.strip()


def log_message(log):
    parts = log.split(" ", 1)
    if len(parts) == 2 and parts[0].startswith("#") and parts[0][1:].isdigit():
        return parts[1]
    return log


def expected_completion(command):
    parts = command.split()
    if len(parts) == 4 and parts[0].lower() == "wheel":
        return f"arm command: {command}"
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return f"wheel command: {command}"
    if len(parts) == 1 and parts[0].lower() == "open":
        return "gripper timed stop"
    if len(parts) == 1 and parts[0].lower() == "close":
        return "gripper timed stop"
    if len(parts) == 3 and parts[0].lower() == "grip":
        return "gripper timed stop"
    if len(parts) == 2 and parts[0].lower() == "stop" and parts[1].lower() == "gripper":
        return "gripper stop"
    if len(parts) == 2 and parts[0].lower() == "stop" and parts[1].lower() == "all":
        return "all stop"
    if len(parts) == 2 and parts[0].lower() == "stop" and parts[1].lower() in SERVOS:
        return f"{parts[1].lower()} stop"
    if len(parts) == 3 and parts[0].lower() == "set" and parts[1].lower() in SERVOS:
        return f"{parts[1].lower()} = {parts[2]}"
    if len(parts) == 3 and parts[0].lower() == "us" and parts[1].lower() in SERVOS:
        return f"{parts[1].lower()} pulse_us = {parts[2]}"
    if len(parts) == 4 and parts[0].lower() == "move" and parts[1].lower() in TIME_SERVOS:
        return f"{parts[1].lower()} timed stop"
    if len(parts) == 6 and parts[0].lower() == "pair":
        return f"{parts[3].lower()} timed stop"
    if len(parts) in (2, 3) and parts[0].lower() == "reach":
        if parts[1].lower() == "up":
            return "shoulder = 50"
        if parts[1].lower() == "down":
            return "shoulder = 100"
        return "elbow timed stop"
    return None


def send_only(session, ip, command):
    post_command(session, ip, command)
    print(f"Sent: {command}")


def send_and_confirm(session, ip, command):
    previous_log = ""
    try:
        previous_log = read_mega_log(session, ip)
    except requests.exceptions.RequestException:
        pass

    post_command(session, ip, command)
    expected = expected_completion(command)
    deadline = time.monotonic() + MOVE_CONFIRM_TIMEOUT_SECONDS
    last_log = ""
    saw_new_log = expected is None

    while True:
        time.sleep(0.25)
        log = read_mega_log(session, ip)
        last_log = log
        if log != previous_log:
            saw_new_log = True
        if expected is None or (saw_new_log and log_message(log) == expected):
            print(f"Mega: {log}")
            return log
        message = log_message(log)
        if message.startswith("Ready.") or message == "Center targets set.":
            print(f"Mega: {log}")
            print("Warning: Mega appears to have reset before the move completed.")
            return last_log
        if time.monotonic() >= deadline:
            print(f"Mega: {log}")
            print(f"Warning: did not see expected completion: {expected}")
            return last_log


def send_command(session, ip, command, confirm):
    jog_command = parse_jog_command(command)
    if jog_command is not None:
        servo, speed, duration_ms = jog_command
        return send_jog_command(session, ip, servo, speed, duration_ms, confirm)

    if confirm:
        return send_and_confirm(session, ip, command)
    send_only(session, ip, command)
    return None


def clamp_servo_angle(servo, angle):
    return max(0, min(180, angle))


def parse_jog_command(command):
    parts = command.split()
    if len(parts) != 4 or parts[0].lower() != "jog":
        return None

    servo = parts[1].lower()
    if servo not in SERVOS:
        return None

    speed = clamp_servo_angle(servo, int(parts[2]))
    duration_ms = max(0, int(parts[3]))
    return servo, speed, duration_ms


def send_jog_command(session, ip, servo, speed, duration_ms, confirm):
    start_command = f"set {servo} {speed}"
    stop_command = f"set {servo} 90"

    if confirm:
        send_and_confirm(session, ip, start_command)
    else:
        send_only(session, ip, start_command)

    try:
        time.sleep(duration_ms / 1000.0)
    finally:
        if confirm:
            return send_and_confirm(session, ip, stop_command)
        send_only(session, ip, stop_command)
    return None


def clamp_servo_pulse_us(pulse_us):
    return max(MIN_SERVO_PULSE_US, min(MAX_SERVO_PULSE_US, pulse_us))


def print_help():
    print()
    print("Commands:")
    print("  wheel <left|right> <forward|backward|slow-forward|slow-backward> <milliseconds>")
    print("  <right_direction> <right_ms> <left_direction> <left_ms>")
    print("  status")
    print("  set <base|shoulder|elbow|gripper> <angle>")
    print("  us <base|shoulder|elbow|gripper> <pulse_us>")
    print("  jog <base|shoulder|elbow|gripper> <speed> <milliseconds>")
    print("  move <base|shoulder|elbow|gripper> <speed> <milliseconds>")
    print("  pair shoulder <speed> elbow <speed> <milliseconds>")
    print("  reach <up|down|forward|back> <milliseconds>")
    print("  stop <servo>")
    print("  stop all")
    print("  open")
    print("  close")
    print("  grip <speed> <milliseconds>")
    print("  stop gripper")
    print("  log")
    print("  help")
    print("  quit")
    print()
    print("Wheels:")
    print("  Word command: wheel left forward 300, wheel right backward 300")
    print("  Number command: 0=forward, 1=backward, 2=stop, 3=slow_forward, 4=slow_backward")
    print("  Examples: 0 300 0 300 = both forward, 2 0 0 300 = left only forward")
    print()
    print("Servos: base, shoulder, elbow, gripper")
    print("Speed: 90 stops, farther from 90 is faster")
    print("Examples: set shoulder 60, us shoulder 1500, jog shoulder 120 400")
    print("Pairs: pair shoulder 120 elbow 60 400, reach up 400")
    print("Angles: 0-180")
    print()


def run_interactive(ip, confirm):
    session = create_session()
    print(f"ESP32: http://{ip}")
    if not confirm:
        print("No-confirm mode: commands are sent without waiting for Mega log.")
    print_help()

    def send(command):
        return send_command(session, ip, command, confirm)

    while True:
        try:
            raw = input("arm> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        parts = raw.split()
        action = parts[0].lower()

        try:
            if action in ("quit", "exit"):
                break

            if action == "help":
                print_help()
                continue

            if action == "log":
                print(f"Mega: {read_mega_log(session, ip)}")
                continue

            if action == "status":
                send("status")
                continue

            if action == "wheel" and len(parts) == 4:
                side = parts[1].lower()
                direction = parts[2].lower()
                if side not in ("left", "right", "l", "r"):
                    print("Wheel must be left or right.")
                    continue
                if direction not in ("forward", "f", "backward", "back", "slow-forward", "slow", "slow-backward"):
                    print("Direction must be forward, backward, slow-forward, or slow-backward.")
                    continue
                duration_ms = max(0, int(parts[3]))
                send(f"wheel {side} {direction} {duration_ms}")
                continue

            if len(parts) == 4 and all(part.isdigit() for part in parts):
                right_direction = parts[0]
                left_direction = parts[2]
                if right_direction not in ("0", "1", "2", "3", "4") or left_direction not in ("0", "1", "2", "3", "4"):
                    print("Wheel directions must be 0, 1, 2, 3, or 4.")
                    continue
                right_ms = max(0, int(parts[1]))
                left_ms = max(0, int(parts[3]))
                send(f"{right_direction} {right_ms} {left_direction} {left_ms}")
                continue

            if action == "open":
                send("open")
                continue

            if action == "close":
                send("close")
                continue

            if action == "grip" and len(parts) == 3:
                speed = clamp_servo_angle("gripper", int(parts[1]))
                duration_ms = max(0, int(parts[2]))
                send(f"grip {speed} {duration_ms}")
                continue

            if action == "set" and len(parts) == 3:
                servo = parts[1].lower()
                if servo not in SERVOS:
                    print("Unknown servo. Use: base, shoulder, elbow, or gripper")
                    continue

                angle = clamp_servo_angle(servo, int(parts[2]))
                send(f"set {servo} {angle}")
                continue

            if action == "us" and len(parts) == 3:
                servo = parts[1].lower()
                if servo not in SERVOS:
                    print("Unknown servo. Use: base, shoulder, elbow, or gripper")
                    continue

                pulse_us = clamp_servo_pulse_us(int(parts[2]))
                send(f"us {servo} {pulse_us}")
                continue

            if action == "jog" and len(parts) == 4:
                servo = parts[1].lower()
                if servo not in SERVOS:
                    print("Unknown servo. Use: base, shoulder, elbow, or gripper")
                    continue

                speed = clamp_servo_angle(servo, int(parts[2]))
                duration_ms = max(0, int(parts[3]))
                send(f"jog {servo} {speed} {duration_ms}")
                continue

            if action == "move" and len(parts) == 4:
                servo = parts[1].lower()
                if servo not in TIME_SERVOS:
                    print("Unknown servo. Use: base, shoulder, elbow, or gripper")
                    continue

                speed = clamp_servo_angle(servo, int(parts[2]))
                duration_ms = max(0, int(parts[3]))
                send(f"move {servo} {speed} {duration_ms}")
                continue

            if action == "pair" and len(parts) == 6:
                first = parts[1].lower()
                second = parts[3].lower()
                if first not in SERVOS or second not in SERVOS:
                    print("Unknown servo in pair command.")
                    continue
                first_speed = clamp_servo_angle(first, int(parts[2]))
                second_speed = clamp_servo_angle(second, int(parts[4]))
                duration_ms = max(0, int(parts[5]))
                send(f"pair {first} {first_speed} {second} {second_speed} {duration_ms}")
                continue

            if action == "reach" and len(parts) in (2, 3):
                direction = parts[1].lower()
                if direction not in ("up", "down", "forward", "back"):
                    print("Direction must be up, down, forward, or back.")
                    continue
                if direction in ("forward", "back") and len(parts) != 3:
                    print(f"Use: reach {direction} <milliseconds>")
                    continue
                duration_ms = max(0, int(parts[2])) if len(parts) == 3 else 0
                send(f"reach {direction} {duration_ms}")
                continue

            if action == "stop" and len(parts) == 2:
                servo = parts[1].lower()
                if servo != "all" and servo not in SERVOS:
                    print("Unknown servo. Use: base, shoulder, elbow, gripper, or all")
                    continue
                send(f"stop {servo}")
                continue

            print("Unknown command. Type: help")
        except ValueError:
            print("Angle and delta must be numbers.")
        except requests.exceptions.RequestException as error:
            print(f"Request failed: {error}")


def parse_args():
    parser = argparse.ArgumentParser(description="Control the MeArm over ESP32 Wi-Fi.")
    parser.add_argument("--ip", default=DEFAULT_IP, help="ESP32 IP address")
    parser.add_argument("--command", help="Send one command and exit")
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Send commands without waiting for Mega log feedback",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    session = create_session()

    if args.command:
        try:
            send_command(session, args.ip, args.command, not args.no_confirm)
        except requests.exceptions.RequestException as error:
            raise SystemExit(f"Request failed: {error}")
        return

    run_interactive(args.ip, not args.no_confirm)


if __name__ == "__main__":
    main()
