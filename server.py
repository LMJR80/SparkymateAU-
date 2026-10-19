import os
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg
from flask import Flask, request, jsonify, send_from_directory, session

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
app.secret_key = os.environ.get("APP_SECRET_KEY") or None
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() in ("1", "true", "yes", "on"),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
)
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

MARKETPLACE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS marketplace_users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('customer', 'electrician')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS marketplace_jobs (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES marketplace_users(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    suburb TEXT NOT NULL,
    state TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'cancelled')),
    electrician_id BIGINT REFERENCES marketplace_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_marketplace_jobs_open ON marketplace_jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_jobs_customer ON marketplace_jobs(customer_id, created_at DESC);

CREATE TABLE IF NOT EXISTS marketplace_subscriptions (
    user_id BIGINT PRIMARY KEY REFERENCES marketplace_users(id) ON DELETE CASCADE,
    stripe_customer_id TEXT UNIQUE,
    stripe_subscription_id TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'inactive',
    current_period_end BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
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
                        cur.execute(MARKETPLACE_SCHEMA_SQL)
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


PASSWORD_ITERATIONS = 310_000
AU_STATES = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}


def marketplace_user(user_id=None):
    user_id = user_id or session.get("marketplace_user_id")
    if not user_id:
        return None
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, role FROM marketplace_users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    return {"id": row[0], "email": row[1], "role": row[2]} if row else None


def marketplace_ready():
    if not app.secret_key:
        return jsonify(error="Marketplace setup is incomplete: APP_SECRET_KEY is missing."), 503
    return None


def same_origin_request():
    origin = request.headers.get("Origin")
    if not origin:
        return False
    parsed = urlparse(origin)
    if parsed.netloc != request.host or parsed.scheme not in ("http", "https"):
        return False
    return not request.is_secure or parsed.scheme == "https"


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_hash_is_valid(password_hash):
    return len(password_hash) < 256


