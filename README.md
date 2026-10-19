# chat_engen — Chat Engine فوق `general_ai_core`

محرك شات مستقل (API + كتالوج موحّد + streaming + محادثات) يُعيد استخدام `Router / Registry / ExecutionService` من
[`general_ai_core`](https://github.com/belalalibb/general_ai_core) **كتبعية مثبّتة بـ SHA** دون تعديل عقوده (additive-only).

```text
PHASE NOW:     0 — AUDIT (انظر CHAT_ENGINE_STATE.md)   ·   لا كود منتج قبل OWNER_APPROVAL = YES
UPSTREAM_REF:  general_ai_core @ 0a13556305bcf40cbf1b750d7ef1d81f7850cabf
```

## للمطوّرين والوكلاء البرمجيين: كيف تستدعي المحرك وتبني عليه

**ابدأ من `docs/INTEGRATION_GUIDE.md`** — دليل مرجعي كامل (شكل كل request/response حرفيًا، SSE، الذاكرة، الاستراتيجيات، Skills، الأخطاء وكيف تتصرف، حقائق مزوّدات البوابة، SDKs، وصفة بناء تطبيق). كل أمثلته نُفِّذت فعلًا ضد خادم حي. المرجع الحاكم للعقد: `GET /openapi.json` ثم `docs/API.md`.

## وثائق المرحلة 0 (اقرأها بهذا الترتيب)

| # | الملف | ما فيه |
|---|---|---|
| 1 | `CHAT_ENGINE_STATE.md` | **ملف الحالة الوحيد القابل للتغيير** — المرحلة، المهمة الحالية، الموافقة، الـ ledger، خطوات الاستئناف |
| 2 | `CHAT_ENGINE_REALITY_AUDIT.md` | تدقيق المرجع بالأدلة: PROVEN / PARTIAL / CONTRACT_ONLY / TEMPLATE / MISSING / BROKEN، أداء 1k/10k، فجوات P0/P1/P2 |
| 3 | `docs/adr/ADR-CE-0001-architecture-lock.md` | القرارات المعمارية المقفلة D0–D10 (PROPOSED حتى الموافقة) |
| 4 | `CHAT_ENGINE_REFERENCE_ASSESSMENT.md` | مراجعة 7 مستودعات خارجية: رخص، أنماط تُعتمد، قرارات REUSE/ADAPT/REJECT/FUTURE |
| 5 | `CHAT_ENGINE_TASKS.md` | خطة التنفيذ CE-00..CE-18 بقالب موحّد (هدف/نطاق/تبعيات/ملفات/اختبارات/قبول/مخاطر/rollback) |
| — | `evidence/ce00/` | مخرجات خام قابلة لإعادة الإنتاج (pytest، scale probe، cli describe/routes، smoke محلي) |

## عقد التشغيل الدائم (يسري على كل جلسة)

1. **المصدر الوحيد للحقيقة = Git.** ما لم يُدفع إلى `origin/main` يُعتبر غير موجود (أُثبت ذلك عمليًا: 3 انقطاعات خلال CE-00).
2. **وحدة عمل واحدة** → تحقق → commit → **push فورًا** → صف ledger عند checkpoint مُتحقَّق.
3. **Additive-only على المرجع:** لا تعديل لأي ملف في `general_ai_core`؛ الحاجة لتعديله ⇒ ADR + PR منفصل.
4. **لا نسخ UI** من المرجع (ملف/لون/تخطيط). الواجهة الجديدة في `ui_standalone/` بـ TypeScript + Vite + Lit.
5. **لا تبعية جديدة بلا إثبات** مُسجَّل في الـ ledger (الاسم، الرخصة، السبب، البديل المرفوض).
6. **لا أسرار** في logs/docs/evidence/commits. أنماط ممنوعة: `gsk_`, `ghp_`, `sk-`, `AKIA`, `xox`. الـ token الذي يرسله المالك يُستخدم **فقط** لإعداد `~/.git-credentials` ولا يُكتب في أي ملف داخل الـ repo.
7. **التوسيم الصادق:** كل سلوك لم يُختبر ضد مزوّد حقيقي يُوسم `UNVERIFIED (no live key)`.
8. **Release gate لكل مهمة:** ruff + mypy + lint-imports + pytest (hermetic) + compat/golden + secret-scan.
9. **الوثائق بالعربية** مع المصطلحات التقنية بالإنجليزية.

## بروتوكول الاستئناف ("تابع")

عندما يكتب المالك `تابع` (مع الـ token عند الحاجة):

```bash
cd /home/user/webapp
git status && git rev-parse HEAD && git log --oneline -5 && git diff --stat
# 1) اقرأ CHAT_ENGINE_STATE.md ثم هذا الملف
# 2) الملفات غير الملتزمة = Recovery Candidates: افحص وصنّف، لا تحذف عمياء
# 3) أعد إعداد بيئة المرجع إن فُقدت (sandbox resets تمسح .venv و /home/user/ref):
mkdir -p /home/user/ref && cd /home/user/ref \
  && (test -d general_ai_core || git clone -q https://github.com/belalalibb/general_ai_core.git) \
  && cd general_ai_core && git checkout -q 0a13556305bcf40cbf1b750d7ef1d81f7850cabf \
  && python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
# 4) تابع CURRENT_TASK فقط؛ وحدة واحدة؛ تحقق؛ commit؛ push
# 5) حدّث CHAT_ENGINE_STATE.md عند checkpoint مُتحقَّق فقط، بـ NEXT_TASK واحد
```

إعداد Git بعد كل reset (الـ token من رسالة المالك، لا يُحفظ في الـ repo):

```bash
git config user.name "belalalibb" && git config user.email "belalalibb@users.noreply.github.com"
git config credential.helper store
printf 'https://x-access-token:%s@github.com\n' "$OWNER_TOKEN" > ~/.git-credentials && chmod 600 ~/.git-credentials
```

## إعادة إنتاج أدلة CE-00 (Linux)

```bash
cd /home/user/ref/general_ai_core
# اختبارات hermetic (بلا مفاتيح)
env -u GSK_API_KEY -u GROQ_API_KEY -u GSK_TOKEN .venv/bin/python -m pytest \
  tests/contract tests/providers tests/routing tests/execution tests/api tests/verification -o addopts="" -q
# وصف الـ CLI والمسارات
.venv/bin/python -m apps.cli describe ; .venv/bin/python -m apps.cli routes
# قياس 1k/10k مزوّد
.venv/bin/python /home/user/webapp/evidence/ce00/scale_probe.py
```

> Windows PowerShell: الأوامر المكافئة في `CHAT_ENGINE_REALITY_AUDIT.md §9` موسومة **UNVERIFIED** حتى تُثبت في CE-16 على `windows-latest`.

## البنية المستهدفة بعد الموافقة (ملخص D0–D10)

```text
chat_engine/              حزمة Python مستقلة — تستورد core.* / providers.real.* / infrastructure.* فقط (ممنوع apps.*)
  api/                    FastAPI: /v1/execute (متوافق) · /v1/chat* · /v1/catalog/* · /v1/admin/providers/* · /v1/chat/completions
  composition/            composition root يركّب Router/Registry/ExecutionService من المرجع مباشرة
  catalog/                CanonicalModel + ModelAlias{confirmed|proposed|rejected} + CatalogIndex
  handoff/                HandoffPacket v1 → payload["messages"] (مع إبقاء ask/context)
  admission/              AdmissionPort: InMemory (افتراضي) / Redis Lua — rpm/tpm/concurrency/budget
  capabilities/           CapabilityLedger: declared ∧ verified ∧ enabled ∧ available_now
  streaming/              StreamingAdapterPort اختياري + SSE؛ لا failover صامت بعد أول delta
  conversations/          Projects / Conversations / Turns / Summary (PostgreSQL ce_* أو in-memory)
compat/                   freeze baseline للمرجع + golden snapshots + manifests
ui_standalone/            TypeScript + Vite + Lit — بلا أي ملف من ui/ المرجعية
evidence/ceNN/            مخرجات خام لكل مهمة
```

## الحالة

انظر `CHAT_ENGINE_STATE.md` — هو المرجع الوحيد لما أُنجز وما يلي.
