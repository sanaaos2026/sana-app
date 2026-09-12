"""Backward-compatible article seed entrypoint.

The public article catalog now lives in sana_articles.py. Keeping this filename
prevents old operational notes/scripts from re-introducing the legacy article
copy that exposed internal methodology references.
"""
from app import _connect_pg
from sana_articles import seed_articles_and_knowledge


def seed():
    db = _connect_pg()
    try:
        result = seed_articles_and_knowledge(db)
        print(
            f"✔ تم تحديث {result['articles']} مقالًا عامًا، وتفعيل سياسة المحتوى، "
            f"وحفظ {result['content_backlog_topics']} موضوعًا داخليًا في Knowledge OS"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
