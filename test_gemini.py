"""Step 0 smoke test: confirm GEMINI_API_KEY works. Run: uv run python test_gemini.py"""

import os

from dotenv import load_dotenv
from google import genai

from worker.config import MODEL

load_dotenv()

key = os.getenv("GEMINI_API_KEY")
if not key:
    raise SystemExit("GEMINI_API_KEY is empty — fill it in .env first.")

client = genai.Client(api_key=key)
interaction = client.interactions.create(
    model=MODEL,
    input="Reply with one short sentence confirming you are working.",
)
print(interaction.output_text)
