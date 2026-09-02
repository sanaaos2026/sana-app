"""أمر إنتاجي أحادي التشغيل لمجدول تنبيهات التنفيذ."""
import json
import sys

from app import _connect_pg
from sana_decision_room import ensure_schema, materialize_due_reminders


def main():
    db = _connect_pg()
    try:
        ensure_schema(db)
        result = materialize_due_reminders(db, acquire_lock=True)
        db.commit()
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 1 if result.get("status") == "failed" else 0
    except Exception as exc:
        db.rollback()
        print(json.dumps({
            "status": "failed",
            "error": str(exc)[:500],
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())