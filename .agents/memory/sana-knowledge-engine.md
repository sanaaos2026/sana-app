---
name: Sana deterministic knowledge engine
description: The durable rule for how Sana Knowledge must govern diagnostics and AI presentation.
---

Sana must derive diagnostic findings and decisions from approved knowledge objects, linked evidence, and explicit diagnostic/decision rules before involving AI. AI may only present a supported result and may not invent causes, KPIs, benchmarks, decisions, or recommendations.

**Why:** A language model can produce plausible but unsupported business advice, which violates Sana's promise of evidence-led consulting and makes the output impossible to audit.

**How to apply:** For every case, run the deterministic engine first. If the sector or evidence is not covered, return the exact evidence-gate message, list missing evidence/questions, and mark research or consultant review as required. Keep source rights, jurisdiction, version, review date, and evidence quality attached to reusable knowledge.

Scan rules must evaluate structured SDS answers and explicit conjunctions, not broad substring matches. A free-text follow-up may corroborate a qualified controlled answer but must never replace it. Scores have no methodology baseline: every nonzero component must cite real SDS, Fact, or independent Evidence sources. The current case statement is traceable context, not independent corroboration of its own Discovery answer.

**Why:** Broad token matching and prefix matching of follow-up references can turn weak or contradictory answers into severe bottlenecks and unsupported decisions.

**How to apply:** Require the exact direct SDS source reference and qualifying controlled value first, then apply any required independent evidence, SOP, acceptance-criterion, or recurrence condition. If the conjunction fails, emit only an evidence-gathering hypothesis and proposed review step.

General service/B2B operating principles belong in a shared guidance framework, not in a client-specific knowledge base and not as automatic diagnostic patterns.

**Why:** The same principles should support many projects, but applying a generic rule as if it proved a specific company's condition would recreate unsupported AI advice.

**How to apply:** Let general frameworks supply questions, categories, and experiment design. Require case evidence and an explicit rule before producing a finding or decision, and never derive numeric benchmarks from guidance alone.

المراجع المشتركة لا تُعرض لمجرد أنها عامة الصلاحية؛ يلزم تطابق سياقي محدد، وإلا تظهر فجوة معرفة. وأي تعارض معلّق يحجب طرفيه من الاسترجاع بغض النظر عن اتجاه سجل التعارض.

**Why:** اعتبار وسوم مثل shared/all تطابقًا فعليًا يملأ كل قضية بقائمة عامة، كما أن فحص جهة واحدة من علاقة التعارض يسمح للمادة المتعارضة بالعودة إلى التقارير.

**How to apply:** استخدم الوسوم العامة كحد أهلية فقط لا كدرجة صلة، وافحص التعارض تماثليًا وقت البحث مع عرضه منفصلًا للمراجعة البشرية.