"""اختبارات محرك الاختناق والتجارب والقرار وحلقة التعلم."""
import os
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg
from sana_growth_os import (
    METRIC_DEFINITIONS,
    create_baseline,
    create_truth_record,
    ensure_schema as ensure_growth_schema,
    seed_growth_os,
)
from sana_growth_engine import (
    advance_process,
    approve_decision,
    begin_experiment,
    close_experiment,
    create_bottleneck_cycle,
    create_experiment,
    create_learning_link,
    ensure_schema,
    seed_growth_engine,
    update_experiment,
)
from sana_decision_room import (
    create_sop,
    ensure_schema as ensure_decision_room_schema,
    record_sop_application,
)


class GrowthEngineRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL غير مضبوط")
        cls.db = _connect_pg()
        cls.db.execute("BEGIN")
        ensure_growth_schema(cls.db)
        seed_growth_os(cls.db)
        seed_growth_engine(cls.db)
        ensure_schema(cls.db)
        ensure_decision_room_schema(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.rollback()
        cls.db.close()

    def _truth(self, classification="Fact"):
        return create_truth_record(self.db, "C001", {
            "subject": "اختناق تجريبي",
            "value": {"count": 4},
            "classification": classification,
            "source_ref": "crm:verified-snapshot",
            "observed_at": "2026-09-01",
            "confidence": 90,
        })

    def _baseline(self, complete=True):
        keys = METRIC_DEFINITIONS if complete else METRIC_DEFINITIONS[:1]
        metrics = {
            key: {"value": 10, "source_ref": f"ledger:{key}", "confidence": 90}
            for key, *_ in keys
        }
        return create_baseline(self.db, "C001", {
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "observed_at": "2026-09-01",
            "confidence": 90,
            "source_ref": "ledger:august",
            "metrics": metrics,
        })

    def _cycle(self):
        baseline = self._baseline()
        truth = self._truth()
        cycle = create_bottleneck_cycle(self.db, "C001", {
            "baseline_id": baseline["baseline_id"],
            "candidates": [
                {
                    "problem": "انخفاض التحويل",
                    "evidence_ids": [truth["truth_id"]],
                    "financial_impact": {
                        "value": 12000,
                        "currency": "SAR",
                        "source_ref": "finance:lost-revenue",
                    },
                    "candidate_cause": "بطء المتابعة قد يرفع التسرب",
                    "confidence": 80,
                    "priority": 95,
                },
                {
                    "problem": "ضعف الاحتفاظ",
                    "evidence_ids": [truth["truth_id"]],
                    "financial_impact": {
                        "value": 3000,
                        "currency": "SAR",
                        "source_ref": "finance:retention-impact",
                    },
                    "candidate_cause": "التواصل بعد التسليم قد يكون غير منتظم",
                    "confidence": 65,
                    "priority": 60,
                },
            ],
        })
        return baseline, truth, cycle

    def _experiment(self):
        baseline, truth, cycle = self._cycle()
        experiment = create_experiment(self.db, "C001", {
            "cycle_id": cycle["cycle_id"],
            "hypothesis": "تقليل زمن المتابعة يرفع الإيراد المحقق",
            "one_change": "متابعة الفرصة خلال ساعتين",
            "target_segment": "الفرص المؤهلة",
            "owner": "مالك المبيعات",
            "start_date": "2026-09-02",
            "kpi": "revenue",
            "baseline_id": baseline["baseline_id"],
            "target": 15,
            "success_boundary": "الإيراد >= 15",
            "stop_boundary": "الإيراد < 8",
            "evidence_ids": [truth["truth_id"]],
        })
        return baseline, truth, cycle, experiment

    def test_incomplete_baseline_stops_bottleneck_cycle(self):
        baseline = self._baseline(complete=False)
        truth = self._truth()
        with self.assertRaisesRegex(ValueError, "BASELINE_INCOMPLETE"):
            create_bottleneck_cycle(self.db, "C001", {
                "baseline_id": baseline["baseline_id"],
                "candidates": [{
                    "problem": "نقص البيانات",
                    "evidence_ids": [truth["truth_id"]],
                    "financial_impact": {"value": 1, "currency": "SAR", "source_ref": "finance:test"},
                    "candidate_cause": "سبب محتمل",
                    "confidence": 50,
                    "priority": 50,
                }],
            })

    def test_conflicting_evidence_stops_selection(self):
        baseline = self._baseline()
        conflict = self._truth("Conflict")
        with self.assertRaisesRegex(ValueError, "EVIDENCE_GATE"):
            create_bottleneck_cycle(self.db, "C001", {
                "baseline_id": baseline["baseline_id"],
                "candidates": [{
                    "problem": "مشكلة غير محسومة",
                    "evidence_ids": [conflict["truth_id"]],
                    "financial_impact": {"value": 1, "currency": "SAR", "source_ref": "finance:test"},
                    "candidate_cause": "سبب مختلف عليه",
                    "confidence": 50,
                    "priority": 50,
                }],
            })

    def test_cycle_has_exactly_one_primary_bottleneck(self):
        _, _, cycle = self._cycle()
        primary = [item for item in cycle["bottlenecks"] if item["is_primary"]]
        self.assertEqual(1, len(primary))
        self.assertEqual("انخفاض التحويل", primary[0]["problem"])
        self.assertEqual("Hypothesis", primary[0]["classification"])

    def test_experiment_requires_all_fields_and_locks_baseline_on_start(self):
        baseline, truth, cycle = self._cycle()
        with self.assertRaisesRegex(ValueError, "one_change"):
            create_experiment(self.db, "C001", {
                "cycle_id": cycle["cycle_id"],
                "hypothesis": "فرضية",
                "target_segment": "قطاع",
                "owner": "مالك",
                "start_date": "2026-09-02",
                "kpi": "revenue",
                "baseline_id": baseline["baseline_id"],
                "target": 20,
                "success_boundary": ">=20",
                "stop_boundary": "<8",
                "evidence_ids": [truth["truth_id"]],
            })
        _, _, _, experiment = self._experiment()
        running = begin_experiment(self.db, "C001", experiment["experiment_id"])
        self.assertEqual("running", running["status"])
        self.assertIsNotNone(running["locked_at"])
        self.assertEqual("complete", running["baseline_snapshot"]["status"])
        with self.assertRaisesRegex(ValueError, "EXPERIMENT_LOCKED"):
            update_experiment(self.db, "C001", experiment["experiment_id"], {"target": 99})

    def test_only_four_decisions_and_approval_requires_actual_result(self):
        _, truth, _, experiment = self._experiment()
        begin_experiment(self.db, "C001", experiment["experiment_id"])
        result = {
            "actual_result": {
                "value": 16,
                "source_ref": "ledger:experiment-result",
                "observed_at": "2026-09-30",
                "unit": "currency",
            },
            "reason": "تجاوزت النتيجة الهدف مع قياس موثق",
            "evidence_ids": [truth["truth_id"]],
            "source_ref": "review:experiment-close",
        }
        with self.assertRaisesRegex(ValueError, "Scale"):
            close_experiment(self.db, "C001", experiment["experiment_id"], {
                **result, "decision": "InventedDecision",
            })
        closed = close_experiment(self.db, "C001", experiment["experiment_id"], {
            **result, "decision": "Scale",
        })
        self.assertEqual("closed", closed["status"])
        self.assertEqual("proposed", closed["decision"]["status"])
        approved = approve_decision(self.db, "C001", closed["decision_id"])
        self.assertEqual("approved", approved["status"])

    def test_manual_process_blocks_premature_automation(self):
        baseline, truth, _, experiment = self._experiment()
        begin_experiment(self.db, "C001", experiment["experiment_id"])
        advance_process(self.db, "C001", experiment["experiment_id"], {
            "stage": "Measure", "source_ref": "ops:measurement",
        })
        close_experiment(self.db, "C001", experiment["experiment_id"], {
            "actual_result": {
                "value": 12,
                "source_ref": "ledger:experiment-result",
                "observed_at": "2026-09-30",
            },
            "decision": "Modify",
            "reason": "النتيجة دون الهدف وتحتاج تعديلًا واحدًا",
            "evidence_ids": [truth["truth_id"]],
        })
        advance_process(self.db, "C001", experiment["experiment_id"], {
            "stage": "Improve", "source_ref": "ops:improvement",
        })
        advance_process(self.db, "C001", experiment["experiment_id"], {
            "stage": "Standardize", "manual_proven": True, "repetitions": 1,
            "source_ref": "ops:standard",
        })
        with self.assertRaisesRegex(ValueError, "AUTOMATION_GATE"):
            advance_process(self.db, "C001", experiment["experiment_id"], {
                "stage": "Automate", "manual_proven": True, "repetitions": 1,
                "source_ref": "ops:automation-candidate",
            })
        evidence_id = f"E-SOP-{experiment['experiment_id'][:12]}"
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,title,source_type,confidence,evidence_type,source_ref)
               VALUES (?,?,?,?,?,?,?)""",
            (evidence_id, "C001", "تطبيق SOP", "manual-test", 90, "Fact",
             "test:sop-application"),
        )
        sop = create_sop(self.db, "C001", {
            "title": "متابعة الفرص", "owner_role": "مالك المبيعات",
            "steps": ["راجع", "تابع"], "checklist": ["تمت المتابعة"],
            "sla": "ساعتان", "kpi": "الإيراد", "reusable_template": "قالب",
            "evidence_ids": [evidence_id],
            "result": {"status": "captured", "source_ref": "test:sop"},
            "source_ref": "test:sop", "maturity": "Manual", "approve": True,
        }, "tester")
        for index, value in enumerate((16, 17), start=1):
            result_evidence_id = f"{evidence_id}-R{index}"
            result_source = f"ledger:sop-result-{index}"
            self.db.execute(
                """INSERT INTO evidence
                   (evidence_id,company_id,title,source_type,confidence,evidence_type,source_ref)
                   VALUES (?,?,?,?,?,?,?)""",
                (result_evidence_id, "C001", f"نتيجة {index}", "manual-test",
                 90, "Fact", result_source),
            )
            record_sop_application(self.db, "C001", sop["sop_id"], {
                "version_id": sop["version_id"], "kpi": "revenue",
                "application_ref": f"growth-run-{index}",
                "direction": "higher_is_better",
                "baseline_id": baseline["baseline_id"],
                "evidence_ids": [result_evidence_id],
                "result_evidence_id": result_evidence_id,
                "result": {
                    "value": value,
                    "source_ref": result_source,
                    "observed_at": f"2026-10-0{index}",
                },
            }, "tester")
        automated = advance_process(self.db, "C001", experiment["experiment_id"], {
            "stage": "Automate", "manual_proven": True, "repetitions": 2,
            "source_ref": "ops:automation-approved",
            "sop_version_id": sop["version_id"],
        })
        self.assertEqual("Automate", automated["current_stage"])

    def test_learning_link_is_versioned_and_idempotent(self):
        _, truth, cycle = self._cycle()
        primary = next(item for item in cycle["bottlenecks"] if item["is_primary"])
        payload = {
            "from_type": "evidence",
            "from_id": truth["truth_id"],
            "link_type": "qualifies",
            "to_type": "hypothesis",
            "to_id": primary["bottleneck_id"],
            "source_ref": "review:knowledge-loop",
            "knowledge_version": "v1.0",
        }
        first, created = create_learning_link(self.db, "C001", payload)
        second, created_again = create_learning_link(self.db, "C001", payload)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["link_id"], second["link_id"])


if __name__ == "__main__":
    unittest.main()