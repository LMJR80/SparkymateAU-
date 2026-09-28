import os
from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

app = Flask(__name__, static_folder="public")

SYSTEM_PROMPT = """You are SparkymateAU, an AI assistant designed for Australian electricians.
Help users structure troubleshooting, calculations, job documentation and customer explanations.
Be practical, concise and explicit about assumptions.
Do not claim a test was performed, a component is safe, or a requirement applies unless the user has supplied the evidence/source.
For electrical work, prioritise safety: isolation/testing procedures should be performed by appropriately licensed/qualified people using suitable equipment.
Do not invent clauses, standards numbers, legal requirements, cable ratings or test results.
When a question depends on current Australian standards, state that the applicable current requirement/source must be checked.
This Stage 1 service has no standards database and must not be treated as authoritative electrical advice.
Use plain text only. Do not use Markdown, headings, asterisks, dollar-sign math delimiters, LaTeX, or backslash commands. Write calculations in simple readable form such as: Current = 3200 W / 240 V = 13.33 A.
"""

@app.get("/")
def home():
    return send_from_directory("public", "index.html")

@app.get("/<path:filename>")
def public_file(filename):
    return send_from_directory("public", filename)

@app.post("/api/ask")
def ask():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify(error="Message is required."), 400

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify(error="GEMINI_API_KEY is not configured on the server."), 500

    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT
            ),
        )

        return jsonify(answer=response.text)

    except Exception as exc:
        app.logger.exception("Gemini request failed")
        return jsonify(error="AI request failed.", detail=str(exc)), 500

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000"))
    )
