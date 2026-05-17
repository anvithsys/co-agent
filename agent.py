import json
import os
import time

import pyautogui
import win32gui
from dotenv import load_dotenv
from groq import Groq
from pywinauto import Application

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

pyautogui.FAILSAFE = True
client = Groq(api_key=GROQ_API_KEY)


def scan_elements():
    hwnd = win32gui.GetForegroundWindow()
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
        ctrl_type = str(elem.element_info.control_type or "element").lower()
        elements.append(f'"{name}" | {ctrl_type} | x:{x} y:{y}')
    return elements


def get_actions(result):
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("actions"), list):
            return result["actions"]
        if "x" in result and "y" in result:
            return [result]
    raise ValueError(f"Unexpected Groq response: {result}")


while True:
    elements = scan_elements()
    task = input("What do you want to do? ")
    if task.strip().lower() == "stop":
        break

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": (
                    'Pick element(s) for the task. '
                    'One click: {"action": "click", "x": <int>, "y": <int>}. '
                    'Multiple clicks: {"actions": [{"action": "click", "x": <int>, "y": <int>}, ...]}'
                ),
            },
            {
                "role": "user",
                "content": f"Task: {task}\n\nElements:\n" + "\n".join(elements),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    result = json.loads(response.choices[0].message.content)
    actions = get_actions(result)
    for i, action in enumerate(actions):
        pyautogui.click(action["x"], action["y"])
        print(f"clicked at ({action['x']}, {action['y']})")
        if i < len(actions) - 1:
            time.sleep(0.5)
