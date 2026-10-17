# CHAT_ENGINE_TASKS — خطة التنفيذ المرحلية (CE-00 → CE-18)

```text
STATUS:        PROPOSED — لا تنفيذ لأي مهمة بعد CE-00 قبل OWNER_APPROVAL = YES في CHAT_ENGINE_STATE.md
UPSTREAM_REF:  general_ai_core @ 0a13556305bcf40cbf1b750d7ef1d81f7850cabf  (مثبّت؛ ترقية = قرار في الـ ledger)
TARGET:        https://github.com/belalalibb/chat_engen.git
INPUTS:        CHAT_ENGINE_REALITY_AUDIT.md · docs/adr/ADR-CE-0001-architecture-lock.md · CHAT_ENGINE_REFERENCE_ASSESSMENT.md
RULES:         additive-only على عقود المرجع · لا نسخ UI · لا تبعية جديدة بلا إثبات · لا أسرار في logs/docs ·
               وحدة عمل واحدة في كل مرة · commit + push بعد كل وحدة · تحديث CHAT_ENGINE_STATE.md عند checkpoint مُتحقَّق فقط
```

> **ملاحظة ترقيم:** الترقيم CE-00..CE-18 يتبع رسم التبعيات في ADR-CE-0001 (D0→D10) وأولويات الفجوات P0→P2 في التدقيق. إن اختلف اسم أو ترتيب أي مهمة عمّا في §19 من الـ MASTER PROMPT، يُصحَّح هنا **قبل** الموافقة — الترقيم هنا هو المرجع التشغيلي بعدها.

## 0. خريطة التبعيات والمراحل

```text
PHASE 0  AUDIT        CE-00 ────────────────────────────────────────────┐ (gate: owner approval)
PHASE 1  FOUNDATION   CE-01 → CE-02 → CE-03                             │
PHASE 2  CATALOG      CE-04 → CE-05 → CE-06                             │  كل مهمة: release gate (D8)
PHASE 3  RUNTIME      CE-07 → CE-08 → CE-09 → CE-10                     │  + ledger row + push
PHASE 4  SURFACES     CE-11 (SDK) → CE-12 (multi-model) → CE-13 (UI)    │
PHASE 5  PLATFORM     CE-14 (App Factory) → CE-15 (observability/usage) │
PHASE 6  SHIP         CE-16 (packaging/CI/Windows) → CE-17 (scale 10k) → CE-18 (release gate + final verification)
```

| المهمة | يعتمد على | يُغلق فجوة | قرار ADR |
|---|---|---|---|
| CE-01 Contract Freeze & Compatibility Map | CE-00 | P1-4 | D8 |
| CE-02 Dependency Boundary & Skeleton | CE-01 | P1-6 | D0, D3 |
| CE-03 Context Handoff Contract | CE-02 | P0-4 | D4 |
| CE-04 Canonical Model Identity & Catalog Index | CE-02 | P0-2, P0-6 | D1 |
| CE-05 Provider Lifecycle, OpenAI-compatible Onboarding & BYOK | CE-04 | P0-3, P1-3 | D2 |
| CE-06 Capability Ledger & Probes | CE-05 | P1-2 | D6 |
| CE-07 Distributed Admission & Idempotency | CE-04 | P1-1, P2-1 | D5 |
| CE-08 Token Streaming (SSE) | CE-03, CE-06, CE-07 | P0-1 | D7 |
| CE-09 Conversations, Projects & Summarization | CE-03, CE-08 | P0-5, P1-5 | D4, D10 |
| CE-10 OpenAI-compatible API Surface & Error Codes | CE-08, CE-09 | P1-4, P2-5 | D3, D8 |
| CE-11 SDKs & Consumer Example | CE-10 | — | D3 |
| CE-12 Multi-model Orchestration (handoff/ensemble) | CE-09, CE-07 | — | D4 |
| CE-13 Standalone UI | CE-10, CE-08 | — | D9 |
| CE-14 App Factory Execution | CE-12 | P2-3 | — |
| CE-15 Usage, Cost & Observability | CE-07, CE-09 | — | D5 |
| CE-16 Packaging, CI, Docker & Windows | CE-10 | P2-4 | D8 |
| CE-17 Scale Validation @10k | CE-04, CE-07, CE-16 | P0-6 | D1, D5 |
| CE-18 Release Gate & Final Verification | الكل | — | D8 |

**القالب الموحّد لكل مهمة:** الهدف · النطاق (In/Out) · التبعيات · الملفات · الاختبارات · معايير القبول · المخاطر · Rollback.

---

## CE-00 — Reality Audit & Gate Check  ✅ (مكتمل — بانتظار الموافقة)

- **الهدف:** معرفة ما هو مُثبت/جزئي/عقد-فقط/قالب/مفقود/معطّل في المرجع قبل أي كود.
- **النطاق:** قراءة المرجع @0a13556، تشغيل `apps.cli describe/routes`، smoke محلي hermetic، pytest hermetic، scale probe 1k/10k، مراجعة 7 مستودعات خارجية، ADR القفل المعماري، خطة المهام هذه.
- **الملفات:** `CHAT_ENGINE_REALITY_AUDIT.md`, `CHAT_ENGINE_REFERENCE_ASSESSMENT.md`, `docs/adr/ADR-CE-0001-architecture-lock.md`, `CHAT_ENGINE_TASKS.md`, `CHAT_ENGINE_STATE.md`, `evidence/ce00/*`.
- **الإثبات:** pytest hermetic `1656 passed, 15 skipped` (ملف الأدلة أُعيد توليده كاملًا)؛ scale probe N=10000: explicit p95 284ms، AUTO p50 664ms، catalog 404ms.
- **معيار القبول:** موافقة المالك على الوثائق الأربع. **لا كود منتج قبلها.**
- **Rollback:** لا شيء للتراجع عنه (وثائق فقط).

---

## CE-01 — Contract Freeze & Compatibility Map

