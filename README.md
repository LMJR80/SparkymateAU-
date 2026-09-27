# SparkymateAU — Stage 1

This version connects the SparkymateAU web interface to a real OpenAI API backend.

## Architecture

Browser -> `/api/ask` -> Python/Flask backend -> OpenAI Responses API -> answer

The OpenAI API key is **never placed in the browser**.

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
6. Set your API key:
   - macOS/Linux: `export OPENAI_API_KEY="your-key"`
   - Windows PowerShell: `$env:OPENAI_API_KEY="your-key"`
7. Start:
   `python server.py`
8. Open:
   `http://localhost:8000`

## Important

Do not put an OpenAI API key directly into `public/index.html` or any client-side JavaScript.

Stage 1 intentionally has no Australian standards/document retrieval system yet. It is a real AI connection, but electrical answers still require professional verification.