def has_active_membership(user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM marketplace_subscriptions
                   WHERE user_id = %s AND status IN ('active', 'trialing')
                   AND (current_period_end IS NULL OR current_period_end > EXTRACT(EPOCH FROM NOW()))""",
                (user_id,),
            )
            return cur.fetchone() is not None


@app.get("/api/marketplace/me")
def marketplace_me():
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    user = marketplace_user()
    if not user:
        session.pop("marketplace_user_id", None)
        return jsonify(user=None), 200
    user["membership"] = "active" if user["role"] == "electrician" and has_active_membership(user["id"]) else "inactive"
    return jsonify(user=user), 200


@app.post("/api/marketplace/register")
def marketplace_register():
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    if not same_origin_request():
        return jsonify(error="Request origin could not be verified."), 403
    if rate_limited():
        return jsonify(error="Too many account requests. Wait a minute and try again."), 429
    data = request.get_json(silent=True) or {}
    email = data.get("email", "")
    password = data.get("password", "")
    role = data.get("role", "")
    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify(error="Enter a valid email and password."), 400
    email = email.strip().lower()
    if len(email) > 254 or "@" not in email or email.startswith("@") or email.endswith("@"):
        return jsonify(error="Enter a valid email address."), 400
    if len(password) < 12 or len(password) > 256:
        return jsonify(error="Use a password between 12 and 256 characters."), 400
    if role not in ("customer", "electrician"):
        return jsonify(error="Choose customer or electrician."), 400
    encoded = hash_password(password)
    if not password_hash_is_valid(encoded):
        return jsonify(error="Unable to create this account."), 400
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO marketplace_users (email, password_hash, role) VALUES (%s, %s, %s) RETURNING id, email, role",
                    (email, encoded, role),
                )
                row = cur.fetchone()
    except psycopg.errors.UniqueViolation:
        return jsonify(error="An account with that email already exists. Sign in instead."), 409
    session.clear()
    session.permanent = True
    session["marketplace_user_id"] = row[0]
    return jsonify(user={"id": row[0], "email": row[1], "role": row[2], "membership": "inactive"}), 201


@app.post("/api/marketplace/login")
def marketplace_login():
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    if not same_origin_request():
        return jsonify(error="Request origin could not be verified."), 403
    if rate_limited():
        return jsonify(error="Too many sign-in attempts. Wait a minute and try again."), 429
    data = request.get_json(silent=True) or {}
    email = data.get("email", "")
    password = data.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify(error="Email or password is incorrect."), 401
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, role, password_hash FROM marketplace_users WHERE email = %s",
                (email.strip().lower(),),
            )
            row = cur.fetchone()
    if not row or not verify_password(password, row[3]):
        return jsonify(error="Email or password is incorrect."), 401
    session.clear()
    session.permanent = True
    session["marketplace_user_id"] = row[0]
    membership = "active" if row[2] == "electrician" and has_active_membership(row[0]) else "inactive"
    return jsonify(user={"id": row[0], "email": row[1], "role": row[2], "membership": membership}), 200


@app.post("/api/marketplace/logout")
def marketplace_logout():
    if not same_origin_request():
        return jsonify(error="Request origin could not be verified."), 403
    session.clear()
    return jsonify(ok=True), 200


@app.route("/api/marketplace/jobs", methods=["GET", "POST"])
def marketplace_jobs():
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    user = marketplace_user()
    if not user:
        return jsonify(error="Sign in to use the job board."), 401

    if request.method == "POST":
        if not same_origin_request():
            return jsonify(error="Request origin could not be verified."), 403
        if rate_limited():
            return jsonify(error="Too many job requests. Wait a minute and try again."), 429
        if user["role"] != "customer":
            return jsonify(error="Only customer accounts can post jobs."), 403
        data = request.get_json(silent=True) or {}
        job_type = data.get("job_type", "")
        suburb = data.get("suburb", "")
        state = data.get("state", "")
        description = data.get("description", "")
        if not all(isinstance(value, str) for value in (job_type, suburb, state, description)):
            return jsonify(error="Complete each job field with text."), 400
        job_type, suburb, state, description = job_type.strip(), suburb.strip(), state.strip().upper(), description.strip()
        if not job_type or len(job_type) > 100 or not suburb or len(suburb) > 120:
            return jsonify(error="Enter a job type and suburb."), 400
        if state not in AU_STATES or not description or len(description) > 4000:
            return jsonify(error="Choose a state and enter job details (up to 4,000 characters)."), 400
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO marketplace_jobs (customer_id, job_type, suburb, state, description)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id, job_type, suburb, state, description, status, created_at""",
                    (user["id"], job_type, suburb, state, description),
                )
                row = cur.fetchone()
        return jsonify(job={"id": row[0], "job_type": row[1], "suburb": row[2], "state": row[3], "description": row[4], "status": row[5], "created_at": row[6].isoformat()}), 201

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if user["role"] == "customer":
                cur.execute(
                    """SELECT j.id, j.job_type, j.suburb, j.state, j.description, j.status, j.created_at,
                              CASE WHEN j.status = 'accepted' THEN e.email ELSE NULL END
                       FROM marketplace_jobs j LEFT JOIN marketplace_users e ON e.id = j.electrician_id
                       WHERE j.customer_id = %s ORDER BY j.created_at DESC LIMIT 100""",
                    (user["id"],),
                )
            else:
                cur.execute(
                    """SELECT j.id, j.job_type, j.suburb, j.state, j.description, j.status, j.created_at,
                              CASE WHEN j.status = 'accepted' THEN c.email ELSE NULL END
                       FROM marketplace_jobs j JOIN marketplace_users c ON c.id = j.customer_id
                       WHERE j.status = 'open' OR (j.status = 'accepted' AND j.electrician_id = %s)
                       ORDER BY j.created_at DESC LIMIT 100""",
                    (user["id"],),
                )
            rows = cur.fetchall()
    jobs = [{"id": r[0], "job_type": r[1], "suburb": r[2], "state": r[3], "description": r[4], "status": r[5], "created_at": r[6].isoformat(), "contact_email": r[7]} for r in rows]
    return jsonify(jobs=jobs), 200