- **الهدف:** تثبيت "ما يجب ألّا ينكسر" آليًا قبل كتابة أي سطر منتج.
- **In:** نسخ `contract_freeze_baseline.json` للمرجع (15 modules/116 contracts/46 routes) إلى `compat/upstream_freeze_baseline_0a13556.json`؛ سكربت اشتقاق يعيد بناء الأشكال من الحزمة المثبّتة ويقارنها؛ خريطة توافق `docs/COMPATIBILITY_MAP.md` تربط كل عقد/مسار بالمهمة التي تلمسه (قراءة فقط/إضافة/لا تلمس)؛ قائمة `ErrorCode` الحالية (11) + الأكواد المقترحة الإضافية مُعلَّمة `proposed`؛ golden snapshots لـ `/v1/execute` (sync, async, 4 حالات خطأ) من المرجع عبر `apps.main` محليًا (hermetic).
- **Out:** أي تغيير في المرجع؛ أي كود runtime في chat_engen.
- **التبعيات:** CE-00، موافقة المالك.
- **الملفات:** `compat/upstream_freeze_baseline_0a13556.json`, `compat/golden/execute/*.json`, `compat/derive_upstream_baseline.py`, `tests/compat/test_upstream_freeze.py`, `docs/COMPATIBILITY_MAP.md`.
- **الاختبارات:** `test_upstream_freeze` يفشل عند أي اختلاف شكل؛ `test_golden_loadable` يتحقق أن كل snapshot يُفكَّك بعقود المرجع (`extra=forbid`).
- **القبول:** baseline مطابق 100% للمرجع؛ خريطة التوافق تغطي الـ 46 مسارًا و116 عقدًا؛ gate أخضر.
- **المخاطر:** اختلاف بيئة الاشتقاق (python version) يغيّر ترتيب الحقول → نثبّت sort keys.
- **Rollback:** حذف `compat/` — لا أثر على أي شيء آخر.

---

## CE-02 — Dependency Boundary & Project Skeleton

- **الهدف:** مشروع `chat_engine` قابل للتثبيت من clone نظيف، يستهلك المرجع كـ git dependency مثبّت بـ SHA، مع حارس معماري.
- **In:** `pyproject.toml` (`chat-engine`, python ≥3.11، تبعية `ai-orchestration-platform @ git+...@0a13556`)؛ حزمة `chat_engine/` (`api/`, `composition/`, `catalog/`, `handoff/`, `admission/`, `streaming/`, `conversations/`, `compat/`, `migrations/`)؛ import-linter: `chat_engine.*` يستورد فقط `core.*`, `providers.real.*`, `providers.common.*`, `infrastructure.*`؛ **ممنوع** `apps.*` و`ui/`؛ composition root يركّب `ProviderRegistry/ModelRegistry/BindingRegistry/SimpleScoringRouter/ExecutionService/ResourceSignalBoard` مباشرة؛ `POST /v1/execute` بنفس `ExecuteRequest/ExecuteResponse` (إعادة استخدام `ExecutionService`) + `GET /healthz` يُظهر `upstream_sha`, `admission_backend`, `replicas_safe`؛ `python -m chat_engine serve`؛ ruff/mypy/pytest config؛ secret-scan script (أنماط `gsk_`, `ghp_`, `sk-`, `AKIA`, `xox`).
- **Out:** أي ميزة جديدة؛ أي استيراد من `apps.api`.
- **التبعيات:** CE-01.
- **الملفات:** `pyproject.toml`, `.importlinter`, `chat_engine/__main__.py`, `chat_engine/composition/runtime.py`, `chat_engine/api/app.py`, `chat_engine/api/routes_execute.py`, `scripts/secret_scan.py`, `tests/arch/test_no_apps_import.py`, `tests/api/test_execute_compat.py`.
- **الاختبارات:** golden CE-01 تُعاد ضد `chat_engine.api` وتتطابق byte-for-byte على الحقول الثابتة (مع تطبيع `request_id`/timestamps)؛ `lint-imports` يمر؛ اختبار يستورد `apps` داخل `chat_engine` عمدًا ويتأكد من فشل الحارس.
- **القبول:** `pip install -e ".[dev]"` من clone نظيف ينجح؛ `/v1/execute` متوافق؛ `/healthz` يعمل؛ gate أخضر.
- **المخاطر:** تصادم أسماء الحزم العامة (`core`) مع مشروع المستهلك (R1) → يُوثَّق ويُختبر في CE-11؛ تثبيت git dependency يحتاج شبكة في CI → cache wheel.
- **Rollback:** الحزمة مستقلة؛ حذفها لا يمس المرجع.

---

## CE-03 — Context Handoff Contract (`HandoffPacket v1`)

- **الهدف:** إيصال التاريخ والقيود ونتائج الأدوات كـ `payload["messages"]` (OpenAI-compatible) بدل JSON داخل system message، مع عقد انتقال محايد بين النماذج.
- **In:** `chat_engine.handoff.contracts`: `CanonicalMessage{role, parts[], tool_call_id?, name?}`, `ContentPart{text|image_ref|file_ref|tool_call|tool_result}`, `HandoffPacket{objective, constraints, messages, tool_results, decisions, attachments, progress, summary, output_spec, budget}`؛ `HandoffCompiler` يحوّل packet → `payload["messages"]` **مع إبقاء** `payload["ask"]`/`payload["context"]` (إضافة لا كاسرة)؛ سياسة قص حسب `Model.context_window` (القيود/القرارات/tool_results لا تُقص قبل التاريخ القديم)؛ `payload["handoff_packet_id"]` للتتبع.
- **Out:** التلخيص الآلي (CE-09)؛ أي تعديل في `core.contracts.conversation`/`context`.
- **التبعيات:** CE-02.
- **الملفات:** `chat_engine/handoff/contracts.py`, `chat_engine/handoff/compiler.py`, `chat_engine/handoff/truncation.py`, `tests/handoff/test_compiler.py`, `tests/handoff/test_truncation.py`, `tests/handoff/test_legacy_adapter_ignores_messages.py`.
- **الاختبارات:** golden: مهمة 3 مراحل تنتقل A→B؛ B يستلم نفس `objective/constraints/tool_results` ولا يعيد تنفيذ أداة نُفّذت؛ fake adapter قديم يتجاهل `messages` ويعمل بـ `ask` كما كان؛ قص 50 رسالة إلى حد نافذة 8k مع إبقاء القيود.
- **القبول:** `ProviderGenerateRequest` يمر بلا تغيير (`extra=forbid` على العقود الجديدة فقط)؛ gate أخضر.
- **المخاطر:** حساب tokens تقريبي بلا tokenizer لكل نموذج → heuristic موثّق + `budget.max_tokens_hint`.
- **Rollback:** الـ compiler اختياري؛ إيقافه يعيد `payload` القديم فقط.

---

