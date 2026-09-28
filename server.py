import os
import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

import psycopg
from flask import (
    Flask,
    jsonify,
    request,
    send_from_directory,
    session,
)
from google import genai
from google.genai import types
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__, static_folder="public")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is not configured")

app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)


RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW = 60

AUTH_LIMIT_REQUESTS = 10
AUTH_LIMIT_WINDOW = 300

request_history = defaultdict(deque)
auth_history = defaultdict(deque)
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
CREATE INDEX IF NOT EXISTS idx_customers_user_id
ON customers(user_id);

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
CREATE INDEX IF NOT EXISTS idx_jobs_user_id
ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_customer_id
ON jobs(customer_id);

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
CREATE INDEX IF NOT EXISTS idx_reports_user_id
ON reports(user_id);
CREATE INDEX IF NOT EXISTS idx_reports_job_id
ON reports(job_id);

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
CREATE INDEX IF NOT EXISTS idx_quotes_user_id
ON quotes(user_id);
CREATE INDEX IF NOT EXISTS idx_quotes_job_id
ON quotes(job_id);
"""def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(database_url, connect_timeout=10)


def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def limited(history_store, limit, window):
    now = time.monotonic()
    ip = client_ip()

    with rate_lock:
        history = history_store[ip]

        while history and now - history[0] >= window:
            history.popleft()

        if len(history) >= limit:
            return True

        history.append(now)
        return False


def rate_limited():
    return limited(
        request_history,
        RATE_LIMIT_REQUESTS,
        RATE_LIMIT_WINDOW,
    )


def auth_rate_limited():
    return limited(
        auth_history,
        AUTH_LIMIT_REQUESTS,
        AUTH_LIMIT_WINDOW,
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")

        if not user_id:
            return jsonify(error="Authentication required."), 401

        return view(*args, **kwargs)

    return wrapped


def clean_text(value, maximum=5000):
    if value is None:
        return ""

    if not isinstance(value, str):
        raise ValueError("Text fields must contain text.")

    value = value.strip()

    if len(value) > maximum:
        raise ValueError(
            f"Text field exceeds {maximum} characters."
        )

    return value


def valid_email(value):
    if not isinstance(value, str):
        return None

    email = value.strip().lower()

    if (
        len(email) < 5
        or len(email) > 254
        or "@" not in email
        or email.startswith("@")
        or email.endswith("@")
    ):
        return None

    return email


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = (
        "strict-origin-when-cross-origin"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=()"
    )

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

        return jsonify(
            status="ok",
            service="SparkymateAU",
            database="ok",
        ), 200

    except Exception:
        app.logger.exception("Database health check failed")

        return jsonify(
            status="degraded",
            service="SparkymateAU",
            database="unavailable",
        ), 503


@app.get("/")
def home():
    return send_from_directory("public", "index.html")


@app.get("/<path:filename>")
def public_file(filename):
    return send_from_directory("public", filename)


@app.post("/api/auth/register")
def register():
    if auth_rate_limited():
        response = jsonify(
            error="Too many account attempts. Please wait and try again."
        )
        response.status_code = 429
        response.headers["Retry-After"] = "300"
        return response

    if not request.is_json:
        return jsonify(error="JSON request required."), 415

    data = request.get_json(silent=True) or {}
    email = valid_email(data.get("email"))
    password = data.get("password")

    if not email:
        return jsonify(error="Enter a valid email address."), 400

    if not isinstance(password, str) or len(password) < 10:
        return jsonify(
            error="Password must be at least 10 characters."
        ), 400

    if len(password) > 200:
        return jsonify(error="Password is too long."), 400

    password_hash = generate_password_hash(
        password,
        method="scrypt",
    )

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (email, password_hash),
                )
                user_id = cur.fetchone()[0]

        session.clear()
        session.permanent = True
        session["user_id"] = user_id
        session["email"] = email

        return jsonify(
            ok=True,
            user={"id": user_id, "email": email},
        ), 201

    except psycopg.errors.UniqueViolation:
        return jsonify(
            error="An account with that email already exists."
        ), 409

    except Exception:
        app.logger.exception("Registration failed")
        return jsonify(
            error="Account could not be created."
        ), 500@app.post("/api/auth/login")
def login():
    if auth_rate_limited():
        response = jsonify(
            error="Too many login attempts. Please wait and try again."
        )
        response.status_code = 429
        response.headers["Retry-After"] = "300"
        return response

    if not request.is_json:
        return jsonify(error="JSON request required."), 415

    data = request.get_json(silent=True) or {}
    email = valid_email(data.get("email"))
    password = data.get("password")

    if not email or not isinstance(password, str):
        return jsonify(error="Invalid email or password."), 401

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, email, password_hash
                    FROM users
                    WHERE email = %s
                    """,
                    (email,),
                )
                user = cur.fetchone()

        if not user or not check_password_hash(
            user[2],
            password,
        ):
            return jsonify(
                error="Invalid email or password."
            ), 401

        session.clear()
        session.permanent = True
        session["user_id"] = user[0]
        session["email"] = user[1]

        return jsonify(
            ok=True,
            user={
                "id": user[0],
                "email": user[1],
            },
        )

    except Exception:
        app.logger.exception("Login failed")
        return jsonify(
            error="Login could not be completed."
        ), 500


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/auth/session")
def auth_session():
    user_id = session.get("user_id")
    email = session.get("email")

    if not user_id:
        return jsonify(
            authenticated=False,
            user=None,
        )

    return jsonify(
        authenticated=True,
        user={
            "id": user_id,
            "email": email,
        },
    )


