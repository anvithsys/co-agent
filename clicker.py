import pyautogui

pyautogui.FAILSAFE = True

x = int(input())
y = int(input())

pyautogui.click(x, y)
print(f"clicked at ({x}, {y})")