## CE-04 — Canonical Model Identity & Catalog Index

- **الهدف:** نموذج واحد في الكتالوج مهما تعدد المزوّدون، بدون لمس `Model.model_key` أو الـ Router، مع فهرس يحل مشكلة p95 @10k.
- **In:** `CanonicalModel`, `ModelAlias{alias, canonical_id, provider_key?, decision∈{confirmed,proposed,rejected}, confidence, source, decided_by, decided_at}`؛ `CatalogIndex` يلفّ الـ registries بفهارس `model_key→bindings`, `canonical_id→model_keys`, `provider_key→models` (بناء O(N) مرة، تحديث تزايدي عند `register/replace/remove`)؛ `CanonicalResolver`: `canonical_id` → `model_key`s المؤكدة → `explicit_models + fallback_chain` أو `explicit_model + same_model_different_provider` للـ Router **كما هو**؛ مُقترِح aliases من تشابه الأسماء + snapshot `models.dev/api.json` (ملف ثابت في `compat/catalogs/`) ⇒ `proposed` فقط؛ `GET /v1/catalog/models` (صف واحد لكل canonical مع `configured_provider_count`, `eligible_provider_count`، pagination + filters)؛ `POST /v1/catalog/aliases/{id}:decide`؛ تخزين in-memory + جداول `ce_canonical_model`, `ce_model_alias` (D10).
- **Out:** تعديل `BindingRegistry` في المرجع (يُقدَّم لاحقًا كـ PR additive إن أثبت CE-17 الحاجة)؛ الدمج التلقائي.
- **التبعيات:** CE-02.
- **الملفات:** `chat_engine/catalog/{contracts,index,resolver,alias_proposer,store}.py`, `chat_engine/api/routes_catalog.py`, `chat_engine/migrations/versions/0001_catalog.py`, `compat/catalogs/models_dev_snapshot_<date>.json`, `tests/catalog/*`, `evidence/ce04/scale_probe_index.txt`.
- **الاختبارات:** نموذج من 1,000 مزوّد يظهر مرة واحدة بعدّاد 1,000؛ `proposed` لا يغيّر العدّاد؛ `confirmed` يغيّره؛ `rejected` يعيد الفصل؛ scale probe CE-00 يُعاد عبر `CatalogIndex`: هدف catalog p95 < 50ms @10k، explicit 3-provider p95 < 10ms؛ AUTO يُقاس ويُسجَّل (الهدف النهائي في CE-17).
- **القبول:** الأهداف المقاسة مُحققة أو مُسجَّلة بفجوة واضحة لـ CE-17؛ لا تغيير في عقود المرجع؛ gate أخضر.
- **المخاطر:** تزامن الفهرس مع الـ registries عند التعديل المباشر → الوصول للـ registries فقط عبر `CatalogIndex` (حارس اختبار)؛ جودة snapshot models.dev → `source` و`verified_at` على كل سجل.
- **Rollback:** الفهرس طبقة قراءة؛ إزالته تعيد المسح الخطي.

---

## CE-05 — Provider Lifecycle, OpenAI-compatible Onboarding & BYOK

- **الهدف:** إضافة مزوّد OpenAI-compatible مباشرة (بلا gateway) وتسجيل مفاتيح المستخدم (BYOK) بمرجع فقط، مع سجل إصدارات append-only لإعداد المزوّد.
- **In:** `ProviderConfigRevision{provider_key, revision, config_hash, state, changed_by, changed_at, reason}` بحالات `draft → validating → discovered → verified → enabled | failed | disabled | archived`؛ `OpenAICompatibleAdapter` يطبّق `ProviderAdapterPort` كاملًا (manifest ديناميكي من `base_url`, `validate_credential` عبر `/models`, `discover_models`, `generate` sync، `normalize_error` إلى الـ 12 فئة)؛ onboarding `POST /v1/admin/providers` (draft) → `:validate` → `:discover` → `:enable` → `:disable` → `:rollback {revision}`؛ BYOK: `POST /v1/credentials` يستقبل المفتاح **مرة واحدة** ويخزّنه عبر `SecretStorePort` (env-backed افتراضيًا، Vault اختياري كالمرجع) ويعيد `credential_ref` فقط؛ `CredentialPolicy` من المرجع كما هو؛ `owner_type∈{user,tenant,platform}`؛ تركيب `ResourceSelector` الموجود في المرجع.
- **Out:** أي مزوّد غير OpenAI-compatible (templates المرجع تبقى كما هي)؛ تغيير `ProviderManifest`.
- **التبعيات:** CE-04.
- **الملفات:** `chat_engine/providers/openai_compatible/{adapter,manifest,errors}.py`, `chat_engine/providers/lifecycle/{contracts,service,store}.py`, `chat_engine/credentials/{service,store}.py`, `chat_engine/api/routes_admin_providers.py`, `chat_engine/api/routes_credentials.py`, `chat_engine/migrations/versions/0002_providers_credentials.py`, `tests/providers/openai_compatible/*` (fake HTTP server)، `tests/lifecycle/*`, `tests/credentials/test_no_secret_leak.py`.
- **الاختبارات:** دورة حياة كاملة ضد fake OpenAI server (200/401/429/5xx/timeout)؛ `rollback` يعيد `config_hash` السابق؛ secret-scan على logs/responses بعد كل اختبار (المفتاح لا يظهر إطلاقًا)؛ `validate_credential` بمفتاح خاطئ ⇒ `failed` بسبب مُطبَّع؛ onboarding المرجع عبر gateway يبقى يعمل (golden).
- **القبول:** مزوّد حقيقي يُضاف بثلاث طلبات؛ لا سر في أي log/response/DB column غير مشفّر؛ gate أخضر.
- **المخاطر:** تنوّع "OpenAI-compatible" (حقول ناقصة/`usage` غائب) → tolerant parser + `limits_source=conservative_default`؛ **لا مفتاح حي للاختبار (R3)** → كل ادعاء live يبقى `UNVERIFIED` حتى يُشغّل المالك بمفتاحه.
- **Rollback:** `archive` المزوّد + تعطيل المسارات بعلم `CE_FEATURE_ONBOARDING=0`.

---

## CE-06 — Capability Ledger & Probes