@app.get("/api/customers")
@login_required
def list_customers():
    user_id = session["user_id"]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        name,
                        phone,
                        email,
                        address,
                        notes,
                        created_at
                    FROM customers
                    WHERE user_id = %s
                    ORDER BY created_at DESC@app.post("/api/jobs")
@login_required
def create_job():
    if not request.is_json:
        return jsonify(error="JSON request required."), 415

    data = request.get_json(silent=True) or {}

    try:
        title = clean_text(data.get("title"), 200)
        site_address = clean_text(
            data.get("site_address"),
            500,
        )
        description = clean_text(
            data.get("description"),
            5000,
        )
        status = clean_text(
            data.get("status") or "open",
            50,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    if not title:
        return jsonify(error="Job title is required."), 400

    allowed_statuses = {
        "open",
        "in_progress",
        "completed",
        "cancelled",
    }

    if status not in allowed_statuses:
        return jsonify(error="Invalid job status."), 400

    customer_id = data.get("customer_id")

    if customer_id in ("", None):
        customer_id = None
    else:
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            return jsonify(
                error="Invalid customer."
            ), 400

        if customer_id <= 0:
            return jsonify(
                error="Invalid customer."
            ), 400

    user_id = session["user_id"]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if customer_id is not None:
                    cur.execute(
                        """
                        SELECT 1
                        FROM customers
                        WHERE id = %s
                          AND user_id = %s
                        """,
                        (customer_id, user_id),
                    )

                    if not cur.fetchone():
                        return jsonify(
                            error="Customer not found."
                        ), 404

                cur.execute(
                    """
                    INSERT INTO jobs (
                        user_id,
                        customer_id,
                        title,
                        site_address,
                        description,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING
                        id,
                        created_at,
                        updated_at
                    """,
                    (
                        user_id,
                        customer_id,
                        title,
                        site_address or None,
                        description or None,
                        status,
                    ),
                )
                row = cur.fetchone()

        return jsonify(
            job={
                "id": row[0],
                "customer_id": customer_id,
                "title": title,
                "site_address": site_address,
                "description": description,
                "status": status,
                "created_at": row[1].isoformat(),
                "updated_at": row[2].isoformat(),
            }
        ), 201

    except Exception:
        app.logger.exception("Job creation failed")
        return jsonify(
            error="Job could not be saved."
        ), 500


@app.post("/api/ask")
def ask():
    if rate_limited():
        response = jsonify(
            error=(
                "Too many AI requests. "
                "Please wait a moment and            )
        )
        response.status_code = 429
        response.headers["Retry-After"] = "60"
        return response

    if not request.is_json:
        return jsonify(error="JSON request required."), 415

    data = request.get_json(silent=True) or {}
    message = data.get("message")

    if not isinstance(message, str):
        return jsonify(
            error="Message must be text."
        ), 400

    message = message.strip()

    if not message:
        return jsonify(
            error="Message is required."
        ), 400

    if len(message) > 6000:
        return jsonify(
            error=(
                "Message is too long. "
                "Please keep it under 6,000 characters."
            )
        ), 400

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        app.logger.error(
            "GEMINI_API_KEY is not configured"
        )
        return jsonify(
            error="AI service is temporarily unavailable."
        ), 503

    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=os.environ.get(
                "GEMINI_MODEL",
                "gemini-2.5-flash-lite",
            ),
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )

        answer = (response.text or "").strip()

        if not answer:
            return jsonify(
                error=(
                    "The AI returned an empty response. "
                    "Please try again."
                )
            ), 502

        return jsonify(answer=answer)

    except Exception:
        app.logger.exception(
            "Gemini request failed"
        )
        return jsonify(
            error=(
                "AI service could not complete "
                "the request. Please try again."
            )
        ), 502


try:
    init_db()
    app.logger.info("Database schema is ready")

except Exception:
    app.logger.exception(
        "Database initialization failed"
    )
    raise


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
