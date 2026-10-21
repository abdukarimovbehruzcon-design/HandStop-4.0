# ============================================================
#  HandStop v4 — закрывает всё, кроме офиса, когда:
#    • поднял обе руки вверх
#    • ушёл из кадра
#  Ctrl+Alt+H — пауза/возобновление. Q — выход (если превью включено)
# ============================================================

import os, sys, json, time
import cv2
import mediapipe as mp
import psutil
import pyttsx3
import keyboard


# ---------------- Конфиг ----------------
def load_config():
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
           else os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "config.json")
    default = {
        "HOLD_TIME": 1.0,
        "HAND_LOST_GRACE": 3.0,
        "ABSENCE_TIME": 3.0,
        "COOLDOWN": 8.0,
        "GRACE_KILL": 3.0,
        "NOSE_VIS_THRESH": 0.4,
        "SHOW_PREVIEW": True,
        "SPEAK": True,
        "OFFICE_WHITELIST": [
            "winword.exe","excel.exe","powerpnt.exe","outlook.exe","onenote.exe",
            "msaccess.exe","mspub.exe","visio.exe","olk.exe","teams.exe","ms-teams.exe"
        ]
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        default.update(cfg)
    except Exception:
        pass
    return default

CFG = load_config()
HOLD_TIME        = float(CFG["HOLD_TIME"])
HAND_LOST_GRACE  = float(CFG["HAND_LOST_GRACE"])
ABSENCE_TIME     = float(CFG["ABSENCE_TIME"])
COOLDOWN         = float(CFG["COOLDOWN"])
GRACE_KILL       = float(CFG["GRACE_KILL"])
NOSE_VIS_THRESH  = float(CFG["NOSE_VIS_THRESH"])
SHOW_PREVIEW     = bool(CFG["SHOW_PREVIEW"])
SPEAK            = bool(CFG["SPEAK"])
OFFICE_WHITELIST = set(x.lower() for x in CFG["OFFICE_WHITELIST"])

SYSTEM_WHITELIST = {
    "explorer.exe","dwm.exe","winlogon.exe","csrss.exe","svchost.exe",
    "lsass.exe","services.exe","system","registry","smss.exe","wininit.exe",
    "taskhostw.exe","shellexperiencehost.exe","searchhost.exe",
    "startmenuexperiencehost.exe","textinputhost.exe","ctfmon.exe",
    "runtimebroker.exe","handstop.exe","python.exe","pythonw.exe",
}


# ---------------- MediaPipe / TTS ----------------
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(min_detection_confidence=0.5, model_complexity=0)

try:
    tts = pyttsx3.init()
    tts.setProperty("rate", 180)
except Exception:
    tts = None

def say(text):
    if not (SPEAK and tts):
        return
    try:
        tts.say(text)
        tts.runAndWait()
    except Exception:
        pass


# ---------------- Пауза ----------------
PAUSED = False
def toggle_pause():
    global PAUSED
    PAUSED = not PAUSED
    say("Пауза" if PAUSED else "Активно")

try:
    keyboard.add_hotkey("ctrl+alt+h", toggle_pause)
except Exception as e:
    print("Hotkey не зарегистрирован:", e)


# ---------------- Поза ----------------
def classify_pose(lm):
    L = mp_pose.PoseLandmark
    nose, lw, rw = lm[L.NOSE], lm[L.LEFT_WRIST], lm[L.RIGHT_WRIST]

    if nose.visibility < NOSE_VIS_THRESH:
        return "no_body"

    if (lw.visibility > 0.5 and rw.visibility > 0.5
            and lw.y < nose.y and rw.y < nose.y):
        return "both_up"

    l_off = (lw.visibility < 0.35) or (lw.y < 0.06)
    r_off = (rw.visibility < 0.35) or (rw.y < 0.06)
    if l_off and r_off:
        return "gone_top"

    return "down"


# ---------------- Процессы ----------------
def get_visible_pids():
    import win32gui, win32process
    pids = set()
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pids.add(pid)
    win32gui.EnumWindows(cb, None)
    return pids


def close_everything_except_office():
    my_pid  = os.getpid()
    visible = get_visible_pids()
    closed, skipped, targets = [], [], []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid  = proc.info["pid"]
            name = (proc.info["name"] or "").lower()
            if pid == my_pid or pid not in visible: continue
            if name in SYSTEM_WHITELIST:            continue
            if name in OFFICE_WHITELIST:
                skipped.append(name);               continue
            try:
                proc.terminate()
            except Exception:
                pass
            targets.append(proc)
            closed.append(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    time.sleep(GRACE_KILL)

    for proc in targets:
        try:
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                proc.kill()
        except Exception:
            pass

    return closed, skipped


# ---------------- Основной цикл ----------------
def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Камера не найдена.")
        return

    hold_start, last_up_time = None, 0.0
    never_seen   = True
    absent_since = None
    last_trigger = 0.0
    state_display = "-"

    print("HandStop v4 запущен. Ctrl+Alt+H — пауза.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        res = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        now = time.time()

        if res.pose_landmarks:
            status = classify_pose(res.pose_landmarks.landmark)
        else:
            status = "no_body"
        state_display = status

        # --- трекер видимости ---
        if status != "no_body":
            never_seen   = False
            absent_since = None
        else:
            if not never_seen and absent_since is None:
                absent_since = now

        # --- трекер рук ---
        if status == "both_up":
            last_up_time = now
            if hold_start is None:
                hold_start = now
        elif status == "gone_top" and hold_start is not None \
                and (now - last_up_time) < HAND_LOST_GRACE:
            pass  # руки за кадром — продолжаем считать
        else:
            hold_start = None

        # --- пауза ---
        if PAUSED:
            hold_start, absent_since = None, None

        # --- проверка триггеров ---
        reason = ""
        if not PAUSED:
            if (hold_start is not None
                    and (now - hold_start) >= HOLD_TIME
                    and (now - last_trigger) >= COOLDOWN):
                reason = "hands_up"
            elif (absent_since is not None
                    and (now - absent_since) >= ABSENCE_TIME
                    and (now - last_trigger) >= COOLDOWN):
                reason = "absent"

        if reason:
            closed, skipped = close_everything_except_office()
            txt = "Руки вверх" if reason == "hands_up" else "Ты ушёл"
            print(f"[{txt}] Закрыто: {len(closed)} | Office сохранён: {len(set(skipped))}")
            say(f"{txt}. Приложения закрыты. Офисные программы сохранены.")
            hold_start, last_up_time = None, 0.0
            absent_since = None
            last_trigger = now
            never_seen   = False

        # --- HUD ---
        if SHOW_PREVIEW:
            if hold_start is not None:
                prog = min((now - hold_start) / HOLD_TIME, 1.0)
                color = (0, 200, 0) if status == "both_up" else (0, 165, 255)
                cv2.rectangle(frame, (30, 30), (330, 60), (0, 0, 0), -1)
                cv2.rectangle(frame, (30, 30),
                              (30 + int(300 * prog), 60), color, -1)
                cv2.putText(frame, "HANDS UP", (40, 52),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            elif absent_since is not None and not never_seen:
                prog = min((now - absent_since) / ABSENCE_TIME, 1.0)
                cv2.rectangle(frame, (30, 30), (330, 60), (0, 0, 0), -1)
                cv2.rectangle(frame, (30, 30),
                              (30 + int(300 * prog), 60), (80, 80, 255), -1)
                cv2.putText(frame, "ABSENT", (40, 52),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            mode = "PAUSED" if PAUSED else \
                   ("WAITING" if never_seen else "ARMED")
            cv2.putText(frame, f"{mode} | {state_display}", (30, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow("HandStop", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    if SHOW_PREVIEW:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