- **الهدف:** قدرة تُنفَّذ فقط إذا `declared ∧ verified.passed ∧ enabled ∧ available_now`، بسجل جانبي لا يلمس `ProviderCapabilities`.
- **In:** `CapabilityLedgerEntry` (D6)؛ تحميل الـ14 من manifest كـ `declared`؛ قدرات إضافية (`streaming_text, tool_calling, structured_output, json_mode, vision_input, long_context_128k, …`) تعيش في الـ ledger فقط؛ `ProbeRunner` بـ probes صغيرة لكل قدرة (ضد fake في الاختبارات؛ ضد المزوّد الحقيقي عند التفعيل بأمر المشرف) تُنتج `evidence_ref`؛ `stale` بعد TTL؛ `CapabilityGate` يُستدعى قبل التوجيه ويعيد `unsupported_capability` مع تفاصيل؛ `GET /v1/admin/capabilities/ledger`, `POST .../probe`, `POST .../enable|disable`؛ جدول `ce_capability_ledger`.
- **Out:** تغيير `ProviderCapabilities`؛ `/v1/admin/capabilities` المرجعي (يبقى لقدرات المنصة).
- **التبعيات:** CE-05.
- **الملفات:** `chat_engine/capabilities/{contracts,ledger,probes,gate,store}.py`, `chat_engine/api/routes_admin_capabilities.py`, `chat_engine/migrations/versions/0003_capability_ledger.py`, `tests/capabilities/*`.
- **الاختبارات:** manifest قديم يمر بلا تعديل؛ قدرة بلا probe ناجح تُرفض؛ بعد probe + تفعيل تمر؛ `failed` تُعزل تلقائيًا؛ `stale` تُعيد الطلب للـ probe لا للرفض الفوري (سياسة موثّقة).
- **القبول:** قاعدة التنفيذ مُثبتة بالاختبار على الحالات الأربع × 3 قدرات؛ gate أخضر.
- **المخاطر:** probes تستهلك رصيد المزوّد → حد يومي لكل مزوّد + تشغيل يدوي فقط للـ live.
- **Rollback:** `CE_CAPABILITY_GATE=off` يعيد سلوك المرجع (manifest فقط).

---

## CE-07 — Distributed Admission & Idempotency

- **الهدف:** قبول الطلبات بحجز ذري عبر أبعاد `rpm/tpm/concurrency/budget` على scopes متعددة، آمن لعدة replicas، مع idempotency.
- **In:** `AdmissionPort{reserve, settle, release}` (D5)؛ `InMemoryAdmission` يعيد استخدام `ResourceSignalBoard` (افتراضي)؛ `RedisAdmission` بـ Lua atomic وـ `ADMISSION_REDIS_URL`؛ حدود مجهولة ⇒ `rpm=60, concurrency=4` موسومة `limits_source=conservative_default`؛ `Retry-After` ≤60s يُحترم وأطول ⇒ failover (كالمرجع)؛ circuit breaker لكل scope (30s/60s)؛ `allowed_fails_policy` نسبي (نمط litellm) يُقاس مقابل cooldown الثابت؛ `idempotency_key` + hash 24h، نفس المفتاح بـ hash مختلف ⇒ 409؛ `/healthz.replicas_safe = backend != in_memory`؛ (اختياري P2-1) producer لـ `admin_defined_chain` من إعدادات المشرف.
- **Out:** Redis إلزامي؛ تغيير `ExecutionService` (يُلفّ بـ decorator/hook فقط).
- **التبعيات:** CE-04.
- **الملفات:** `chat_engine/admission/{port,memory,redis_lua,breaker,idempotency}.py`, `chat_engine/admission/lua/reserve.lua`, `tests/admission/*` (`fakeredis` **dev-only** إن أُقرّت كتبعية اختبار بعد إثبات؛ وإلا Redis حقيقي عبر `REDIS_TEST_URL` مع skip)، `evidence/ce07/retry_storm.txt`.
- **الاختبارات:** عمليتان + fake clock على `RedisAdmission` لا تتجاوزان RPM؛ retry storm 500 طلب يثبت jitter وعدم تجاوز concurrency؛ 409 على تعارض idempotency؛ `admin_defined_chain` ينتج سلسلة صالحة يقبلها الـ Router.
- **القبول:** لا تجاوز لأي حد في الاختبارات التنافسية؛ overhead admission p95 < 2ms in-memory، < 5ms Redis محلي؛ gate أخضر.
- **المخاطر:** إضافة `fakeredis` تبعية → قرار صريح في الـ ledger مع إثبات أو استخدام Redis حقيقي اختياري.
- **Rollback:** الافتراضي in-memory؛ إزالة `ADMISSION_REDIS_URL` تعيد سلوك المرجع.

---

## CE-08 — Token Streaming (SSE)

- **الهدف:** `stream=true` يعمل فعليًا (token deltas) عبر port اختياري دون تغيير `ProviderAdapterPort`.
- **In:** `StreamingAdapterPort.stream(request) -> AsyncIterator[StreamChunk]`؛ تنفيذ لـ `OpenAICompatibleAdapter` (SSE parser متسامح) و**Groq** عبر wrapper في chat_engen (المرجع يرسل `stream:false` — لا يُعدَّل)؛ `POST /v1/chat/stream` SSE بأحداث `started, message_delta, tool_call_start|delta|end, usage, heartbeat, completed, failed, cancelled` بـ `seq`/`event_id`؛ فشل قبل أول delta ⇒ failover صامت؛ بعده ⇒ `failed{reason: stream_interrupted, resumable: true}`؛ `POST /v1/chat/stream/{id}:resume` بـ `HandoffPacket + delivered_until_seq`؛ إلغاء ⇒ `cancelled` + إلغاء upstream + تسوية usage الجزئي عبر `AdmissionPort.settle`؛ timeouts first-byte 30s / idle 60s / total `deadline_ms`؛ backpressure بـ buffer محدود؛ `stream_fallback=buffer` ⇒ دفعة واحدة موسومة `synthesized: true`؛ WebSocket extension point **غير مُنفَّذ** (موثّق فقط)؛ `/v1/execute` بـ `stream=true` يبقى 422 (توافق) مع `hint` نحو `/v1/chat/stream`.
- **Out:** realtime audio؛ WebSocket؛ تغيير `generate`.
- **التبعيات:** CE-03, CE-06 (`streaming_text` probe), CE-07.
- **الملفات:** `chat_engine/streaming/{port,events,service,sse,buffer,resume}.py`, `chat_engine/providers/openai_compatible/stream.py`, `chat_engine/providers/groq_stream.py`, `chat_engine/api/routes_chat_stream.py`, `tests/streaming/*` (fake streaming adapter بخمس حالات)، `evidence/ce08/sse_capture.txt`.
- **الاختبارات:** الحالات الخمس (يدعم / لا يدعم / فشل قبل أول delta / فشل بعده / إلغاء)؛ resume لا يعيد محتوى مُسلَّمًا؛ `synthesized:true` موجود عند الـ fallback؛ heartbeat كل 15s عند الخمول؛ ترتيب `seq` صارم.
- **القبول:** الحالات الخمس خضراء؛ أحداث المرجع الستة على `/v1/executions/{id}/events` بلا تغيير؛ gate أخضر.
- **المخاطر:** اختلاف SSE بين المزوّدين → normalizer لكل مزوّد + fixtures مسجَّلة؛ **لا مفتاح حي** → Groq stream يبقى `UNVERIFIED` حتى تشغيل المالك.
- **Rollback:** المسار الجديد معزول؛ `CE_FEATURE_STREAMING=0` يُرجع 422 كالمرجع.

