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
    "You control one desktop app. Execute ONLY the current step. One action per turn.\n\n"
    "Rules:\n"
    "- Use ONLY elements from the UI list provided. Never guess coordinates.\n"
    "- Never perform steps the user didn't ask for.\n"
    "- If current step is complete, set step_complete:true.\n"
    "- If stuck 2 turns in a row, set action:done and explain why in problem field.\n"
    "- After each action you see a fresh UI in the next message. If that UI already shows the "
    "current step done (e.g. File menu items listed), do not repeat the same click: use "
    "step_complete:true on the last plan step, or action:done if that was the only/final step.\n"
    "- When Last successful click is present, do not click that exact (x,y) again unless the "
    "current UI clearly shows the prior click failed or the UI reverted.\n"
    "- When all plan steps are satisfied, respond with action:done (problem empty or brief) "
    "and do not ask for more clicks.\n"
    "- For type: x,y must come from a docume or edit line in the UI list (e.g. Text editor). "
    "Never use (0,0). Set append:true when adding to text already in the editor.\n\n"
    "Return JSON only:\n"
    'click: {"action":"click","element_name":"...","x":n,"y":n,"step":n,"step_complete":false,"why":"..."}\n'
    'type: {"action":"type","element_name":"...","x":n,"y":n,"text":"...","append":true,"step":n,"step_complete":false,"why":"..."}\n'
    'done: {"action":"done","why":"...","problem":"..."}'
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
        en = (result.get("element_name") or "").strip()
        x, y = result["x"], result["y"]
        if en:
            return f'click "{en}" at ({x},{y}){suffix}'
        return f"click {x},{y}{suffix}"
    if a == "type":
        text = result["text"]
        en = (result.get("element_name") or "").strip()
        x, y = result.get("x"), result.get("y")
        if en and x is not None and y is not None:
            preview = text if len(text) <= 50 else text[:50] + "..."
            return f'type "{preview}" into "{en}" at ({x},{y}){suffix}'
        preview = text if len(text) <= 50 else text[:50] + "..."
        return f'type "{preview}"{suffix}'
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
    hwnd = None
    app_name = window_query
    try:
        hwnd = find_window(window_query)
        app_name = win32gui.GetWindowText(hwnd) or window_query
    except Exception as e:
        return elements, str(e), window_query, None

    try:
        elements = scan_elements(hwnd)
        if not elements:
            scan_note = "empty scan"
    except Exception as e:
        scan_note = str(e)
    return elements, scan_note, app_name, hwnd


def _center(rect):
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def find_uia_wrapper_at(hwnd, x, y, elements):
    """Match UIA wrapper to scan line: same center; prefer name from elements_at."""
    want_name = None
    lines = elements_at(elements, x, y)
    if lines:
        want_name = target_name(lines[0])

    app = Application(backend="uia").connect(handle=hwnd)
    root = app.window(handle=hwnd)
    candidates = []
    for elem in root.descendants():
        name = (elem.element_info.name or elem.window_text() or "").strip()
        if not name:
            continue
        try:
            rect = elem.rectangle()
        except Exception:
            continue
        cx, cy = _center(rect)
        if cx != x or cy != y:
            continue
        candidates.append((elem, name))

    if not candidates:
        return None
    if want_name:
        for elem, name in candidates:
            if name == want_name:
                return elem
        for elem, name in candidates:
            if want_name in name or name in want_name:
                return elem
    return candidates[0][0]


def invoke_uia_or_fallback(hwnd, x, y, elements):
    """
    Prefer UIA Invoke (no mouse move). Fallback pyautogui.click at (x,y) if invoke fails
    or UIA is unavailable. Logs which method was used; returns \"UIA invoke\" or \"fallback click\".
    """
    x, y = int(x), int(y)
    if hwnd is None:
        pyautogui.click(x, y)
        print("  fallback click")
        return "fallback click"

    wrapper = find_uia_wrapper_at(hwnd, x, y, elements)
    if wrapper is None:
        pyautogui.click(x, y)
        print("  fallback click")
        return "fallback click"

    try:
        wrapper.invoke()
        print("  UIA invoke")
        return "UIA invoke"
    except Exception:
        pyautogui.click(x, y)
        print("  fallback click")
        return "fallback click"


