"""One-shot production database initialization for Sana.

Run this as Railway's pre-deploy command, before starting Gunicorn. It is
deliberately separate from the web process so DDL and seed work never runs in
each worker.
"""

import os


def main():
    runtime = os.environ.get("SANA_ENV", "").strip().lower()
    if runtime not in {"production", "prod"}:
        raise RuntimeError("production_init.py requires SANA_ENV=production")
    if not os.environ.get("SESSION_SECRET"):
        raise RuntimeError("SESSION_SECRET is required for production initialization")

    from app import init_db, seed_db, seed_decision_impacts, seed_knowledge_db

    init_db()
    seed_db()
    seed_decision_impacts()
    seed_knowledge_db()
    print("[production-init] database initialization and seeding completed", flush=True)


if __name__ == "__main__":
    main()