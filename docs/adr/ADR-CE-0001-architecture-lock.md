# ADR-CE-0001 — Architecture Lock لمحرك الشات (Chat Engine)

```text
STATUS:      PROPOSED — ينتظر موافقة المالك (يصبح ACCEPTED عند تسجيل الموافقة في CHAT_ENGINE_STATE.md)
DATE:        2026-10-15
CONTEXT REF: CHAT_ENGINE_REALITY_AUDIT.md (CE-00) ; general_ai_core @ 0a13556
SCOPE:       القرارات الثمانية المطلوبة قبل أي كود + قرار التبعية (D0) + UI stack (D9) + التخزين (D10)
```

> لكل قرار: **السياق المُثبت** (من التدقيق) · **القرار** · **المرفوض ولماذا** · **الإثبات**. أي قرار يتطلب تغييرًا كاسرًا في المرجع مُعلَّم ومؤجل.

---

## D0. طريقة الاعتماد على `general_ai_core`

**السياق:** المرجع يعلن حزم top-level عامة (`core`, `apps`, `providers`, `infrastructure`) باسم توزيع `ai-orchestration-platform`؛ ليس namespaced؛ عقوده مجمّدة بحارس آلي؛ بوابته مقفلة لأي تغيير غير معلن.

**القرار:**
- `chat_engen` يستهلك المرجع كـ **git dependency مثبّت بـ SHA** في `pyproject.toml`:
  `ai-orchestration-platform @ git+https://github.com/belalalibb/general_ai_core.git@0a13556305bcf40cbf1b750d7ef1d81f7850cabf`
- **لا نسخ، لا fork داخلي، لا subtree.** ترقية الـ SHA = قرار في الـ ledger + إعادة تشغيل compatibility suite.
- import-linter في `chat_engen`: `chat_engine.*` يستورد فقط `core.*`, `providers.real.*`, `providers.common.*`, `infrastructure.*` (عبر composition). **ممنوع** `apps.*` وأي شيء من `ui/`.
- تعديلات المرجع المطلوبة (مثل فهرس `BindingRegistry`) تُقدَّم لاحقًا كـ PR additive + ADR هناك؛ إلى حينه wrapper في chat_engen.

**مرفوض:** إعادة تسمية المرجع تحت namespace (كاسر لكل مستهلك)؛ vendoring (fork غير قابل للصيانة — قاعدة 22).

**الإثبات:** `lint-imports` يمر؛ `pip install -e .` من clone نظيف يسحب المرجع بالـ SHA؛ اختبار يفشل عند أي استيراد من `apps`.

---

## D1. Canonical Model Identity

**السياق:** الهوية = `Model.model_key` string؛ onboarding يسجّل `{prefix}/{provider_model_name}`؛ R190 سمح بمشاركة نفس الـ key بين مزوّدين ("logical model") لكن **بلا aliases** (سجل التجميد صراحة)؛ `GET /v1/models` صف لكل key.

**القرار — خمس هويات منفصلة لا تُدمج:**

| الكيان | المصدر | الحقول |
|---|---|---|
| `CanonicalModel` | **جديد** `chat_engine.catalog` | `canonical_id` (مثل `openai/gpt-4o`), `display_name`, `family`, حقائق محايدة (context, modalities, capabilities) |
| `Model` | المرجع كما هو | `model_key` — يبقى **مفتاح التوجيه** الذي يفهمه الـ Router |
| `ModelAlias` | **جديد** | `{alias, canonical_id, provider_key?, decision∈{confirmed,proposed,rejected}, confidence, source∈{manual, catalog:models.dev, catalog:litellm, discovery}, decided_by, decided_at}` |
| `ProviderModelBinding` | المرجع | `provider_model_name, availability, limits_metadata` |
| `Credential/Account` | المرجع | `owner_type, credential_ref` |