---

## CE-09 — Conversations, Projects & Summarization

- **الهدف:** واجهة شات حقيقية: محادثات ومشاريع دائمة، تاريخ يُبنى كـ `HandoffPacket`، تلخيص منظّم ضمن token budget.
- **In:** `Project{id, tenant_id, name, default_policy_ref?, system_instructions?}`, `Conversation{id, project_id?, title, model_selection, created_at}`, `Turn{id, conversation_id, role, parts, usage, provider_key, model_key, handoff_packet_id, seq}`؛ CRUD `/v1/projects`, `/v1/conversations`, `/v1/conversations/{id}/turns`؛ `POST /v1/chat` (sync) و`/v1/chat/stream` يقرآن التاريخ من المخزن ويبنيان packet (CE-03)؛ `Summarizer` يُنتج `summary` منظّمًا (objective/decisions/open items — **لا chain-of-thought**) عند تجاوز `budget`، بنموذج قابل للتهيئة ومُسجَّل كـ turn من نوع `summary`؛ جداول `ce_project, ce_conversation, ce_turn, ce_summary`؛ in-memory بلا `DATABASE_URL`.
- **Out:** مشاركة/صلاحيات متقدمة؛ بحث دلالي/vector DB.
- **التبعيات:** CE-03, CE-08.
- **الملفات:** `chat_engine/conversations/{contracts,store_memory,store_pg,service,summarizer}.py`, `chat_engine/api/routes_chat.py`, `chat_engine/api/routes_conversations.py`, `chat_engine/migrations/versions/0004_conversations.py`, `tests/conversations/*`.
- **الاختبارات:** محادثة 40 دورة تُلخَّص عند الحد وتبقى القيود؛ نفس الطلب عبر memory و PG يعطي نفس الناتج (parametrized، PG بـ skip إن غاب `DATABASE_URL`)؛ turn من stream مقطوع يُحفظ جزئيًا بعلامة `partial`.
- **القبول:** شات متعدد الدورات يعمل end-to-end بـ fake provider؛ gate أخضر.
- **المخاطر:** التلخيص يستهلك نموذجًا → سياسة `summarize_with` افتراضيًا أرخص نموذج مؤهل عبر الـ Router.
- **Rollback:** الجداول `ce_*` مستقلة؛ alembic downgrade.

---

## CE-10 — OpenAI-compatible API Surface, Error Codes & OpenAPI

- **الهدف:** عملاء OpenAI SDK القياسيون يعملون ضد chat_engine، وأكواد الأخطاء مكتملة ومُعلنة.
- **In:** `POST /v1/chat/completions` (subset: messages, model=`canonical_id`|`model_key`, stream, temperature, max_tokens, tools, response_format) يُترجَم إلى `HandoffPacket`+Router؛ `GET /v1/models` بصيغة OpenAI من الكتالوج؛ mapper أخطاء OpenAI-style ⇄ `ErrorCode` المرجعي؛ أكواد إضافية **في chat_engine فقط** (`unsupported_capability`, `admission_denied`, `idempotency_conflict`, `stream_interrupted`, `alias_unconfirmed`) مع ADR-CE-0002 يقترح إضافتها append-only للمرجع لاحقًا؛ OpenAPI كامل غير admin-gated على `/openapi.json` (P2-5) مع `x-ce-stability: stable|experimental` لكل مسار؛ `docs/API.md`.
- **Out:** endpoints غير chat (`/v1/embeddings`, `/v1/images` تبقى `FUTURE`).
- **التبعيات:** CE-08, CE-09.
- **الملفات:** `chat_engine/api/routes_openai_compat.py`, `chat_engine/api/errors.py`, `chat_engine/api/openapi.py`, `docs/adr/ADR-CE-0002-error-codes.md`, `docs/API.md`, `tests/api/test_openai_sdk_hermetic.py`, `tests/api/test_error_mapping.py`.
- **الاختبارات:** عميل `openai` Python الرسمي (dev dependency للاختبار فقط — مُثبت الإصدار) ضد التطبيق hermetic: sync + stream + خطأ 429/401/422؛ كل `ErrorCode` مرجعي له mapping؛ OpenAPI يُتحقق بـ schema validator؛ golden CE-01 تمر.
- **القبول:** SDK يعمل بلا تعديل؛ لا كود خطأ بلا توثيق؛ gate أخضر.
- **المخاطر:** إضافة `openai` كتبعية اختبار → مقيّدة بـ `[test]` extra وقرار في الـ ledger.
- **Rollback:** المسار معزول بعلم `CE_FEATURE_OPENAI_COMPAT`.

---

## CE-11 — SDKs & Consumer Example (إثبات الاستقلال)