def _is_editable_scan_line(line):
    parts = line.split("|")
    if len(parts) < 3:
        return False
    return parts[1].lower().startswith("edit") or parts[1].lower().startswith("docume")


def _valid_type_coords(x, y, elements):
    if x is None or y is None:
        return False
    x, y = int(x), int(y)
    if x <= 0 and y <= 0:
        return False
    lines = elements_at(elements, x, y)
    return any(_is_editable_scan_line(line) for line in lines)


def _has_text_pattern(wrapper):
    try:
        wrapper.iface_text
        return True
    except Exception:
        return False


def _set_text_pattern(wrapper, text, append=False):
    if not _has_text_pattern(wrapper):
        return False
    try:
        iface = wrapper.iface_text
        doc = iface.DocumentRange
        if append:
            end = doc.Clone()
            end.MoveEndpointByRange(0, doc, 1)
            end.Collapse(True)
            end.InsertText(text, False)
        else:
            target = doc.Clone()
            target.ExpandToEnclosingUnit(2)
            target.DeleteContent()
            target.InsertText(text, False)
        return True
    except Exception:
        return False


def _has_value_pattern(wrapper):
    try:
        wrapper.iface_value
        return True
    except Exception:
        pass
    try:
        props = wrapper.legacy_properties()
        return bool(props) and not props.get("IsReadOnly", False)
    except Exception:
        return False


def _set_value_pattern(wrapper, text, append=False):
    if not _has_value_pattern(wrapper):
        return False
    try:
        if append:
            try:
                current = wrapper.window_text() or ""
            except Exception:
                current = wrapper.iface_value.CurrentValue or ""
            pos = len(current)
            wrapper.set_edit_text(text, pos_start=pos, pos_end=pos)
        else:
            wrapper.set_edit_text(text)
        return True
    except Exception:
        pass
    try:
        if append:
            current = wrapper.iface_value.CurrentValue or ""
            wrapper.iface_value.SetValue(current + text)
        else:
            wrapper.iface_value.SetValue(text)
        return True
    except Exception:
        return False


def _default_type_target_line(elements):
    for line in elements:
        if _is_editable_scan_line(line) and line.split("|")[1].lower().startswith("docume"):
            return line
    for line in elements:
        if _is_editable_scan_line(line):
            return line
    return None


def find_type_target(hwnd, elements, result):
    """Resolve the UIA wrapper to type into: explicit xy/name, else main edit/document."""
    if hwnd is None:
        return None

    x, y = result.get("x"), result.get("y")
    if _valid_type_coords(x, y, elements):
        return find_uia_wrapper_at(hwnd, int(x), int(y), elements)

    want_name = (result.get("element_name") or "").strip()
    if want_name:
        app = Application(backend="uia").connect(handle=hwnd)
        root = app.window(handle=hwnd)
        partial = None
        for elem in root.descendants():
            name = (elem.element_info.name or elem.window_text() or "").strip()
            if not name:
                continue
            if name == want_name:
                return elem
            if want_name in name or name in want_name:
                partial = partial or elem
        if partial:
            return partial

    line = _default_type_target_line(elements)
    if line:
        cx, cy = line.split("|")[2].split(",")
        return find_uia_wrapper_at(hwnd, int(cx), int(cy), elements)
    return None


def set_text_uia_or_fallback(hwnd, elements, text, result):
    """
    Type via UIA only (ValuePattern or TextPattern). Does not focus the window or
    use the keyboard — safe while the user works in another app.
    """
    if hwnd is None:
        raise RuntimeError("no window handle — cannot type without a UIA target")

    wrapper = find_type_target(hwnd, elements, result)
    if wrapper is None:
        raise RuntimeError(
            "no editable target — use Text editor|docume or edit x,y from the UI list (not 0,0)"
        )

    append = result.get("append")
    if append is None:
        append = True
    else:
        append = bool(append)

    if _set_value_pattern(wrapper, text, append=append):
        print("  UIA ValuePattern")
        return "UIA ValuePattern"

    if _set_text_pattern(wrapper, text, append=append):
        print("  UIA TextPattern")
        return "UIA TextPattern"

    raise RuntimeError("UIA typing failed — element has no ValuePattern or TextPattern")


