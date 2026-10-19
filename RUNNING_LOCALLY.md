## Running with Docker Compose

A quick way to run the app locally (Postgres + web) is via Docker Compose.

1. Copy `.env.example` to `.env` and fill in any keys you want to set (you can leave OPENAI_API_KEY empty for now).
2. Build and start the app:

   docker-compose up --build

3. Open http://localhost:8000

Notes:
- The docker-compose sets up a Postgres 15 instance and the web service configured to connect to it using DATABASE_URL.
- The web service will run using gunicorn and will initialize the database schema on startup.
