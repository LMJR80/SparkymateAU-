import os
import time
from collections import defaultdict, deque
from threading import Lock

import psycopg
from flask import Flask, request, jsonify, send_from_directory

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None
    types = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

# Optional proxy fix will be applied based on TRUST_PROXY env var
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
except Exception:  # pragma: no cover
    ProxyFix = None

# Optional Redis for cross-process rate limiting
try:
    import redis
except Exception:  # pragma: no cover
    redis = None

# Create app
app = Flask(__name__, static_folder="public")
# Max content length (bytes). Default to 32 MB. Can be overridden with env MAX_CONTENT_LENGTH.
try:
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 32 * 1024 * 1024))
except ValueError:
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

# Apply ProxyFix when running behind a proxy/load-balancer and TRUST_PROXY is set
if os.environ.get("TRUST_PROXY", "1").lower() in ("1", "true", "yes", "on") and ProxyFix is not None:
    # Trust a single proxy by default (x_for=1, x_proto=1). Adjust via env if needed.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", 20))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", 60))
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

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customers (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_customers_user_id ON customers(user_id);

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    customer_id BIGINT REFERENCES customers(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    site_address TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_customer_id ON jobs(customer_id);

CREATE TABLE IF NOT EXISTS reports (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    customer_name TEXT,
    site_address TEXT,
    job_description TEXT,
    work_performed TEXT,
    test_results TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports(user_id);
CREATE INDEX IF NOT EXISTS idx_reports_job_id ON reports(job_id);

CREATE TABLE IF NOT EXISTS quotes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    customer_id BIGINT REFERENCES customers(id) ON DELETE SET NULL,
    labour_cents BIGINT NOT NULL DEFAULT 0 CHECK (labour_cents >= 0),
    materials_cents BIGINT NOT NULL DEFAULT 0 CHECK (materials_cents >= 0),
    other_cents BIGINT NOT NULL DEFAULT 0 CHECK (other_cents >= 0),
    gst_cents BIGINT NOT NULL DEFAULT 0 CHECK (gst_cents >= 0),
    total_cents BIGINT NOT NULL DEFAULT 0 CHECK (total_cents >= 0),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quotes_user_id ON quotes(user_id);
CREATE INDEX IF NOT EXISTS idx_quotes_job_id ON quotes(job_id);
"""

# Redis client (optional)
_redis_client = None
REDIS_URL = os.environ.get("REDIS_URL")
if REDIS_URL and redis is not None:
    try:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    except Exception:
        _redis_client = None


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(database_url, connect_timeout=10)


def init_db_once(timeout=30):
    """Run the SCHEMA_SQL exactly once across multiple processes using a Postgres advisory lock.
    This prevents multiple gunicorn workers from racing to create tables.
    """
    start = time.time()
    lock_key = 424242  # arbitrary constant chosen for the advisory lock
    while True:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Try to obtain the advisory lock without blocking
                    cur.execute("SELECT pg_try_advisory_lock(%s);", (lock_key,))
                    got = cur.fetchone()[0]
                    if got:
                        app.logger.info("Acquired advisory lock for DB init; creating schema")
                        cur.execute(SCHEMA_SQL)
                        # Release the lock
                        cur.execute("SELECT pg_advisory_unlock(%s);", (lock_key,))
                        app.logger.info("Schema initialization complete; released advisory lock")
                        return
                    else:
                        # somebody else is running init; wait until timeout or schema exists
                        if time.time() - start > timeout:
                            app.logger.info("Timed out waiting for DB init lock; continuing")
                            return
                        time.sleep(0.5)
        except Exception:
            app.logger.exception("Database init attempt failed")
            if time.time() - start > timeout:
                raise
            time.sleep(1)


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limited():
    """Cross-process rate limiter using Redis if available, otherwise in-process memory.
    Uses sliding window via simple counter with expiry.
    """
    ip = client_ip()
    if _redis_client is not None:
        key = f"rate:{ip}"
        try:
            # INCR and set expiry if first seen
            count = _redis_client.incr(key)
            if count == 1:
                _redis_client.expire(key, RATE_LIMIT_WINDOW)
            return int(count) > RATE_LIMIT_REQUESTS
        except Exception:
            # Redis failed; fallback to in-memory
            pass

    # In-memory fallback (per-process)
    now = time.monotonic()
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
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return jsonify(status="ok", service="SparkymateAU", database="ok"), 200
    except Exception:
        app.logger.exception("Database health check failed")
        return jsonify(status="degraded", service="SparkymateAU", database="unavailable"), 503


@app.get("/")
def home():
    return send_from_directory("public", "index.html")


@app.get("/<path:filename>")
def public_file(filename):
    return send_from_directory("public", filename)


def get_openai_answer(message):
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK is not installed")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        input=message,
        instructions=SYSTEM_PROMPT,
        temperature=0.2,
        max_output_tokens=1800,
    )

    if hasattr(response, "output_text") and response.output_text:
        return response.output_text.strip()

    output = getattr(response, "output", None) or []
    parts = []
    for item in output:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(str(text))
    if parts:
        answer = "".join(parts).strip()
        if answer:
            return answer

    if hasattr(response, "choices") and response.choices:
        choice = response.choices[0]
        if hasattr(choice, "message"):
            msg = getattr(choice.message, "content", "")
            if isinstance(msg, list):
                text = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in msg
                )
                if text:
                    return text.strip()
            if isinstance(msg, str) and msg.strip():
                return msg.strip()

    raise RuntimeError("The OpenAI response was empty")


def get_gemini_answer(message):
    if genai is None or types is None:
        raise RuntimeError("Google GenAI SDK is not installed")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        contents=message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    answer = (response.text or "").strip()
    if not answer:
        raise RuntimeError("The Gemini response was empty")
    return answer


def get_ai_answer(message):
    if os.environ.get("OPENAI_API_KEY"):
        return get_openai_answer(message)
    if os.environ.get("GEMINI_API_KEY"):
        return get_gemini_answer(message)
    raise RuntimeError("No AI API key configured")


@app.post("/api/ask")
def ask():
    if rate_limited():
        response = jsonify(error="Too many AI requests. Please wait a moment and try again.")
        response.status_code = 429
        response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW)
        return response

    if not request.is_json:
        return jsonify(error="JSON request required."), 415

    data = request.get_json(silent=True) or {}
    message = data.get("message")
    if not isinstance(message, str):
        return jsonify(error="Message must be text."), 400

    message = message.strip()
    if not message:
        return jsonify(error="Message is required."), 400
    if len(message) > 6000:
        return jsonify(error="Message is too long. Please keep it under 6,000 characters."), 400

    try:
        answer = get_ai_answer(message)
        return jsonify(answer=answer)
    except Exception:
        app.logger.exception("AI request failed")
        return jsonify(error="AI service could not complete the request. Please try again."), 502


# Initialize the database schema safely once across workers
try:
    init_db_once()
    app.logger.info("Database schema ensured")
except Exception:
    app.logger.exception("Database initialization failed")
    raise


if __name__ == "__main__":
    # When running directly (development), start the Flask dev server.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