def elements_at(elements, x, y):
    suffix = f"|{x},{y}"
    return [line for line in elements if line.endswith(suffix)]


def target_name(line):
    return line.split("|")[0] if line else "?"


def format_last_click(elements, x, y, method, result):
    """Human-readable line for the model: what was clicked, where, and how."""
    matches = elements_at(elements, x, y)
    scan_name = target_name(matches[0]) if matches else None
    model_name = (result.get("element_name") or "").strip() or None
    if model_name and scan_name and model_name != scan_name:
        name = f'{scan_name} (element_name {model_name!r})'
    elif scan_name:
        name = scan_name
    elif model_name:
        name = model_name
    else:
        name = "unknown control"
    return f"{name} at ({x},{y}), {method}"


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
        x, y = result.get("x"), result.get("y")
        if x is not None and y is not None:
            matches = elements_at(elements, int(x), int(y))
            name = target_name(matches[0]) if matches else f"{x},{y}"
            print(f'Action: type → "{preview}" into {name} ({x},{y})')
        else:
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


def build_messages(
    task, app_name, steps, current_step, history, click_coords, elements, scan_note, last_click_summary
):
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
    if last_click_summary:
        parts.append(
            "Last successful click: "
            + last_click_summary
            + "\nDo not use that same (x,y) again unless the current UI shows it failed or reverted."
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
                'Return JSON only: {"steps":["step 1","step 2",...]}\n'
                "Rules:\n"
                "- Be literal to user's words. No invented steps.\n"
                "- Never add save/rename unless user asked.\n"
                '- Never add "create new file" unless user explicitly wants a new document.\n'
                '- Keep steps minimal. If user says "click File menu" — one step only.'
            ),
        },
        {"role": "user", "content": f"App: {app_name}\nTask: {task}"},
    ]
    data = json.loads(groq_json(messages).choices[0].message.content)
    steps = data.get("steps") or [task]
    return [str(s) for s in steps]


def main():
    while True:
        task = input("What do you want to do? ")
        if task.strip().lower() == "stop":
            break

        window_query = input("Which window? (type part of the window title) ")
        history = []
        click_coords = []
        elements, scan_note, app_name, hwnd = do_scan(window_query)
        steps = plan_task(task, app_name)
        current_step = 1
        print_plan(app_name, steps, current_step)
        print_scan(elements, scan_note)

        last_click_summary = None
        while True:
            messages = build_messages(
                task,
                app_name,
                steps,
                current_step,
                history,
                click_coords,
                elements,
                scan_note,
                last_click_summary,
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
                    print("--- done ---")
                    break

            action = result.get("action")
            err = validate_action(result)
            if not err:
                try:
                    if action == "click":
                        x, y = int(result["x"]), int(result["y"])
                        method = invoke_uia_or_fallback(hwnd, x, y, elements)
                        click_coords.append((x, y))
                        last_click_summary = format_last_click(elements, x, y, method, result)
                    elif action == "type":
                        set_text_uia_or_fallback(hwnd, elements, result["text"], result)
                    elif action == "wait":
                        time.sleep(float(result["seconds"]))
                except Exception as e:
                    err = str(e)

            if err:
                print(f"--- AI ---\nProblem: invalid action — {err}\n------------")
                history.append(f"fail: {err}")
            else:
                history.append(action_summary(result))
                if result.get("step_complete"):
                    if current_step < len(steps):
                        current_step += 1
                        print_plan(app_name, steps, current_step)
                    else:
                        print("--- done ---")
                        break
                if action == "click" and is_stuck_on_click(click_coords):
                    print("agent stuck")
                    break

            time.sleep(0.3)
            elements, scan_note, app_name, hwnd = do_scan(window_query)
            print_scan(elements, scan_note)


if __name__ == "__main__":
    main()