- **قاعدة الدمج:** نموذجان يظهران كواحد **فقط** بـ `ModelAlias.decision=confirmed` لكليهما نحو نفس `canonical_id`. التشابه الاسمي ⇒ `proposed` بـ `confidence` للمراجعة — **لا دمج تلقائي**.
- الكتالوج الموحّد يعرض `CanonicalModel` مرة واحدة مع `configured_provider_count` و`eligible_provider_count` (يُحسبان منفصلين).
- resolver يحوّل `canonical_id` → مجموعة `model_key`s المؤكدة → يمرّرها للـ Router الحالي (`explicit_models`+`fallback_chain` أو `explicit_model`+`same_model_different_provider`). **الـ Router لا يتغيّر.**

**مرفوض:** إضافة `aliases` إلى `core.contracts.domain.Model` (contract change)؛ الدمج بالاسم.

**الإثبات:** golden: نموذج من 1,000 مزوّد يظهر مرة واحدة بعدّاد 1,000؛ alias `proposed` لا يغيّر العدّاد؛ `confirmed` يغيّره؛ `rejected` يعيد الفصل.

---

## D2. Provider Configuration Lifecycle

**السياق:** المرجع لديه admin `changes` (draft→validate→preview→publish→rollback)، `ProviderRegistry.replace/remove`، onboarding يسجّل disabled ويعيد `enable_draft_payload`. لا سجل إصدارات لإعداد المزوّد.

**القرار:**
- حالة المزوّد: `draft → validating → discovered → verified → enabled | failed | disabled | archived`، تُخزَّن كسجل **append-only** `ProviderConfigRevision{provider_key, revision, config_hash, state, changed_by, changed_at, reason}`.
- كل تعديل = revision جديد؛ `rollback` = نشر revision سابق كـ revision جديد.
- **أثر التعديل على الطلبات النشطة:** `RoutingDecision.policy_snapshot` (11 §16) يحمي التنفيذ الجاري؛ **الطلبات الجديدة فقط** ترى الإعداد الجديد. kill switch يُلغي الأهلية فورًا للجديد ولا يقطع stream جاريًا.
- إبطال cache بـ versioned invalidation (لا إعادة تشغيل). Audit عبر `core.audit` الحالي.

**مرفوض:** الكتابة مباشرة إلى `ProviderRegistry.replace` من الـ API بلا revision.

**الإثبات:** تعديل حدود أثناء تنفيذ جارٍ لا يغيّر `policy_snapshot`؛ `rollback` يعيد الحالة ويُسجَّل revision جديد.

---

## D3. Extraction Boundary

**السياق:** handler `/v1/execute` 2,608 سطر يدمج 10 اهتمامات. Core نظيف (import-linter).

**القرار:**
- Chat Engine = **خدمة FastAPI مستقلة** (`chat_engine.api`) بـ composition root خاص (`chat_engine.composition`) يركّب `core.*` مباشرة — **لا يستورد `apps.*`**.
- `POST /v1/execute` في chat_engine يقبل **نفس `ExecuteRequest`** ويعيد نفس الأشكال؛ contract tests تقارن مع snapshots المرجع.
- الجديد تحت `/v1/chat/*`, `/v1/catalog/*`, `/v1/admin/providers/*`.
- **إثبات الاستقلال:** `examples/consumer_project/` يستخدم SDK فقط ضد `python -m chat_engine serve` بلا أي ملف من `ui/`/`apps/`.

**مرفوض:** proxy أمام `apps.main` (يجعل المحرك تابعًا للمرجع كاملًا — قاعدة 5).

---

## D4. Context Handoff Contract

**السياق:** التاريخ يُحقن كـ `payload["context"]` JSON داخل system message؛ `Message` بلا `tool_call_id`؛ لا تلخيص.

**القرار — `HandoffPacket v1` محايد:**
```text
objective: str · constraints: list[str] · messages: list[CanonicalMessage]{role, parts[], tool_call_id?, name?}
tool_results: list[ToolResult] · decisions: list[Decision] · attachments: list[AttachmentRef]
progress: {done, pending, resume_at} · summary: str|None (منظّم، لا chain-of-thought) · output_spec · budget{max_tokens_hint}
```
- يُحوَّل إلى `payload["messages"]` (OpenAI-compatible) **مع إبقاء** `payload["ask"]`/`payload["context"]` للتوافق — adapter قديم يتجاهل المفتاح الجديد (إضافة لا كاسرة).
- القص حسب `Model.context_window`؛ القيود/القرارات/tool_results **لا تُقص** قبل التاريخ القديم.

