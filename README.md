Co-Agent
An AI agent that works alongside you on your computer — without stealing your mouse or keyboard.
What makes it different

No cursor takeover — actions execute through Windows UI Automation directly. Your mouse stays yours.
No screenshots — reads the UI accessibility tree instead. Faster and more reliable than vision-based agents.
No API key setup hell — just one model key. That's it.
Runs locally — your files, your apps, your data. Nothing goes to the cloud.
Works in parallel — you work, agent works. Simultaneously.

How it works

Scans the focused window via Windows UI Automation tree
Sends element names + coordinates to AI as structured text
AI returns a JSON action
Agent invokes the element directly via UIA — no mouse movement
Rescans after every action. Repeats until task is done.

Current capabilities

Click any UI element without moving your cursor
Type into fields without stealing keyboard focus
Multi-step task execution with replanning after each action
Fallback to coordinate clicking when UIA invoke unavailable

Roadmap

 Deep memory layer — AI that actually knows you
 Vision fallback for canvas/custom UIs
 Multi-agent — run 4 agents simultaneously
 Browser layer via Playwright
 Recovery loop — agent detects when stuck and retries intelligently

Stack

Python
pywinauto — UI Automation tree scanning
pyautogui — fallback clicking
Groq — inference
python-dotenv — environment management

Setup
bashpip install pywinauto pyautogui groq python-dotenv
Create .env:
GROQ_API_KEY=your_key_here
Run:
bashpython agent.py
