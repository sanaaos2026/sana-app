"""
سكربت لمرة واحدة: ينقل كل البيانات الحالية من sana.db (SQLite) إلى قاعدة
بيانات PostgreSQL المُفعَّلة عبر DATABASE_URL — دون فقدان أي صف.

يفرغ الجداول في PostgreSQL أولاً (كانت تحتوي فقط على بيانات البذر الافتراضية
التي أنشأها init_db()/seed_db() عند أول تشغيل على القاعدة الجديدة، وليست
بيانات عملاء حقيقية)، ثم ينسخ كل صف كما هو تمامًا من sana.db — بما في ذلك
التواريخ الأصلية — بترتيب يحترم المفاتيح الأجنبية.

آمن لإعادة التشغيل: كل تشغيل يُفرغ الجداول في PostgreSQL وينسخ من sana.db
من جديد، دون أي تعديل على sana.db نفسها.

تشغيل: python3 migrate_to_postgres.py
"""
import os
import sqlite3
import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "sana.db")
DATABASE_URL = os.environ["DATABASE_URL"]

# ترتيب الجداول يحترم المفاتيح الأجنبية: الآباء أولاً
TABLES_IN_ORDER = [
    "companies",
    "user_accounts",
    "users",
    "cases",
    "assets",
    "evidence",
    "decisions",
    "tasks",
    "methodology_docs",
    "decision_asset_impacts",
]


def main():
    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row
    scur = sconn.cursor()

    pconn = psycopg2.connect(DATABASE_URL)
    pcur = pconn.cursor()

    # 1) إفراغ جداول PostgreSQL بترتيب عكسي (الأبناء أولاً) — تمهيدًا لنسخ نظيف
    for table in reversed(TABLES_IN_ORDER):
        pcur.execute(f"DELETE FROM {table}")
    pconn.commit()

    totals = {}
    for table in TABLES_IN_ORDER:
        scur.execute(f"SELECT * FROM {table}")
        rows = scur.fetchall()
        totals[table] = len(rows)
        if not rows:
            continue
        columns = rows[0].keys()
        col_list = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        values = [tuple(row[c] for c in columns) for row in rows]
        pcur.executemany(insert_sql, values)

    pconn.commit()

    # 2) تحقق: عدد الصفوف في PostgreSQL يطابق تمامًا sana.db لكل جدول
    print("=" * 60)
    print("التحقق من تطابق عدد الصفوف بعد النقل:")
    all_match = True
    for table in TABLES_IN_ORDER:
        pcur.execute(f"SELECT COUNT(*) FROM {table}")
        pg_count = pcur.fetchone()[0]
        sqlite_count = totals[table]
        status = "OK" if pg_count == sqlite_count else "MISMATCH!!"
        if pg_count != sqlite_count:
            all_match = False
        print(f"  {table:30s} sqlite={sqlite_count:4d}  postgres={pg_count:4d}  {status}")
    print("=" * 60)
    print("نجح النقل بالكامل ✅" if all_match else "هناك تعارض في عدد الصفوف — راجع أعلاه ❌")

    scur.close()
    sconn.close()
    pcur.close()
    pconn.close()
    return all_match


if __name__ == "__main__":
    ok = main()
    raise SystemExit(0 if ok else 1)
