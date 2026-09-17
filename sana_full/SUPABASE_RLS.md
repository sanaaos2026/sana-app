# حماية بيانات Sana في Supabase

## قرار الوصول

في المرحلة الحالية، كل جداول Sana في مخطط `public` **خاصة بخادم Flask فقط**.
لا يستخدم العميل Supabase مباشرة، ولا توجد حاجة لسياسات عميل مرتبطة بـ
`auth.uid()` بعد. لذلك تطبق القاعدة مرحلتين دفاعيتين:

1. سحب `SELECT/INSERT/UPDATE/DELETE` والصلاحيات التابعة من `anon` و
   `authenticated` ومن `PUBLIC`.
2. تفعيل Row Level Security وإنشاء سياسة `sana_deny_public_access` التي تمنع
   القراءة والكتابة حتى لو أُعيد منح صلاحية جدول بالخطأ.

اتصال Flask يستخدم رابط قاعدة البيانات الخاص عبر `psycopg2` وبصلاحية الخادم
الخاصة. في قاعدة Supabase الحالية يظهر الدور `postgres` مع `rolbypassrls=true`؛
لذلك لا تقطع RLS اتصال الخادم ولا تغيّر عزل الشركات الذي يفرضه Flask. يبقى
`service_role` مخصصًا للاستخدام الخادمي الموثوق فقط، ولا يجوز وضعه في المتصفح.

## جرد الجداول

### بيانات مرتبطة بشركة — يجب أن تبقى خلف Flask

هذه الجداول تحتوي `company_id` مباشرة أو تسجل بيانات العميل التشغيلية:

`companies`, `user_accounts`, `users`, `cases`, `assets`, `evidence`,
`evidence_relations`, `diagnostic_baselines`, `scan_runs`, `scan_findings`,
`decisions`, `tasks`, `task_evidence`, `p0_impact_reviews`, `case_frameworks`,
`leads`, `opportunities`, `sales_activities`, `sales_stage_history`,
`execution_backlog`, `execution_backlog_audit`, `execution_owner_bindings`,
`execution_reminder_attempts`, `execution_reminder_runs`, `execution_reminders`,
`execution_risks`, `execution_sop_applications`, `execution_sop_versions`,
`execution_sops`, `execution_task_audit`, `gos_truth_records`,
`gos_baselines`, `gos_baseline_metrics`, `gos_company_profiles`,
`gos_canonical_entities`, `gos_canonical_merges`, `gos_bottleneck_cycles`,
`gos_bottlenecks`, `gos_experiments`, `gos_experiment_decisions`,
`gos_learning_links`, `rc_stage_history`, `rc_opportunity_economics`,
`rc_projects`, `rc_invoices`, `rc_deal_learning`, `zubair_capture_drafts`,
`zubair_attachments`, `zubair_contacts`, `zubair_prospect_companies`,
`zubair_timeline_events`, `sana_memory_entries`, `drive_client_folder_mappings`,
`drive_source_excerpts`, `drive_private_citations`.

الجداول التي لا تحمل `company_id` لكنها ترتبط ببيانات الشركة عبر مفتاح آخر
تُعامل أيضًا كبيانات خاصة:

`decision_asset_impacts`, `diagnostic_runs`, `diagnostic_findings`,
`research_source_files`, `research_source_chunks`, `research_source_annotations`,
`research_source_relations`, `drive_knowledge_sources`,
`drive_excerpt_reviews`, `drive_provenance_links`.

الجداول الأحدث الخاصة بالمراجعة والذاكرة والفوترة تبقى ضمن النطاق نفسه:

`case_human_reviews`, `company_invitations`, `company_memory_conflicts`,
`company_memory_governance`, `company_memory_items`, `company_memory_links`,
`company_memory_versions`, `financial_evidence`, `financial_evidence_reviews`,
`human_review_events`, `human_review_settings`, `human_review_slots`,
`returning_checkins`, `sana_discovery_drafts`, `sana_billing_cleanup_runs`,
`sana_billing_cleanup_skips`, `sana_billing_coupons`, `sana_billing_events`,
`sana_billing_settings`, `sana_company_subscriptions`.

### معرفة وإدارة داخلية — Flask فقط

حتى المحتوى الذي تعرضه بعض صفحات Sana للعامة يجب أن يمر عبر Flask حتى لا
ينكشف مخطط البيانات أو منطق المعرفة مباشرة:

`methodology_docs`, `task_packs`, `task_pack_items`, `knowledge_objects`,
`knowledge_sources`, `knowledge_versions`, `knowledge_conflicts`,
`knowledge_links`, `knowledge_release_log`, `capability_gaps`,
`gos_governance_registry`, `gos_metric_definitions`, `gos_project_profiles`,
`knowledge_research_config`, `knowledge_research_runs`,
`knowledge_research_candidates`, `knowledge_research_alerts`,
`knowledge_research_gaps`, `knowledge_backup_runs`, `drive_files`,
`drive_index_config`, `drive_sync_runs`, `drive_source_alerts`,
`rc_stage_profiles`.

### أسرار وتشغيل داخلي

هذه الجداول لا يجوز تعريضها لأي دور عميل تحت أي ظرف:

`password_reset_tokens`, `sana_scheduler_credentials`, `experts`,
`expert_sessions`, `expert_facts`, `expert_knowledge_assets`,
`expert_projects`.

كما تبقى سجلات الإدارة والإشعارات الداخلية خلف الخادم:
`admin_audit_log`, `admin_notification_outbox`.

## التطبيق والتحديث

نفّذ الملف `supabase_rls.sql` على قاعدة Supabase المستهدفة باستخدام اتصال خاص
بالخادم، وليس مفتاح Supabase العام. الملف يعيد تطبيق نفسه بأمان. لا تضف تشغيله
إلى `app.py` أو مسار الطلبات؛ مخطط Railway يُحدّث كخطوة نشر/إدارة مستقلة كما هو
موثق في `MIGRATION_AUDIT.md`.

عند إضافة جدول جديد:

1. أضفه إلى `schema.sql` أو منشئ المخطط المناسب.
2. شغّل `supabase_rls.sql` على Supabase.
3. شغّل `test_supabase_rls.py` وتحقق من اختبار رحلة الدخول والعزل والتنبيهات.

## حالة التحقق

اختبارات العقد الأمني تتحقق من:

- وجود RLS وسياسة الرفض على كل جدول `public` فعليًا.
- عدم امتلاك `anon` و`authenticated` أي صلاحية جدول أو sequence.
- بقاء اتصال Flask الخاص قادرًا على القراءة.
- استمرار اختبارات login وعزل الشركات واختبارات execution reminders.