@app.post("/api/marketplace/jobs/<int:job_id>/accept")
def marketplace_accept_job(job_id):
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    if not same_origin_request():
        return jsonify(error="Request origin could not be verified."), 403
    if rate_limited():
        return jsonify(error="Too many requests. Wait a minute and try again."), 429
    user = marketplace_user()
    if not user:
        return jsonify(error="Sign in to accept a job."), 401
    if user["role"] != "electrician":
        return jsonify(error="Only electrician accounts can accept jobs."), 403
    if not has_active_membership(user["id"]):
        return jsonify(error="An active electrician subscription is required to accept jobs.", subscription_required=True), 402
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE marketplace_jobs SET status = 'accepted', electrician_id = %s, accepted_at = NOW()
                   WHERE id = %s AND status = 'open' RETURNING id""",
                (user["id"], job_id),
            )
            row = cur.fetchone()
    if not row:
        return jsonify(error="That job is no longer available."), 409
    return jsonify(ok=True, job_id=row[0]), 200


@app.post("/api/marketplace/subscription/checkout")
def marketplace_subscription_checkout():
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    if not same_origin_request():
        return jsonify(error="Request origin could not be verified."), 403
    if rate_limited():
        return jsonify(error="Too many checkout requests. Wait a minute and try again."), 429
    user = marketplace_user()
    if not user:
        return jsonify(error="Sign in as an electrician first."), 401
    if user["role"] != "electrician":
        return jsonify(error="Only electrician accounts can subscribe."), 403
    if has_active_membership(user["id"]):
        return jsonify(error="Your membership is already active. Use Manage membership to change or cancel it."), 409
    secret = os.environ.get("STRIPE_SECRET_KEY")
    price_id = os.environ.get("STRIPE_PRICE_ID")
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not secret or not price_id or not base_url:
        return jsonify(error="Membership checkout is not configured yet."), 503
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stripe_customer_id FROM marketplace_subscriptions WHERE user_id = %s", (user["id"],))
            row = cur.fetchone()
    form = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": base_url + "/?membership=success#jobs",
        "cancel_url": base_url + "/?membership=cancelled#jobs",
        "client_reference_id": str(user["id"]),
        "metadata[marketplace_user_id]": str(user["id"]),
        "subscription_data[metadata][marketplace_user_id]": str(user["id"]),
    }
    if row and row[0]:
        form["customer"] = row[0]
    else:
        form["customer_email"] = user["email"]
    body = urlencode(form).encode("utf-8")
    req = Request(
        "https://api.stripe.com/v1/checkout/sessions",
        data=body,
        headers={"Authorization": "Bearer " + secret, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("url"):
            raise ValueError("Stripe did not return a checkout URL")
        return jsonify(url=payload["url"]), 200
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        app.logger.exception("Stripe Checkout session creation failed")
        return jsonify(error="Could not start membership checkout. Please try again later."), 502


@app.post("/api/marketplace/subscription/portal")
def marketplace_subscription_portal():
    setup_error = marketplace_ready()
    if setup_error is not None:
        return setup_error
    if not same_origin_request():
        return jsonify(error="Request origin could not be verified."), 403
    if rate_limited():
        return jsonify(error="Too many requests. Wait a minute and try again."), 429
    user = marketplace_user()
    if not user or user["role"] != "electrician":
        return jsonify(error="Sign in as an electrician first."), 401
    secret = os.environ.get("STRIPE_SECRET_KEY")
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not secret or not base_url:
        return jsonify(error="Membership management is not configured yet."), 503
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stripe_customer_id FROM marketplace_subscriptions WHERE user_id = %s", (user["id"],))
            row = cur.fetchone()
    if not row or not row[0]:
        return jsonify(error="No Stripe membership was found for this account."), 404
    body = urlencode({"customer": row[0], "return_url": base_url + "/#jobs"}).encode("utf-8")
    req = Request(
        "https://api.stripe.com/v1/billing_portal/sessions",
        data=body,
        headers={"Authorization": "Bearer " + secret, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("url"):
            raise ValueError("Stripe did not return a portal URL")
        return jsonify(url=payload["url"]), 200
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        app.logger.exception("Stripe billing portal creation failed")
        return jsonify(error="Could not open membership management. Please try again later."), 502


def valid_stripe_signature(payload, signature_header, secret):
    try:
        parts = dict(item.split("=", 1) for item in signature_header.split(","))
        timestamp = int(parts["t"])
        if abs(int(time.time()) - timestamp) > 300:
            return False
        expected = hmac.new(secret.encode("utf-8"), str(timestamp).encode("ascii") + b"." + payload, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, item) for key, item in (p.split("=", 1) for p in signature_header.split(",")) if key == "v1")
    except (KeyError, ValueError, TypeError):
        return False


@app.post("/api/stripe/webhook")
def stripe_webhook():
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    signature = request.headers.get("Stripe-Signature", "")
    payload = request.get_data(cache=False)
    if not webhook_secret or not valid_stripe_signature(payload, signature, webhook_secret):
        return jsonify(error="Invalid Stripe signature."), 400
    try:
        event = json.loads(payload.decode("utf-8"))
        event_type = event.get("type", "")
        obj = event["data"]["object"]
    except (ValueError, KeyError, TypeError):
        return jsonify(error="Invalid Stripe event."), 400
    if event_type in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        metadata = obj.get("metadata") or {}
        user_id = metadata.get("marketplace_user_id")
        customer_id = obj.get("customer")
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    if user_id:
                        cur.execute(
                            """INSERT INTO marketplace_subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at)
                               VALUES (%s, %s, %s, %s, %s, NOW())
                               ON CONFLICT (user_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id,
                               stripe_subscription_id = EXCLUDED.stripe_subscription_id, status = EXCLUDED.status,
                               current_period_end = EXCLUDED.current_period_end, updated_at = NOW()""",
                            (int(user_id), customer_id, obj.get("id"), obj.get("status", "inactive"), obj.get("current_period_end")),
                        )
                    elif customer_id:
                        cur.execute(
                            """UPDATE marketplace_subscriptions SET stripe_subscription_id = %s, status = %s,
                               current_period_end = %s, updated_at = NOW() WHERE stripe_customer_id = %s""",
                            (obj.get("id"), obj.get("status", "inactive"), obj.get("current_period_end"), customer_id),
                        )
        except (ValueError, TypeError):
            app.logger.exception("Invalid marketplace user id in Stripe subscription event")
            return jsonify(error="Invalid subscription metadata."), 400
    return jsonify(received=True), 200


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
