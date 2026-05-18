import json
import os
import re
import time

import pyautogui
import win32con
import win32gui
from dotenv import load_dotenv
from groq import Groq, RateLimitError
from pywinauto import Application, findwindows

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

pyautogui.FAILSAFE = True
client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile"
MAX_UI_LINES = 60

SYSTEM = (
    "You control ONE desktop app (named in each message). Think using that app's menus. "
    "Work ONLY the CURRENT step. One UI action per turn. "
    'Always json with why, goal, step (int). Set step_complete:true when CURRENT step is done in UI. '
    'click:{"action":"click","x":n,"y":n,"why":"...","goal":"...","step":n,"step_complete":false} '
    'type:{"action":"type","text":"...","why":"...","goal":"...","step":n,"step_complete":false} '
    'wait:{"action":"wait","seconds":n,"why":"...","goal":"...","step":n} '
    'done:{"action":"done","why":"...","goal":"..."} only when ALL steps done. '
    "Add problem if stuck. goal must name the step. xy from UI list only. "
    "NEW file tasks: step 1 must be New tab/New — never Save as on an old tab first."
)


def find_window(query):
    pattern = f"(?i).*{re.escape(query.strip())}.*"
    handles = findwindows.find_windows(title_re=pattern, top_level_only=True)
    if not handles:
        raise ValueError(f'No window matching "{query}"')
    return handles[0]


def focus_window(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)


def scan_elements(hwnd):
    app = Application(backend="uia").connect(handle=hwnd)
    window = app.window(handle=hwnd)
    elements = []
    for elem in window.descendants():
        name = (elem.element_info.name or elem.window_text() or "").strip()
        if not name:
            continue
        try:
            rect = elem.rectangle()
        except Exception:
            continue
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        t = str(elem.element_info.control_type or "?").lower()[:6]
        elements.append(f"{name}|{t}|{x},{y}")
    return elements


def validate_action(result):
    a = result.get("action")
    if a == "click" and ("x" not in result or "y" not in result):
        return "click needs x,y"
    if a == "type" and "text" not in result:
        return "type needs text"
    if a == "wait" and "seconds" not in result:
        return "wait needs seconds"
    if a not in ("click", "type", "wait", "done"):
        return f"bad action: {a}"
    return None


def action_summary(result):
    note = result.get("goal") or result.get("why") or ""
    suffix = f" — {note}" if note else ""
    a = result.get("action")
    if a == "click":
        return f"click {result['x']},{result['y']}{suffix}"
    if a == "type":
        text = result["text"]
        if len(text) > 50:
            return f'type "{text[:50]}..."{suffix}'
        return f'type "{text}"{suffix}'
    if a == "wait":
        return f"wait {result['seconds']}s{suffix}"
    return json.dumps(result)


def is_stuck_on_click(click_coords):
    if len(click_coords) < 3:
        return False
    x, y = click_coords[-1]
    return click_coords[-3:] == [(x, y), (x, y), (x, y)]


def do_scan(window_query):
    scan_note = None
    elements = []
    hwnd = find_window(window_query)
    app_name = win32gui.GetWindowText(hwnd) or window_query
    try:
        focus_window(hwnd)
        elements = scan_elements(hwnd)
        if not elements:
            scan_note = "empty scan"
    except Exception as e:
        scan_note = str(e)
    return elements, scan_note, app_name


def elements_at(elements, x, y):
    suffix = f"|{x},{y}"
    return [line for line in elements if line.endswith(suffix)]


def target_name(line):
    return line.split("|")[0] if line else "?"


def format_steps(steps, current_step):
    lines = []
    for i, step in enumerate(steps, 1):
        mark = "  ← CURRENT" if i == current_step else ""
        lines.append(f"{i}. {step}{mark}")
    return "Steps:\n" + "\n".join(lines)


def print_plan(app_name, steps, current_step):
    print("--- Plan ---")
    print(f"App: {app_name}")
    print(format_steps(steps, current_step))
    print("------------")