**الإثبات:** golden: مهمة 3 مراحل تنتقل A→B بعد 429؛ B يستلم نفس objective/constraints/tool_results ولا يعيد تنفيذ أداة نُفّذت.

---

## D5. Distributed Admission and Usage

**السياق:** `ResourceSignalBoard`/`InMemoryRateLimiter` process-local؛ `RateLimitPort/LeasePort/CachePort` موجودة كـ Protocols؛ `infrastructure/redis` موجود غير مُركَّب؛ ADR-0014 = عملية واحدة.

**القرار:**
- `AdmissionPort`: `reserve(scopes, dims{rpm,tpm,concurrency,budget}) → Reservation|Denied(retry_after)`, `settle`, `release`. Scopes: `provider`, `provider:account`, `provider:model`, `tenant`, `tenant:project`.
- تنفيذان: `InMemoryAdmission` (افتراضي، يعيد استخدام `ResourceSignalBoard`) و`RedisAdmission` (Lua atomic) بـ `ADMISSION_REDIS_URL`.
- **حدود مجهولة ≠ لا نهائية:** وضع محافظ افتراضي (`rpm=60, concurrency=4`، قابل للتعديل) موسوم `limits_source=conservative_default`.
- `Retry-After` ≤60s يُحترم؛ أطول ⇒ failover. Circuit breaker لكل scope (cooldown 30s / unavailable 60s كالمرجع).
- Idempotency: `idempotency_key` + hash الطلب 24h؛ نفس المفتاح وhash مختلف ⇒ 409.
- `/healthz` يُظهر `admission_backend` و`replicas_safe`.

**مرفوض:** Redis إلزامي.

**الإثبات:** اختبار عمليتين + fake clock على `RedisAdmission` (fakeredis) يثبت عدم تجاوز RPM؛ اختبار retry storm يثبت jitter.

---

## D6. Capability Evidence and Authorization

**السياق:** `ProviderCapabilities` 14 bool مغلقة (`extra=forbid`)؛ `/v1/admin/capabilities` = قدرات المنصة لا المزوّدين؛ signals تعطي `available_now`.

**القرار — سجلّ جانبي لا يلمس العقد:**
```text
CapabilityLedgerEntry{provider_key, model_key?, capability:str (الـ14 + streaming_text, tool_calling, structured_output, web_search, realtime_audio, …),
  declared{bool, source}, verified{status∈{unverified,passed,failed,stale}, probe_id, at, evidence_ref},
  enabled{by_admin, policy_ref?, scopes}, available_now (مشتق من signals وقت الطلب)}
```
- **قاعدة التنفيذ:** `declared ∧ verified.passed ∧ enabled ∧ available_now` وإلا `unsupported_capability`.
- **الافتراضي:** تفعيل تلقائي لكل `declared ∧ verified.passed` ضمن صلاحيات المشرف؛ `failed/unverified` تُعزل.
- الـ 14 الحالية تُقرأ من manifest كـ `declared`؛ الإضافية تعيش في الـ ledger فقط. **لا تعديل لـ `ProviderCapabilities`.**

**الإثبات:** manifest قديم يمر بلا تعديل؛ capability بلا probe ناجح تُرفض؛ بعد probe + تفعيل تمر.

---

## D7. Streaming and Realtime Semantics

**السياق:** `stream=true` ⇒ 422؛ `DeltaEvent` عقد فقط؛ SSE lifecycle بـ polling؛ `generate` غير قابل للبث؛ groq `stream:False`.

