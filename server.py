import os
import time
from collections import defaultdict, deque
from threading import Lock

from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

app = Flask(__name__, static_folder="public")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024

RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW = 60
request_history = defaultdict(deque)
rate_lock = Lock()

SYSTEM_PROMPT = """You are SparkymateAU, an AI assistant designed for Australian electricians.
Help users structure troubleshooting, calculations, job documentation and customer explanations.
Be practical, concise and explicit about assumptions.
Do not claim a test was performed, a component is safe, or a requirement applies unless the user has supplied the evidence/source.
For electrical work, prioritise safety: isolation/testing procedures should be performed by appropriately licensed/qualified people using suitable equipment.
Do not invent clauses, standards numbers, legal requirements, cable ratings or test results.
When a question depends on current Australian standards, state that the applicable current requirement/source must be checked.
SparkymateAU does not currently contain a licensed standards database and must not be treated as authoritative electrical or compliance advice.
Use plain text only. Do not use Markdown, headings, asterisks, dollar-sign math delimiters, LaTeX, or backslash commands.
Write calculations in simple readable form such as: Current = 3200 W / 240 V = 13.33 A.
"""


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limited():
    now = time.monotonic()
    ip = client_ip()

    with rate_lock:
        history = request_history[ip]

        while history and now - history[0] >= RATE_LIMIT_WINDOW:
            history.popleft()

        if len(history) >= RATE_LIMIT_REQUESTS:
            return True

        history.append(now)
        return False


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=()"

    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"

    return response


@app.errorhandler(413)
def request_too_large(_):
    return jsonify(error="Request is too large."), 413


@app.get("/health")
def health():
    return jsonify(status="ok", service="SparkymateAU"), 200


@app.get("/")
def home():
    return send_from_directory("public", "index.html")


@app.get("/<path:filename>")
def public_file(filename):
    return send_from_directory("public", filename)


@app.post("/api/ask")
def ask():
    if rate_limited():
        response = jsonify(
            error="Too many AI requests. Please wait a moment and try again."
        )
        response.status_code =
