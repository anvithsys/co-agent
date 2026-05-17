import json
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ELEMENTS = [
    '"Sign In" | button | x:342 y:891',
    '"Email" | input | x:340 y:650',
]

TASK = "click the email input"

client = Groq(api_key=GROQ_API_KEY)
response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        {
            "role": "system",
            "content": (
                'Pick the element that matches the task. '
                'Reply with JSON only: {"action": "click", "x": <int>, "y": <int>}'
            ),
        },
        {
            "role": "user",
            "content": f"Task: {TASK}\n\nElements:\n" + "\n".join(ELEMENTS),
        },
    ],
    response_format={"type": "json_object"},
    temperature=0,
)

result = json.loads(response.choices[0].message.content)
print(json.dumps(result))
