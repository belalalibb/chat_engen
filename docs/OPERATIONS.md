# OPERATIONS — تشغيل chat_engine

## 1. أمر واحد لكل منصة

| المنصة | الأمر | ما يفعله |
|---|---|---|
| Linux/macOS | `scripts/dev.sh [port]` | venv + تثبيت (المرجع مثبّت بـ SHA) + بناء UI + `serve` |
| Windows (PowerShell 7+) | `pwsh scripts/dev.ps1 -Port 8080` | نفس الشيء |
| Docker | `docker compose up --build` | صورة multi-stage non-root؛ UI مضمّنة؛ `/healthz` healthcheck |
| Docker + Redis admission | `docker compose --profile redis up --build` مع `ADMISSION_REDIS_URL=redis://redis:6379/0` | قبول آمن للنسخ المتعددة (`replicas_safe:true`) |

بوابة الإصدار: `scripts/gate.sh <python>` أو `pwsh scripts/gate.ps1` (ruff, mypy, import-linter, secret-scan, ui_no_copy_guard, pytest hermetic).

## 2. متغيّرات البيئة

| المتغيّر | الافتراضي | الأثر |
|---|---|---|
| `PORT` / `HOST` | `8080` / `0.0.0.0` | منفذ/عنوان الخدمة |
| `GROQ_API_KEY`, `GSK_API_KEY` | — | عند وجودها تُربط مزوّدات حقيقية عبر محوّلات المرجع (BYOK بديل: `POST /v1/credentials`) |
| `ADMISSION_REDIS_URL` | — | يبدّل `InMemoryAdmission` إلى `RedisAdmission` (Lua ذرّي) |
| `CE_UI_DIST` | `ui_standalone/dist` | مسار UI المبنية؛ غيابها ⇒ API فقط |
| `CE_FEATURE_OPENAI_COMPAT` | `1` | `0` يعطّل `/v1/chat/completions` و`/v1/openai/*` |
| `CE_FEATURE_APP_FACTORY` | `1` | `0` يعطّل `/v1/apps*` |
| `CE_FEATURE_USAGE` | `1` | `0` يجعل مسجّل الاستهلاك no-op |
| `CE_PRICING_SNAPSHOT` | `1` | `0` يمنع تحميل أسعار models.dev (تبقى overrides اليدوية) |
| `CE_ARTIFACT_DIR` | — (ذاكرة) | مجلد artifacts للـ App Factory على القرص |
| `CE_LOG_LEVEL` | `INFO` | مستوى JSON logs (مع redaction إلزامي) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | يفعّل OpenTelemetry فقط إن وُجد **و** ثُبّت extra `[otel]` (غير مُضاف بعد — no-op صادق) |

**لا توجد أسرار في الصورة أو المستودع.** المفاتيح تُمرَّر من الـ shell أو عبر `/v1/credentials` (تُخزَّن في `InMemorySecretManager` المرجعي، وتُعاد بصمة + last4 فقط).

## 3. الصحة والمراقبة

- `GET /healthz` → `{status, version, upstream_sha, providers[], admission_backend, replicas_safe, compat.not_served[]}`.
- `GET /v1/admin/admission` → حدود/عدادات كل scope + حالة breaker.
- `GET /v1/admin/capabilities/ledger` → `declared ∧ verified ∧ enabled ∧ available_now` لكل (مزوّد، قدرة).
- `GET /v1/usage?group_by=provider_key,day` → استهلاك/كلفة؛ `cost.known:false` تعني لا سعر معروف (لا تخمين).
- Logs: سطر JSON لكل حدث؛ محتوى الرسائل والأسرار **لا يُسجَّل أبدًا** (`chat_engine.observability.redaction`).

## 4. الحالة والبيانات (Phase 1)

كل المخازن in-memory (`InMemoryConversationStore`, executions, ledger, usage, skills assignments). إعادة التشغيل تمسحها — سلوك مقصود في Phase 1؛ PostgreSQL `ce_*` مؤجَّل (ADR-CE-0001 D10). النسخ الاحتياطي الوحيد المطلوب حاليًا: مجلد `CE_ARTIFACT_DIR` إن استُخدم.

## 5. تحويل UNVERIFIED إلى PROVEN بمفتاح المالك

1. `export GROQ_API_KEY=...` (أو `GSK_API_KEY`) ثم `scripts/dev.sh`.
2. `GET /healthz` يجب أن يُظهر المزوّد في `providers[]`.
3. `POST /v1/admin/capabilities/<provider>/chat:probe` ثم `.../streaming_text:probe` → السجل يصبح `verified`.
4. `POST /v1/chat/stream {"messages":[{"role":"user","content":"hi"}],"model":"<model_key>"}` → أحداث `message_delta` حقيقية.
5. `GET /v1/usage` → سجل بتكلفة من `pricing_ref` (models.dev) — قارن مع فاتورة المزوّد.

## 6. استكشاف الأخطاء

| العرض | السبب المرجّح | الإجراء |
|---|---|---|
| `not_found: No eligible model candidates` مع `reason: cooldown (quota_exceeded)` | المزوّد أعاد 429/حصة؛ `ResourceSignalBoard` المرجعي وضعه في cooldown | انتظر، أو اختر `model` آخر، أو `cost_tiered` يختار تلقائيًا ما يمكن توجيهه |
| `admission_denied` 429 | حد rpm/tpm/concurrency/budget للـ scope | `GET /v1/admin/admission`؛ ارفع الحد عبر metadata المزوّد |
| `alias_unconfirmed` 422 | اسم النموذج له aliases مقترحة فقط | `POST /v1/catalog/aliases/{alias}:decide` |
| `unsupported_capability` 422 | القدرة غير `verified`/`enabled` | probe ثم enable من شاشة القدرات |
| UI تُظهر 404 على `/` | لم تُبنَ `ui_standalone/dist` | `cd ui_standalone && npm install && npx vite build` |
