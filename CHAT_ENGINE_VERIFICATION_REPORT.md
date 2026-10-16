# CHAT_ENGINE_VERIFICATION_REPORT — v0.1.0

> قاعدة التقرير: كل ادعاء إمّا **PROVEN (hermetic)** بدليل في `evidence/`، أو **UNVERIFIED (no live key)**، أو **DEFERRED** بقرار مُسجَّل. لا ادعاء بلا دليل.

## 1. ما ثبت hermetic (PROVEN)

| البند | الدليل | النتيجة |
|---|---|---|
| بوابة الإصدار D8 كاملة (ruff, mypy strict, import-linter D0/D3, secret-scan, ui_no_copy_guard, pytest hermetic) | `evidence/ce18/gate.txt` | GATE GREEN — 162 اختبارًا (91 + 71) |
| توافق بايت مع المرجع: 14 golden `/v1/execute`, `/v1/models`, 6 أحداث SSE, 14 manifests, baseline 117 عقدًا | `tests/compat/*`, `compat/` | تمر داخل البوابة |
| عدم استيراد `apps.*`/`ui/` (D0) و FastAPI محصور في `chat_engine.api` (D3) | `lint-imports` داخل البوابة | Contracts: 2 kept, 0 broken |
| لا أسرار في المستودع | `scripts/secret_scan.py` داخل البوابة | clean |
| UI لا تحوي أي ملف من `ui/` المرجعي (hash + basename) | `scripts/ui_no_copy_guard.py` | CLEAN (19 ملفًا مقابل 23) |
| الكتالوج @10k مزوّد: `entries()` p50 | `evidence/ce04/scale_10k.txt` | 284.5 ms (كان 17,270 ms قبل a2a7913) |
| قياسات CE-17 (onboarding, catalog page, explicit/AUTO routing, streams first-byte, admission contention, RSS) | `evidence/ce17/bench_1k.json`, `bench_10k.json` | انظر `docs/PERFORMANCE.md` — الأحكام MET/GAP تُطبع من السكربت نفسه |
| الحالات الخمس للبث (D7) بمحوّلات hermetic | `tests/streaming/test_five_cases.py` | تمر |
| OpenAI SDK الرسمي (sync/stream/errors) ضد التطبيق | `tests/api/test_openai_compat.py` | تمر (AsyncOpenAI عبر ASGITransport) |
| سجل استهلاك واحد بالضبط لكل طلب عبر 6 أسطح | `tests/usage/test_usage.py` | تمر |
| App Factory: 3 مراحل → artifacts، توقّف عند `requires_approval` واستئناف | `tests/app_factory/test_runner.py` | تمر |
| Skills: إسناد بالنطاقات، تعطيل، نسخة مسجّلة في كل Turn | `tests/skills/test_assignable_skills.py` | تمر |
| UI smoke في متصفح حقيقي (Playwright/Chromium): 7 شاشات، RTL/LTR، بث + فقاعات، أثر الاستراتيجية، تسجيل مهارة، كتالوج virtualized | `evidence/ce13/playwright_smoke.txt` | **12/12 PASS** (كشف وأصلح خطأين حقيقيين في SDK TS: `new URL` مع base نسبي، و`fetch` بلا receiver) |
| `cost_tiered` على الخادم الحي مع مزوّد يرفض الاستدلال (`plan_refusal_200`) | تشغيل يدوي مسجَّل في الـ ledger | يتدرّج إلى `local_echo` ويعيد trace: `claude-opus-4-5 failed → local-echo-1 succeeded` |

## 2. UNVERIFIED (no live key)

| البند | لماذا | كيف يتحوّل إلى PROVEN |
|---|---|---|
| بث حقيقي من Groq / OpenAI-compatible provider | لا مفتاح حي في الصندوق (المفتاح الوحيد الموجود `GSK_API_KEY` تُرفض خطته بـ `quota_exceeded`) | `docs/OPERATIONS.md` §5 |
| `streaming_text` verified في الـ ledger لمزوّد حقيقي | يتطلب probe حيًا | `POST /v1/admin/capabilities/<p>/streaming_text:probe` |
| دقة الكلفة مقابل فاتورة المزوّد | الأسعار من snapshot models.dev بتاريخ `2026-10-15T23:03:24Z` | قارن `GET /v1/usage` بعد يوم استخدام |
| CI على `windows-latest` | لم تُشغَّل Actions من هذا الصندوق | أول push يُشغّل `.github/workflows/ci.yml` |
| بناء صورة Docker | لا Docker daemon في الصندوق | `docker compose up --build` ثم `/healthz` |
| RedisAdmission تحت Redis حقيقي | بلا Redis في الصندوق (الاختبارات تستخدم fake) | `ADMISSION_REDIS_URL=redis://…` ثم `GET /v1/admin/admission` |

## 3. DEFERRED (بقرار)

| البند | القرار |
|---|---|
| PostgreSQL `ce_*` + migrations | ADR-CE-0001 D10 — Phase 2 |
| Authentication / multi-tenant حقيقي | Phase 1 demo tenant؛ المرجع يملك الـ principal seam |
| `[otel]` extra | لا تبعية بلا حاجة؛ `build_tracer` no-op صادق |
| pipeline استيراد Skills (scan/validate/review) | خارج نطاق CE-19 كما في المرجع (R044) |
| `/v1/embeddings`, `/v1/images` | CE-10 Out |

## 4. مخاطر معروفة

- الصندوق ~1 GB RAM: تسبّب في تجميدات متكررة حتى اكتُشف تسرّب 10 MB/runtime من snapshot الكتالوج (مُصلح: cache على مستوى العملية). البوابة الآن تعمل في ~2 دقيقة.
- كل المخازن in-memory: إعادة التشغيل تمسح المحادثات/المهام (مقصود في Phase 1).

## 5. كيف تُعيد إنتاج كل شيء

```bash
scripts/dev.sh                      # أو pwsh scripts/dev.ps1
scripts/gate.sh .venv/bin/python     # GATE GREEN
.venv/bin/python benchmarks/bench.py --n 10000 --streams 500
.venv/bin/python tests/ui/smoke_playwright.py http://localhost:8080
```