- **الهدف:** مستهلك خارجي يستخدم المحرك بلا أي ملف من `ui/`/`apps/` المرجعية.
- **In:** `sdk/python/chat_engine_client` (sync + async، typed من OpenAPI، retry/backoff يحترم `Retry-After`، SSE client)؛ `sdk/typescript/` (fetch + EventSource، zero-dependency)؛ `examples/consumer_project/` مشروع مستقل يثبّت الحزمة ويشغّل `python -m chat_engine serve` ويُجري محادثة multi-turn + stream؛ اختبار R1 (تصادم اسم `core`) داخل المثال.
- **Out:** نشر على PyPI/npm (CE-16 يحضّر فقط).
- **التبعيات:** CE-10.
- **الملفات:** `sdk/python/**`, `sdk/typescript/**`, `examples/consumer_project/**`, `tests/sdk/*`.
- **الاختبارات:** المثال يعمل في CI من clone نظيف؛ SDK ضد fake server يغطي كل المسارات `stable`.
- **القبول:** المثال لا يستورد شيئًا من المرجع خارج الحزمة؛ gate أخضر.
- **المخاطر:** انجراف SDK عن OpenAPI → توليد types من `/openapi.json` + اختبار مطابقة.
- **Rollback:** حزم مستقلة.

---

## CE-12 — Multi-model Orchestration (handoff / ensemble)

- **الهدف:** استراتيجيات تتجاوز `fallback_chain`/`parallel_compare` الموجودتين، مبنية على `HandoffPacket` بدون تغيير `MultiModelExecutor`.
- **In:** `chat_engine.orchestration`: `sequential_handoff` (A ينتج → B يكمل بـ packet)، `judge_select` (N مرشحون + حَكَم بمعايير `output_spec`)، `cost_tiered` (رخيص أولًا، ترقية عند فشل `output_spec`)؛ كل استراتيجية تنتج `OrchestrationTrace` قابل للتفسير (أي نموذج، لماذا، كلفة)؛ تُركَّب فوق `ExecutionService`/`MultiModelExecutor` (إعادة استخدام)؛ تمرّ بـ `AdmissionPort`؛ `POST /v1/chat` يقبل `strategy`.
- **Out:** تدريب/تعلّم router (RouteLLM = FUTURE_REFERENCE)؛ تغيير `SelectionStrategy` المرجعي.
- **التبعيات:** CE-09, CE-07.
- **الملفات:** `chat_engine/orchestration/{contracts,sequential,judge,cost_tiered,trace}.py`, `tests/orchestration/*`.
- **الاختبارات:** golden D4 (A→B بعد 429 مع نفس packet)؛ `judge_select` يختار حسب `output_spec` على fakes؛ `cost_tiered` يرقّي مرة واحدة فقط؛ الـ trace كامل وقابل للتسلسل.
- **القبول:** الاستراتيجيات الثلاث خضراء بـ traces؛ `parallel_compare` المرجعي بلا تغيير؛ gate أخضر.
- **المخاطر:** تضاعف الكلفة → حد `max_candidates` وميزانية admission.
- **Rollback:** الاستراتيجيات اختيارية؛ الافتراضي سلوك المرجع.

---

## CE-13 — Standalone UI (`ui_standalone/`)

- **الهدف:** واجهة شات + إدارة مزوّدين/كتالوج مستقلة بالكامل، بلا نسخ أي ملف/لون/تخطيط من `ui/*` المرجعية.
- **In:** TypeScript + Vite + Lit + CSS custom properties (D9)؛ شاشات: Chat (stream, cancel, resume, model picker من الكتالوج canonical)، Conversations/Projects، Catalog (virtualized table @10k)، Providers (دورة الحياة + onboarding OpenAI-compatible + BYOK بإدخال المفتاح مرة واحدة بلا تخزين في المتصفح)، Capabilities ledger، Admission/Health؛ API chat_engine فقط عبر `fetch`/`EventSource`؛ i18n (ar/en) مع RTL؛ design tokens خاصة؛ Playwright smoke.
- **Out:** أي أصل من المرجع؛ إطار ثقيل؛ تخزين أسرار في المتصفح.
- **التبعيات:** CE-10, CE-08.
- **الملفات:** `ui_standalone/**` (`src/components`, `src/api`, `src/i18n`, `src/tokens.css`), `tests/ui/playwright/*`, `scripts/ui_no_copy_guard.py` (يقارن hashes/أسماء ضد `ui/` المرجعي ويفشل عند أي تطابق).
- **الاختبارات:** Playwright: محادثة stream تعرض deltas وتُلغى وتُستأنف؛ جدول 10k يبقى < 16ms/frame في القياس؛ `ui_no_copy_guard` أخضر؛ lint + typecheck.
- **القبول:** الشاشات الست تعمل ضد التطبيق hermetic؛ لا نسخ؛ gate أخضر.
- **المخاطر:** حجم العمل → تُقسَّم إلى CE-13a (Chat+Catalog) و CE-13b (Admin) في الـ ledger.
- **Rollback:** مجلد مستقل؛ لا يؤثر على API.

---

## CE-14 — App Factory Execution (من خطة بيانات إلى تنفيذ)

- **الهدف:** `core.agent.app_factory` يتجاوز `generation_deferred=True` بتنفيذ فعلي عبر orchestration CE-12 مع حدود واضحة.
- **In:** `AppFactoryRunner` يحوّل خطة الـ factory إلى سلسلة `HandoffPacket`s (spec → scaffold → review) عبر `sequential_handoff`؛ مخرجات كملفات في `ArtifactStore` (local FS / S3-compatible اختياري) بـ `artifact_ref`؛ `POST /v1/apps:generate` async (outbox المرجعي) + `GET /v1/apps/{id}`؛ حدود: لا تنفيذ كود مولَّد، لا شبكة من داخل المخرجات؛ موافقة بشرية (`tool_approval_required`) قبل أي خطوة موسومة `requires_approval`.
- **Out:** sandbox تنفيذ؛ نشر تلقائي.
- **التبعيات:** CE-12.
- **الملفات:** `chat_engine/app_factory/{runner,artifacts,contracts}.py`, `chat_engine/api/routes_apps.py`, `tests/app_factory/*`.
- **الاختبارات:** خطة 3 مراحل تُنتج artifacts بـ fakes؛ خطوة `requires_approval` تتوقف وتُستأنف؛ `generation_deferred` في المرجع بلا تغيير.
- **القبول:** مسار async كامل hermetic؛ gate أخضر.
- **المخاطر:** توقعات "توليد تطبيق كامل" → النطاق موثّق صراحة (scaffold + review).
- **Rollback:** علم `CE_FEATURE_APP_FACTORY`.

---

## CE-15 — Usage, Cost & Observability