**القرار:**
- **Port اختياري جديد** `StreamingAdapterPort: stream(request) -> AsyncIterator[StreamChunk]` — **`ProviderAdapterPort` لا يتغيّر**؛ القدرة تُسجَّل `streaming_text` وتحتاج probe.
- Transport: **SSE** على `POST /v1/chat/stream`؛ WebSocket `/v1/chat/sessions/{id}/ws` **extension point** فقط — **لا ادعاء realtime audio**.
- أحداث (بـ `seq`, `event_id`): `started, message_delta, tool_call_start|delta|end, usage, heartbeat, completed, failed, cancelled`. أحداث المرجع الستة تبقى على `/v1/executions/{id}/events`.
- **فشل قبل أول delta:** failover صامت مسموح. **بعد أول delta:** لا failover صامت؛ `failed{reason: stream_interrupted, resumable}`؛ الاستئناف بـ `POST .../resume` صريح يحمل `HandoffPacket` + `delivered_until_seq`؛ لا إعادة إرسال محتوى مُسلَّم.
- الإلغاء ⇒ `cancelled` + إلغاء upstream + تسوية usage الجزئي. Timeouts: first-byte 30s، idle 60s، total حسب `deadline_ms`. Backpressure: buffer محدود يُبطئ المصدر.
- مزوّد لا يدعم stream: 422 `unsupported_capability`، أو بسياسة صريحة `stream_fallback=buffer` ⇒ تنفيذ sync يُصدر دفعة واحدة **موسومة** `synthesized: true`.

**مرفوض:** تغيير توقيع `generate`؛ تركيب delta وهمية بلا وسم (41 §49).

**الإثبات:** contract tests للحالات الخمس (يدعم/لا يدعم/فشل قبل/فشل بعد/إلغاء) على fake streaming adapter.

---

## D8. Compatibility and Release Gates

**السياق:** المرجع يملك `contract_freeze_baseline.json` (15/116/46) + حارس + ratchet.

**القرار:**
- CE-01: `compat/upstream_freeze_baseline.json` + اختبار يعيد اشتقاق الأشكال من المرجع المثبّت ويقارن — ترقية SHA تفشل حتى تُحدَّث النسخة بقرار.
- `compat/golden/` snapshots لـ `/v1/execute` (sync/async/errors) من المرجع تُعاد ضد chat_engine.
- `compat/manifests/`: كل manifests المرجع (2 real + 12 template + gateway) تُحمَّل في كل CI.
- Architecture guard: import-linter + اختبار يفشل عند أي استيراد `apps.` أو ملف من `ui/`.
- Release gate لكل CE: pytest + ruff + mypy على `chat_engine/` + lint-imports + secret scan (أنماط المرجع: `gsk_`, `ghp_`, `sk-`, …) + تحديث ledger.
- **تغيير كاسر** = ADR + `/v2/` + adapter؛ ممنوع بلا ذلك.

---

## D9. UI Stack

**القرار:** `ui_standalone/` بـ **TypeScript + Vite + Lit (Web Components) + CSS custom properties**؛ لا نسخ لأي ملف/لون/تخطيط من `ui/*` المرجعية؛ API chat_engine فقط عبر `fetch`/`EventSource`/`WebSocket`؛ جداول virtualized؛ لا أسرار في المتصفح.
**لماذا Lit:** صغير، بلا runtime ثقيل، design system قابل للتخصيص، قابل للعكس. **مرفوض:** نسخ UI المرجع؛ React/Next (أكبر مما يلزم الآن — قابل لإعادة النظر بـ ADR).

## D10. التخزين الدائم
PostgreSQL نفسه (ADR-0002) بجداول `ce_*` وalembic مستقل في `chat_engine/migrations/`؛ in-memory بلا `DATABASE_URL`. لا DB/queue/vector DB جديدة.

---

## ما لا يتغيّر في المرجع (المرحلة الأولى)
`core.contracts.*`، `ProviderAdapterPort`، `SimpleScoringRouter`، `ExecutionService`، الـ registries الثلاثة، `ErrorCode` (إلا إضافة نهائية بـ ADR لاحق)، كل manifests.

## المخاطر المفتوحة
| # | الخطر | التخفيف |
|---|---|---|
| R1 | أسماء حزم عامة (`core`) تتصادم مع مشروع مستهلك | توثيق + اختبار في consumer example |
| R2 | أداء 10k بلا تعديل المرجع | `CatalogIndex` wrapper أولًا؛ قياس؛ PR للمرجع إن لزم |
| R3 | لا مفتاح حي | كل ادعاء live موسوم UNVERIFIED حتى تشغيل بمفتاح المالك |
| R4 | انقطاع الجلسات | commit+push بعد كل وحدة (مُثبت) |