def log_ai(result, elements, scan_note=None, current_step=None):
    action = result.get("action", "?")
    why = (result.get("why") or "").strip()
    goal = (result.get("goal") or "").strip()
    problem = (result.get("problem") or "").strip() or (scan_note or "")
    step = result.get("step", current_step)

    print("--- AI ---")
    if step:
        print(f"Step: {step}")
    if action == "click":
        x, y = int(result["x"]), int(result["y"])
        matches = elements_at(elements, x, y)
        name = target_name(matches[0]) if matches else f"{x},{y}"
        print(f"Action: click → {name} ({x},{y})")
    elif action == "type":
        text = result.get("text", "")
        preview = text if len(text) <= 60 else text[:60] + "..."
        print(f'Action: type → "{preview}"')
    elif action == "wait":
        print(f"Action: wait {result.get('seconds')}s")
    elif action == "done":
        print("Action: done")
    else:
        print(f"Action: {action}")

    if goal:
        print(f"Goal: {goal}")
    if why:
        print(f"Why: {why}")
    if problem:
        print(f"Problem: {problem}")
    print("------------")


def print_scan(elements, scan_note):
    print("--- scan ---")
    if elements:
        for line in elements:
            print(line)
    else:
        print("(none)")
    if scan_note:
        print(f"!{scan_note}")
    print("------------")


def build_messages(task, app_name, steps, current_step, history, click_coords, elements, scan_note):
    parts = [
        f"App: {app_name} (you control this app only)",
        f"Task: {task}",
        format_steps(steps, current_step),
    ]
    if history:
        parts.append("History:\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(history)))
    if click_coords:
        parts.append(
            "Clicks: " + " → ".join(f"({x},{y})" for x, y in click_coords)
        )
    ui = "UI now:\n" + ("\n".join(elements[:MAX_UI_LINES]) if elements else "-")
    if len(elements) > MAX_UI_LINES:
        ui += f"\n...+{len(elements) - MAX_UI_LINES}"
    parts.append(ui)
    if scan_note:
        parts.append(f"!{scan_note}")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def parse_rate_limit_wait(error):
    m = re.search(r"try again in (\d+)m([\d.]+)s", str(error))
    if m:
        return int(m.group(1)) * 60 + float(m.group(2)) + 1
    return 60


def groq_json(messages):
    while True:
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
        except RateLimitError as e:
            wait = parse_rate_limit_wait(e)
            print(f"Rate limit — wait {wait:.0f}s...")
            time.sleep(wait)


def plan_task(task, app_name):
    messages = [
        {
            "role": "system",
            "content": (
                "Return json only. Break the task into ordered steps for the named app. "
                'Format: {"steps":["short step 1","step 2",...]}. '
                "If user wants a NEW file: step 1 MUST be create new file/tab (not open, not save). "
                "Save/rename only after new file exists. Be literal — do not skip steps."
            ),
        },
        {"role": "user", "content": f"App: {app_name}\nTask: {task}"},
    ]
    data = json.loads(groq_json(messages).choices[0].message.content)
    steps = data.get("steps") or [task]
    return [str(s) for s in steps]


while True:
    task = input("What do you want to do? ")
    if task.strip().lower() == "stop":
        break

    window_query = input("Which window? (type part of the window title) ")
    history = []
    click_coords = []
    elements, scan_note, app_name = do_scan(window_query)
    steps = plan_task(task, app_name)
    current_step = 1
    print_plan(app_name, steps, current_step)
    print_scan(elements, scan_note)

    while True:
        messages = build_messages(
            task, app_name, steps, current_step, history, click_coords, elements, scan_note
        )
        result = json.loads(groq_json(messages).choices[0].message.content)
        log_ai(result, elements, scan_note, current_step)

        if result.get("action") == "done":
            if current_step < len(steps):
                print(
                    f"--- AI ---\nProblem: done too early — still on step {current_step}/{len(steps)}\n------------"
                )
                history.append("fail: done before all steps")
            else:
                break

        action = result.get("action")
        err = validate_action(result)
        if not err:
            try:
                if action == "click":
                    x, y = int(result["x"]), int(result["y"])
                    pyautogui.click(x, y)
                    click_coords.append((x, y))
                elif action == "type":
                    pyautogui.typewrite(result["text"], interval=0.05)
                elif action == "wait":
                    time.sleep(float(result["seconds"]))
            except Exception as e:
                err = str(e)

        if err:
            print(f"--- AI ---\nProblem: invalid action — {err}\n------------")
            history.append(f"fail: {err}")
        else:
            history.append(action_summary(result))
            if result.get("step_complete") and current_step < len(steps):
                current_step += 1
                print_plan(app_name, steps, current_step)
            if action == "click" and is_stuck_on_click(click_coords):
                print("agent stuck")
                break

        time.sleep(0.3)
        elements, scan_note, app_name = do_scan(window_query)
        print_scan(elements, scan_note)