- **الهدف:** معرفة من استهلك ماذا وبكم وبأي مزوّد، بسجل أسعار له مصدر وتاريخ تحقق.
- **In:** `UsageRecord{request_id, tenant, project?, conversation?, provider_key, model_key, canonical_id, tokens{in,out,cached?}, latency_ms, status, cost{amount, currency, pricing_ref}}` يُكتب عند `settle`؛ `PricingRecord{provider_key, model_key, input_per_1m, output_per_1m, source, verified_at}` (snapshot models.dev + تجاوز يدوي)؛ `GET /v1/usage` بتجميعات (tenant/project/provider/day)؛ ميزانيات `budget` كبُعد admission (CE-07)؛ OpenTelemetry traces/metrics اختيارية (`OTEL_EXPORTER_OTLP_ENDPOINT`) — بلا أسرار أو محتوى رسائل في spans؛ structured logs مع redaction؛ جدولا `ce_usage`, `ce_pricing`.
- **Out:** فوترة/دفع؛ dashboards خارجية.
- **التبعيات:** CE-07, CE-09.
- **الملفات:** `chat_engine/usage/{contracts,recorder,pricing,aggregates}.py`, `chat_engine/observability/{otel,logging,redaction}.py`, `chat_engine/api/routes_usage.py`, `chat_engine/migrations/versions/0005_usage.py`, `tests/usage/*`, `tests/observability/test_redaction.py`.
- **الاختبارات:** كل تنفيذ (sync/stream/مقطوع) ينتج سجلًا واحدًا بالضبط؛ كلفة محسوبة من `pricing_ref` الصحيح؛ redaction تزيل أنماط الأسرار ومحتوى الرسائل من logs/spans؛ تجاوز الميزانية ⇒ `admission_denied`.
- **القبول:** usage دقيق في الاختبارات التنافسية؛ لا تسرب؛ gate أخضر.
- **المخاطر:** `opentelemetry-*` تبعية جديدة → `[otel]` extra اختياري بقرار ledger.
- **Rollback:** recorder no-op بعلم.

---

## CE-16 — Packaging, CI, Docker & Windows

