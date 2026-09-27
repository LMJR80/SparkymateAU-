import os
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

app = Flask(__name__, static_folder="public")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = """You are SparkymateAU, an AI assistant designed for Australian electricians.
Help users structure troubleshooting, calculations, job documentation and customer explanations.
Be practical, concise and explicit about assumptions.
Do not claim a test was performed, a component is safe, or a requirement applies unless the user has supplied the evidence/source.
For electrical work, prioritise safety: isolation/testing procedures should be performed by appropriately licensed/qualified people using suitable equipment.
Do not invent clauses, standards numbers, legal requirements, cable ratings or test results.
When a question depends on current Australian standards, state that the applicable current requirement/source must be checked. In later stages SparkymateAU will use a licensed/current knowledge base.
This Stage 1 service has no standards database and must not be treated as authoritative electrical advice.
"""

@app.get("/")
def home():
    return send_from_directory("public", "index.html")

@app.post("/api/ask")
def ask():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify(error="Message is required."), 400
    if not os.environ.get("OPENAI_API_KEY"):
        return jsonify(error="OPENAI_API_KEY is not configured on the server."), 500
    try:
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
            instructions=SYSTEM_PROMPT,
            input=message,
        )
        return jsonify(answer=response.output_text)
    except Exception as exc:
        app.logger.exception("OpenAI request failed")
        return jsonify(error="AI request failed.", detail=str(exc)), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
