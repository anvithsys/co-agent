import win32gui
from pywinauto import Application

hwnd = win32gui.GetForegroundWindow()
app = Application(backend="uia").connect(handle=hwnd)
window = app.window(handle=hwnd)

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

    print(f'"{name}" | {ctrl_type} | x:{x} y:{y}')
