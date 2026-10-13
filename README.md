# SparkymateAU — Stage 1

This version connects the SparkymateAU web interface to a real AI backend.

## Architecture

Browser -> `/api/ask` -> Python/Flask backend -> OpenAI or Gemini -> answer

The API key is never placed in the browser.

## Run locally

1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Create a virtual environment:
   `python -m venv .venv`
4. Activate it:
   - Windows: `.venv\Scripts\activate`
   - macOS/Linux: `source .venv/bin/activate`
5. Install packages:
   `pip install -r requirements.txt`
6. Set an AI API key:
   - For OpenAI/ChatGPT:
     - macOS/Linux: `export OPENAI_API_KEY="your-key"`
     - Windows PowerShell: `$env:OPENAI_API_KEY="your-key"`
   - For Gemini:
     - macOS/Linux: `export GEMINI_API_KEY="your-key"`
     - Windows PowerShell: `$env:GEMINI_API_KEY="your-key"`
7. Optional: choose a model:
   - `export OPENAI_MODEL="gpt-4o-mini"`
   - `export GEMINI_MODEL="gemini-2.5-flash-lite"`
8. Start:
   `python server.py`
9. Open:
   `http://localhost:8000`

## Important

Do not put an API key directly into `public/index.html` or any client-side JavaScript.

Stage 1 intentionally has no Australian standards/document retrieval system yet. It is a real AI connection, but electrical answers still require professional verification.