- **الهدف:** تشغيل من clone نظيف على Linux وWindows وDocker، وبوابة CI تطبّق D8 آليًا.
- **In:** `Dockerfile` (multi-stage، non-root)، `docker-compose.yml` (app + postgres + redis اختياريان)؛ GitHub Actions: lint (ruff) + mypy + lint-imports + pytest (hermetic) + compat/golden + secret-scan + `ui_no_copy_guard` + build UI + Playwright smoke؛ matrix `ubuntu-latest` + `windows-latest` (يثبت أوامر PowerShell التي كانت `UNVERIFIED` في التدقيق §9)؛ `scripts/dev.ps1` و`scripts/dev.sh`؛ إعداد نشر حزم (بلا نشر)؛ `docs/OPERATIONS.md` (env vars، healthz، migrations، backup).
- **Out:** نشر سحابي؛ أسرار في CI (المفاتيح الحية تبقى خارج CI).
- **التبعيات:** CE-10 (وتُحدَّث مع كل مهمة لاحقة).
- **الملفات:** `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `scripts/dev.{sh,ps1}`, `docs/OPERATIONS.md`, `evidence/ce16/ci_run_windows.txt`.
- **الاختبارات:** CI أخضر على النظامين؛ `docker compose up` ثم `/healthz` 200 و `/v1/chat` hermetic 200.
- **القبول:** أمر واحد لكل منصة يُشغّل كل شيء؛ gate أخضر.
- **المخاطر:** git dependency في Docker تحتاج شبكة → wheel cache layer.
- **Rollback:** ملفات بنية فقط.

---

## CE-17 — Scale Validation @10k Providers

- **الهدف:** إثبات الأهداف الأدائية بأرقام مُعادة الإنتاج، وتقرير فجوات واضح إن لم تتحقق.
- **In:** إعادة `evidence/ce00/scale_probe.py` ضد chat_engine كاملًا (CatalogIndex + Admission + Capability gate) على N∈{1k, 10k}: catalog p95، explicit p95، AUTO p95، onboarding 10k مزوّد (وقت التسجيل + الذاكرة)، 1,000 محادثة متزامنة stream بـ fakes، admission تحت تنافس؛ تحسينات مُقاسة (top-K قبل scoring، cache لنتائج الأهلية، ترتيب مسبق)؛ إن ثبت أن العائق داخل `BindingRegistry` المرجعي ⇒ صياغة PR additive + ADR للمرجع (لا دمج هنا)؛ `docs/PERFORMANCE.md`.
- **Out:** تحسينات غير مُقاسة؛ تعديل مباشر في المرجع.
- **التبعيات:** CE-04, CE-07, CE-16.
- **الملفات:** `benchmarks/*`, `evidence/ce17/*`, `docs/PERFORMANCE.md`, `tests/perf/test_budgets.py` (ميزانيات زمنية مع هامش، تُشغَّل في CI بعلم).
- **الأهداف (قابلة للمراجعة بالقياس):** catalog p95 < 50ms، explicit (3 مزوّدين) p95 < 10ms، AUTO p95 < 150ms @10k، ذاكرة < 1.5GB @10k، stream first-byte p95 < 200ms (fake).
- **القبول:** كل هدف إما مُحقق بدليل أو مُسجَّل كفجوة مع خطة؛ gate أخضر.
- **المخاطر:** AUTO scoring خطي بطبيعته → top-K بعد فلترة الأهلية؛ قد يحتاج PR للمرجع (R2).
- **Rollback:** التحسينات خلف أعلام قابلة للإيقاف.

---

## CE-19 — Assignable Skills (طلب المالك 2026-10-15)

- **الهدف:** اختيار Skills وإسنادها إلى Agent Profile أو قالب عمل، تفعيل/تعطيل لكل مشروع أو مهمة، وتسجيل إصدار كل Skill استُخدم في كل Turn.
- **In:** `chat_engine/skills/`: `SkillAssignmentService` فوق `core.roles.registry.SkillRegistry` المرجعي (قاعدة الاختيار: ACTIVE ∧ LOCAL فقط — لا تُعاد كتابتها بل تُستهلك)؛ `AgentProfile{id, name, skills:[{skill_id, version?, enabled}], system_instructions, default_model}`؛ إسناد على مستويات: profile → project → conversation → turn (explicit)، مع قاعدة الدمج: الأضيق يفوز، و`enabled:false` في مستوى أضيق يُعطّل ما ورثه؛ `resolve_for_turn()` يعيد السجلات بنسخة الـ Skill الفعلية المسجَّلة (`version` من الـ registry وقت التنفيذ) وتُحفظ في `Turn.skills_used`؛ Skill `instruction` تُحقن في system message للـ HandoffPacket؛ `GET /v1/skills` بشكل المرجع الحرفي (10 §7) + `/v1/skills/{id}` + `/v1/agent-profiles` CRUD + `/v1/projects/{id}/skills` (assign/enable/disable) + `/v1/conversations/{id}/skills`؛ `GET /v1/templates` متوافق مع المرجع؛ `/v1/admin/skills` تسجيل Skill محلي (manifest 14 §2).
- **Out:** تنفيذ Tools؛ pipeline الاستيراد؛ Capability Firewall.
- **التبعيات:** CE-09 (الـ hook موجود)، CE-13 (شاشة Skills في UI).
- **الملفات:** `chat_engine/skills/{contracts,assignments,service}.py`, `chat_engine/api/skills.py`, `tests/skills/*`.
- **القبول:** Turn يحمل `skills_used=[{skill_id, version, source: profile|project|conversation|explicit}]`؛ تعطيل على مستوى المحادثة يمنع Skill المشروع؛ Skill غير ACTIVE تُرفض بـ 422 `skill_not_selectable`؛ gate أخضر.
- **Rollback:** إزالة `extensions["skills"]` ⇒ ChatService يعمل بلا Skills.

---

## CE-18 — Release Gate & Final Verification

- **الهدف:** إصدار `v0.1.0` قابل للتدقيق: كل ادعاء له دليل، وكل ما لم يُثبت حيًا موسوم.
- **In:** تشغيل كامل لبوابة D8 + golden + compat + secret-scan + ui_no_copy_guard؛ `green_manifest.json` خاص بـ chat_engen (نمط المرجع) بالأرقام الفعلية؛ `CHAT_ENGINE_VERIFICATION_REPORT.md` (ما PROVEN hermetic / ما UNVERIFIED live لعدم وجود مفتاح / ما DEFERRED)؛ تحديث ADR-CE-0001 إلى `ACCEPTED` مع أي انحرافات مُسجَّلة؛ `CHANGELOG.md`؛ وسم git `v0.1.0`؛ قائمة "تشغيل بمفتاح المالك" خطوة بخطوة لتحويل UNVERIFIED إلى PROVEN.
- **Out:** أي ميزة جديدة.
- **التبعيات:** CE-01..CE-17.
- **الملفات:** `engineering/verification/green_manifest.json`, `CHAT_ENGINE_VERIFICATION_REPORT.md`, `CHANGELOG.md`, `CHAT_ENGINE_STATE.md` (PHASE: SHIPPED).
- **القبول:** gate أخضر من clone نظيف على Linux + Windows؛ التقرير بلا ادعاء غير مدعوم؛ المالك يوافق على الوسم.
- **Rollback:** الوسم فقط.

---

## CE-20 — Remote Gateway Providers (طلب المالك 2026-10-17)

**الطلب:** ربط مزوّدات مستودع المالك `GPT-5-6-Sol-code/__gateway-service` (14 مزوّداً خلف Remote Provider Gateway ADR-0008) بـ chat_engine، اختبارها، وتسليم رابط معاينة مع الربط.

**المخرجات:**
- `chat_engine/providers/gateway.py` — ربط من `CE_GATEWAY_*` عبر محوّل المرجع `RemoteGatewayAdapter` (إعادة استخدام، لا نسخ)؛ اكتشاف عبر `/v1/describe`؛ تقرير `bound/skipped` في `/healthz`.
- `tests/providers/test_gateway_binding.py` — hermetic (MockTransport): نصف تهيئة ⇒ خطأ، repr مُنقّى، مسار موصوف يُسجَّل ومسار ميت يُتخطّى، `/v1/chat` من طرف لطرف بغلاف wire صحيح.
- إصلاحان حقيقيان كشفهما الربط الحي: `catalog._i` (models.dev `context: 0`) و AUTO بلا failover.
- `scripts/sandbox_bootstrap.sh` + `scripts/sandbox_run.sh` — بيئة قابلة لإعادة البناء بعد كل reset؛ الأسرار تُولَّد في `~/run` خارج المستودع.
- `docs/OPERATIONS.md §2b`، `evidence/ce20/`.

**نتيجة الفحص الحي (2026-10-17، على sandbox):** 13 مسار مربوط (voyage مُتخطّى: embeddings فقط). يجيب فعلاً: `overchat`, `deepai`, `llamacoder`, `parallel`. يرفض upstream (ليس خطأ chat_engine): `groq` (مفتاح غير مهيأ في البوابة), `freebuff`/`ivycraft`/`runpod_console` (`invalid_credential`), `notegpt` (`auth_expired`), `youmind` (`quota_exceeded`), `syntx`/`tinyfish`/`upstage` (`provider_unavailable`). الحالة تتغيّر مع حسابات/كوكيز المالك — تُقرأ من `/healthz` و`/v1/models` وقت التشغيل.

## القواعد العابرة لكل المهام (Definition of Done)

1. **Release gate (D8):** `ruff` + `mypy chat_engine/` + `lint-imports` + `pytest` (hermetic، بلا مفاتيح) + `tests/compat` + `scripts/secret_scan.py` — كلها خضراء.
2. **Additive-only:** لا تعديل لأي ملف في المرجع؛ أي حاجة لتعديله ⇒ ADR + PR منفصل مُعلَّم في الـ ledger.
3. **الأدلة:** كل مهمة تضع مخرجاتها الخام في `evidence/ceNN/` (أوامر + مخرجات حرفية، بلا أسرار).
4. **الحالة:** صف في ledger `CHAT_ENGINE_STATE.md` + `NEXT_TASK` واحد فقط + push فوري.
5. **التوسيم الصادق:** أي سلوك لم يُختبر ضد مزوّد حقيقي يُوسم `UNVERIFIED (no live key)` في الوثائق والـ healthz/ledger.
6. **لا تبعية جديدة** بلا سطر قرار في الـ ledger يذكر: الاسم، الرخصة، لماذا، البديل المرفوض. (المرشحون المعروفون: `fakeredis` [test]، `openai` [test]، `opentelemetry-*` [otel extra]، `lit`/`vite` [UI].)
7. **Rollback** لكل مهمة = علم ميزة أو حذف وحدة مستقلة — لا مهمة تترك النظام في حالة وسطى.
