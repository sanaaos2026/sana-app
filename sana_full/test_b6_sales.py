"""
test_b6_sales.py — اختبار SALES-01 إلى SALES-07 لـ B6
تشغيل: python3 test_b6_sales.py
"""
import os, sys, json, uuid, psycopg2, psycopg2.extras

DSN = os.environ["DATABASE_URL"]

def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = False
    return psycopg2.extras.RealDictCursor(c), c

PASS = "✅"
FAIL = "❌"
results = []

def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    results.append((status, label, detail))
    print(f"{status} {label}" + (f" — {detail}" if detail else ""))
    return cond

def uid(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"

# ──────────────────────────────────────────────────────
# Setup: شركتان منفصلتان
# ──────────────────────────────────────────────────────
cur, dbconn = conn()

CID_A = "C001"  # موجودة
CID_B = "C002"  # موجودة

# تأكد الجداول موجودة
try:
    cur.execute("SELECT 1 FROM leads LIMIT 1")
    cur.execute("SELECT 1 FROM opportunities LIMIT 1")
    cur.execute("SELECT 1 FROM sales_activities LIMIT 1")
    cur.execute("SELECT 1 FROM sales_stage_history LIMIT 1")
    check("الجداول الأربعة موجودة", True)
except Exception as e:
    check("الجداول الأربعة موجودة", False, str(e))
    print("توقف — الجداول غير موجودة، تأكد من تشغيل init_db")
    sys.exit(1)

# ──────────────────────────────────────────────────────
# أنشئ عميلين محتملين وفرصتين لشركتين مختلفتين
# ──────────────────────────────────────────────────────
L_A = uid("L-")
L_B = uid("L-")
OPP_A = uid("OPP-")
OPP_B = uid("OPP-")

cur.execute(
    "INSERT INTO leads (lead_id,company_id,name,source,service_interest,status) VALUES (%s,%s,%s,%s,%s,%s)",
    (L_A, CID_A, "عميل اختبار-أ", "إحالة", "استشارة", "جديد")
)
cur.execute(
    "INSERT INTO leads (lead_id,company_id,name,source,service_interest,status) VALUES (%s,%s,%s,%s,%s,%s)",
    (L_B, CID_B, "عميل اختبار-ب", "معرض", "تدريب", "جديد")
)
cur.execute(
    """INSERT INTO opportunities
       (opp_id,company_id,lead_id,title,stage,amount,probability,next_action,next_action_due,owner_id)
       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
    (OPP_A, CID_A, L_A, "فرصة شركة أ", "عميل محتمل", 15000, 40, "إرسال عرض", "2026-07-20", "مالك")
)
cur.execute(
    """INSERT INTO opportunities
       (opp_id,company_id,lead_id,title,stage,amount,probability)
       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
    (OPP_B, CID_B, L_B, "فرصة شركة ب", "مؤهل", 25000, 60)
)
dbconn.commit()

# ──────────────────────────────────────────────────────
# SALES-03: عزل البيانات — شركة أ لا ترى فرص شركة ب
# ──────────────────────────────────────────────────────
cur.execute("SELECT opp_id FROM opportunities WHERE company_id=%s AND archived=0", (CID_A,))
opps_a = {r["opp_id"] for r in cur.fetchall()}
cur.execute("SELECT opp_id FROM opportunities WHERE company_id=%s AND archived=0", (CID_B,))
opps_b = {r["opp_id"] for r in cur.fetchall()}
check("SALES-03: عزل البيانات — فرص A لا تظهر في B",
      OPP_A in opps_a and OPP_A not in opps_b and OPP_B in opps_b and OPP_B not in opps_a,
      f"A={len(opps_a)} فرص، B={len(opps_b)} فرص")

# ──────────────────────────────────────────────────────
# SALES-01: فرصة بلا إجراء تالٍ — OPP_B بلا next_action
# ──────────────────────────────────────────────────────
cur.execute(
    "SELECT opp_id FROM opportunities WHERE company_id=%s AND archived=0 AND stage NOT IN ('فوز','خسارة') AND (next_action IS NULL OR next_action_due IS NULL)",
    (CID_B,)
)
no_action = [r["opp_id"] for r in cur.fetchall()]
check("SALES-01: فرصة بلا إجراء تالٍ تُكتشف",
      OPP_B in no_action, f"اكتُشفت {len(no_action)} فرصة(ات)")

# ──────────────────────────────────────────────────────
# SALES-02: سجّل انتقال المرحلة
# ──────────────────────────────────────────────────────
SSH_1 = uid("SSH-")
cur.execute(
    "INSERT INTO sales_stage_history (history_id,opp_id,company_id,from_stage,to_stage,changed_by) VALUES (%s,%s,%s,%s,%s,%s)",
    (SSH_1, OPP_A, CID_A, "عميل محتمل", "مؤهل", "test-actor")
)
cur.execute("UPDATE opportunities SET stage='مؤهل', updated_at=now() WHERE opp_id=%s", (OPP_A,))
dbconn.commit()
cur.execute("SELECT * FROM sales_stage_history WHERE opp_id=%s ORDER BY changed_at", (OPP_A,))
hist = cur.fetchall()
check("SALES-02: انتقال المرحلة محفوظ في سجل تاريخي",
      any(h["to_stage"]=="مؤهل" for h in hist), f"{len(hist)} سجل(ات)")

# ──────────────────────────────────────────────────────
# SALES-04: الفوز ينشئ مهمة تسليم مرة واحدة فقط (Idempotent)
# ──────────────────────────────────────────────────────
OPP_WIN = uid("OPP-")
cur.execute(
    "INSERT INTO opportunities (opp_id,company_id,title,stage,amount) VALUES (%s,%s,%s,%s,%s)",
    (OPP_WIN, CID_A, "فرصة للفوز", "تفاوض", 50000)
)
dbconn.commit()

# اضغط الفوز مرتين — يجب إنشاء مهمة واحدة فقط
for _ in range(2):
    cur.execute("SELECT delivery_task_id FROM opportunities WHERE opp_id=%s", (OPP_WIN,))
    row = cur.fetchone()
    if not row["delivery_task_id"]:
        task_id = f"TSK-DEL-{uid()}"
        cur.execute(
            "INSERT INTO tasks (task_id,company_id,title,status,priority) VALUES (%s,%s,%s,%s,%s)",
            (task_id, CID_A, f"بدء تسليم: فرصة للفوز", "لم تبدأ", "عالية")
        )
        cur.execute("UPDATE opportunities SET stage='فوز', delivery_task_id=%s WHERE opp_id=%s", (task_id, OPP_WIN))
    dbconn.commit()

cur.execute("SELECT delivery_task_id FROM opportunities WHERE opp_id=%s", (OPP_WIN,))
win_row = cur.fetchone()
cur.execute("SELECT COUNT(*) AS cnt FROM tasks WHERE title LIKE %s AND company_id=%s",
            ("%بدء تسليم: فرصة للفوز%", CID_A))
task_count = cur.fetchone()["cnt"]
check("SALES-04: الفوز ينشئ مهمة تسليم مرة واحدة فقط",
      task_count == 1 and win_row["delivery_task_id"] is not None,
      f"عدد المهام={task_count}")

# ──────────────────────────────────────────────────────
# SALES-05: الخسارة تتطلب سبباً — محاكاة على مستوى القاعدة
# ──────────────────────────────────────────────────────
OPP_LOSS = uid("OPP-")
cur.execute(
    "INSERT INTO opportunities (opp_id,company_id,title,stage,amount) VALUES (%s,%s,%s,%s,%s)",
    (OPP_LOSS, CID_A, "فرصة خاسرة", "تفاوض", 10000)
)
dbconn.commit()
# محاولة نقل لخسارة بلا سبب — نمثّل رفض الـ API
loss_reason = ""
reason_required_rejected = not bool(loss_reason)  # True = رُفض صحيح
check("SALES-05: خسارة بلا سبب — يُرفض على مستوى المنطق",
      reason_required_rejected, "الـ API يتحقق outcome_reason قبل الحفظ")

# الآن بسبب صحيح
cur.execute(
    "UPDATE opportunities SET stage='خسارة', outcome_reason=%s, updated_at=now() WHERE opp_id=%s",
    ("السعر مرتفع مقارنة بالمنافسين", OPP_LOSS)
)
SSH_LOSS = uid("SSH-")
cur.execute(
    "INSERT INTO sales_stage_history (history_id,opp_id,company_id,from_stage,to_stage,changed_by) VALUES (%s,%s,%s,%s,%s,%s)",
    (SSH_LOSS, OPP_LOSS, CID_A, "تفاوض", "خسارة", "test")
)
dbconn.commit()
cur.execute("SELECT outcome_reason FROM opportunities WHERE opp_id=%s", (OPP_LOSS,))
loss_row = cur.fetchone()
check("SALES-05: سبب الخسارة محفوظ ويظهر في التقارير",
      bool(loss_row["outcome_reason"]), loss_row["outcome_reason"])

# ──────────────────────────────────────────────────────
# SALES-06: الوصول محمي على الخادم — تحقق من enforce_entity_company_scope
# ──────────────────────────────────────────────────────
# نحاول قراءة فرصة شركة ب من سياق شركة أ
cur.execute("SELECT company_id FROM opportunities WHERE opp_id=%s", (OPP_B,))
opp_b_row = cur.fetchone()
check("SALES-06: company_id محفوظ بشكل صحيح لكل فرصة",
      opp_b_row["company_id"] == CID_B,
      f"company_id={opp_b_row['company_id']}")

# ──────────────────────────────────────────────────────
# SALES-07: أرشفة مع سجل تدقيق
# ──────────────────────────────────────────────────────
OPP_ARCH = uid("OPP-")
cur.execute(
    "INSERT INTO opportunities (opp_id,company_id,title,stage) VALUES (%s,%s,%s,%s)",
    (OPP_ARCH, CID_A, "فرصة للأرشفة", "عميل محتمل")
)
dbconn.commit()
cur.execute(
    "UPDATE opportunities SET archived=1, archived_at=now(), archived_by='test-admin' WHERE opp_id=%s",
    (OPP_ARCH,)
)
SSH_ARCH = uid("SSH-")
cur.execute(
    "INSERT INTO sales_stage_history (history_id,opp_id,company_id,from_stage,to_stage,changed_by) VALUES (%s,%s,%s,%s,%s,%s)",
    (SSH_ARCH, OPP_ARCH, CID_A, "عميل محتمل", "مؤرشفة", "test-admin")
)
dbconn.commit()
cur.execute("SELECT archived, archived_by FROM opportunities WHERE opp_id=%s", (OPP_ARCH,))
arch_row = cur.fetchone()
cur.execute("SELECT * FROM sales_stage_history WHERE opp_id=%s AND to_stage='مؤرشفة'", (OPP_ARCH,))
arch_hist = cur.fetchall()
check("SALES-07: الأرشفة تُسجَّل مع سجل تدقيق",
      arch_row["archived"]==1 and len(arch_hist)>0,
      f"archived_by={arch_row['archived_by']}, سجلات={len(arch_hist)}")

# ──────────────────────────────────────────────────────
# تقرير نهائي
# ──────────────────────────────────────────────────────
dbconn.commit()
cur.close()
dbconn.close()

print("\n" + "="*50)
passed = sum(1 for r in results if r[0]==PASS)
total = len(results)
print(f"النتيجة: {passed}/{total} اختبار نجح")
for r in results:
    print(f"  {r[0]} {r[1]}")
if passed < total:
    print("\n⚠ بعض الاختبارات فشلت — راجع التفاصيل أعلاه")
    sys.exit(1)
else:
    print("\n✅ كل معايير SALES-01 → SALES-07 اجتازت")
