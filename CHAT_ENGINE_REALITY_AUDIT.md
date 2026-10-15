# CHAT_ENGINE_REALITY_AUDIT — تدقيق الواقع قبل أي تنفيذ (CE-00)

```text
TASK:            CE-00 Reality Audit & Gate Check
STATUS:          COMPLETE (audit only — no product code written)
UPSTREAM REPO:   https://github.com/belalalibb/general_ai_core.git
UPSTREAM HEAD:   0a13556305bcf40cbf1b750d7ef1d81f7850cabf  (branch main, 2026-10-15)
TARGET REPO:     https://github.com/belalalibb/chat_engen.git  (كان فارغًا بلا commits عند بداية CE-00)
EVIDENCE DIR:    evidence/ce00/   (كل رقم هنا له ملف دليل هناك)
POSTURE:         لا ادعاء جاهزية بدون تشغيل. ما لم يُشغَّل موسوم UNVERIFIED.
```

> هذا الملف يصف ما **ثبت بالتشغيل** على الـ HEAD أعلاه، لا ما تقوله الوثائق. التعارضات في §4.2.

---

## 0. ملخص تنفيذي

| السؤال | الجواب المُثبت |
|---|---|
| هل يوجد "محرك شات" يعمل؟ | **نعم كمسار تنفيذ واحد** (`POST /v1/execute` → Router → ExecutionService → Adapter)، لكنه **execute-centric لا chat-centric**: لا عقد `messages[]` متعدد الأدوار يصل للمزوّد؛ التاريخ يُحقن كـ JSON داخل system message واحدة. |
| هل Streaming منفّذ؟ | **لا (token streaming)**. `execution_policy.stream=true` ⇒ 422 صريح (مُثبت). يوجد فقط SSE لأحداث دورة حياة التنفيذ على `GET /v1/executions/{id}/events` (مُثبت). |
| كم مزوّد حقيقي؟ | **2 REAL adapters (groq, genspark_llm) + 1 REAL gateway adapter بلا gateway حي + 1 hermetic echo + 12 TEMPLATE**. أي "17+" يعدّ القوالب. |
| هل الـ Router قابل لإعادة الاستخدام؟ | **نعم** — deterministic، explainable، `same_model_different_provider` افتراضيًا، مُجمّد تعاقديًا. لكنه **مسح خطي** بلا فهارس: عند 10,000 مزوّد يتجاوز هدف 150ms (مقاس: p95 ≈ 284ms للنموذج عالي التفرّع؛ AUTO ≈ 664ms). |
| هل الـ rate limiting موزّع؟ | **لا**. `ResourceSignalBoard` و`InMemoryRateLimiter` process-local بالتصميم (ADR-0014: عملية واحدة). |
| canonical model identity / aliases؟ | **لا**. الهوية = `model_key` string. R190 أثبت "one logical Model + many bindings" لكن سجل التجميد يقول حرفيًا *"Model identity = key string (no aliases)"*. |
| BYOK؟ | الاسم الصحيح **BYOK** (لا "Boyk"). العقود موجودة (`OwnerType.USER/TENANT`, `CredentialPolicy`, gateway `CredentialMode.user_key`)؛ **لا مسار API** لربط مفتاح مستخدم؛ `ResourceSelector/AccountPoolManager` غير مركّبين. |
| البوابة؟ | **المرجع:** مقفل لأي تغيير غير معلن. **`chat_engen`:** البوابة = موافقة المالك بعد هذا التدقيق. |

---

## 1. حالة Git والبيئة

```text
upstream HEAD      0a13556305bcf40cbf1b750d7ef1d81f7850cabf
upstream log -5    0a13556 Merge PR #67 ui_recon_shell | df677f5 ledger row 17 | 95ccbec UI-RECON-DEC-02
                   1a4e639 gate d62edb21 PASS 4047/0/0/64 | d62edb2 mypy --strict
upstream status    clean (fresh shallow clone)       diff --stat: empty
target             main: 5c6a2eb bootstrap → c9fe22f evidence → cd3f862 smoke → (this file)
Python             3.13.14 (المشروع يطلب ≥3.12); pip install -e ".[dev]" نجح
Secrets            لا مفاتيح حية؛ كل التشغيل بـ env -u GSK_API_KEY -u GROQ_API_KEY -u GSK_TOKEN
Session resets     3 خلال CE-00 (فُقد .venv و/home/user/ref وملف غير ملتزم) — أُعيد البناء من Git كل مرة
Unreadable files   لا شيء. ADMIN_PLATFORM_CAPABILITY_CATALOG.md المُشار إليه في سجلات قديمة غير موجود (مسجّل في R166)
```

### 1.1 ما شُغِّل فعليًا

| الأمر | النتيجة | الدليل |
|---|---|---|
| `apps.cli describe` | `durable:false, provider_keys:[local_echo], route_count:85, agent_tools_offered:0` | `cli_describe_0a13556.json` |
| `apps.cli routes` | 85 مسارًا | `cli_routes_0a13556.json` |
| `pytest tests/{contract,providers,routing,execution,api,verification}` | **1656 passed / 15 skipped / 0 failed** (~39s). الـ 15 skip كلها live tests تحتاج `GROQ_API_KEY/GSK_API_KEY/GW_GROQ_API_KEY` (41 §49) | `pytest_hermetic_core_slices_0a13556.txt` |
| خادم محلي `DEV_DEMO_PRINCIPAL=1` + 12 طلب curl | §4.3 | `live_local_smoke_0a13556.txt` |
| scale probe 1k/10k | §6 | `scale_probe.py`, `scale_probe_output_0a13556.txt` |
| baseline التجميد | 15 وحدة / 116 عقدًا / 46 مسارًا | `upstream_contract_freeze_baseline_0a13556.json` |

> **لم يُشغَّل:** `check_repo.sh` الكامل (4047 اختبارًا + mypy/ruff/import-linter/secret-scan + Playwright). السجل يقول PASS على d62edb21 — **UNVERIFIED-BY-ME**. الشرائح الست التي تهم الشات خضراء بتشغيلي.

---

## 2. بروتوكول الاستئناف في المرجع — وتطبيقه هنا

المرجع (`final_docs_v3/52_RESUME_AND_PROGRESS_PROTOCOL.md` + README "PERMANENT AGENT OPERATING CONTRACT"):
1. Git المُلتزم هو الدليل الوحيد؛ ملف الحالة يتحكم بالتقدم لكنه ليس إثباتًا.
2. ملف حالة واحد + ledger يُقرأ أولًا عند الاستئناف.
3. مهمة واحدة لكل جلسة؛ لا DONE بلا تحقق + commit.
4. عمل غير ملتزم = Recovery Candidate (يُصنّف، لا يُحذف عشوائيًا).
5. منذ R168: commit + **push** بعد كل عنصر لأن الـ sandbox يُعاد ضبطه.

**مُطبَّق في `chat_engen`:** `CHAT_ENGINE_STATE.md` (ملف الحالة الوحيد، به خطوات "تابع") + `README.md` + `evidence/` + دفع فوري. **مُختبر فعليًا:** انقطعت جلسة CE-00 ثلاث مرات؛ كل ما كان مدفوعًا نجا، وما لم يُدفع (نسخة سابقة من هذا الملف) ضاع وأُعيد.

---

## 3. جرد ما هو موجود فعليًا

### 3.1 العقود (core/contracts) — PROVEN + FROZEN

المرجع جمّد **15 وحدة / 116 عقدًا / 46 مسار `/v1/`** عبر baseline مشتق آليًا (`engineering/verification/contract_freeze_derive.py`) + حارس (`tests/verification/test_contract_freeze_baseline.py`). القاعدة: **additive-only** (حقل اختياري بافتراضي، عضو enum في النهاية، class جديد، route جديد)؛ أي حذف/إعادة تسمية/تغيير نوع = فشل.

| العقد | المحتوى الفعلي | ما يهم محرك الشات |
|---|---|---|
| `provider.py` | `ProviderOperation` (11 مغلقة)، `ProviderManifest`، `ProviderCapabilities` (**14 bool مغلقة، `extra=forbid`**)، `ProviderErrorCategory` (12)، `ProviderGenerateRequest{request_id, tenant_id, operation, provider_model_name, credential_ref, account_id?, payload:JsonObject, timeout_ms?}`، `ProviderGenerateResponse{succeeded, output, usage, error, latency_ms}` | لا `stream`/`messages`؛ `payload` حر — الـ seam الوحيد لبيانات الشات. القدرات مغلقة ⇒ capability جديدة = contract change. |
| `domain.py` | `Model{model_key, tier, modalities, capabilities:list[str] مفتوح, context_window?, *_score?}`، `ProviderModelBinding{provider_model_name, availability, limits_metadata, capabilities}`، `Credential{owner_type∈{platform,tenant,user}}`، `CredentialPolicy{platform_only,user_only,prefer_user,auto}` | BYOK = owner_type + policy. لا aliases/canonical id. |
| `routing.py` | `RoutingRequest{operation, required_capabilities, required_modalities, model_policy, context_length_hint, weights, credential_policy}` → `RoutingDecision{selected, ranked, fallback_candidates, excluded[], policy_snapshot, fallback_policy, weights}` | explainable بالكامل. |
| `model_policy.py` | 5 أنواع؛ `FallbackScope` (6)؛ `SelectionStrategy` (5) | **منفّذ: `fallback_chain`, `parallel_compare`**؛ `best_of_n/debate/specialist_roles` ⇒ 422 (مُثبت). |
| `execute.py` | `ExecuteRequest{ask*, mode, conversation_id, project_id, role, skills, model_policy, agent_policy, requirements, execution_strategy, execution_policy{strategy, async, stream, max_cost_units, max_steps, deadline_ms}, tools, context{attachments, metadata, language}, output{format, language, schema}, webhook_url}`؛ `StreamEvent` (6 منها `delta`) | `delta` عقد فقط. |
| `conversation.py` | `Conversation{tenant_id,user_id,project_id?,title,status}`، `Message{role∈{user,assistant,system,tool}, content:str, attachments[]}` | لا `tool_call_id`، لا content parts. |
| `context.py` | `ComposedContext{context_blocks[type∈{role,preference,history,ask}], excluded[]}`، budget بالأحرف | لا تلخيص/token budget/handoff. |
| `errors.py` | `ErrorCode` (11): validation_error, unauthenticated, unauthorized, entitlement_exceeded, capability_denied, provider_unavailable, model_unavailable, tool_approval_required, rate_limited, execution_failed, internal_error | غائب كأكواد API: `no_eligible_provider` (يُخدم كـ `model_unavailable` + `details.excluded` — مُثبت)، `unsupported_capability`, `credential_invalid`, `request_timeout`, `stream_interrupted`, `context_too_large`, `policy_denied`. append-only. |

### 3.2 جرد المزوّدين الفعلي

| provider_key | نوع التنفيذ | الموديلات | الاعتماد | القدرات | الحدود | اختبار اتصال | آخر دليل حي | التفعيل | التصنيف |
|---|---|---|---|---|---|---|---|---|---|
| `groq` | REAL adapter (`providers/real/groq/adapter.py` 642 سطرًا، httpx pooled) | discovery `GET /models` + قائمة ثابتة `GROQ_MODELS` تُسجَّل عند الإقلاع | `api_key` → `SecretManagerPort` → `credential_ref` | chat/generate_text; constrained decoding; **`"stream": False` مثبّت** | TPM/RPM من 429+Retry-After فقط | hermetic ✅؛ live skipped | R165 live agent run 32 مرحلة؛ R167: حساب المالك `organization_restricted` | ACTIVE عند المفتاح | **REAL+WORKING (hermetic) / REAL BUT UNVERIFIED (live الآن)** |
| `genspark_llm` | REAL adapter (665) OpenAI-compatible proxy | `GET /models` (52) + embeddings allowlist | `api_key` → ref | generate_text, create_embeddings | — | hermetic ✅؛ live skipped | R163 PONG؛ R164/R168 الخطة المجانية ترفض (HTTP 200 in-band ⇒ quota_exceeded) | ACTIVE عند `GSK_API_KEY` | **REAL+WORKING (hermetic) / UNVERIFIED (live)** |
| `local_echo` | hermetic | `local-echo-1` | — | chat | — | ✅ الآن | الآن | افتراضي | **PROVEN (hermetic)** |
| `gateway` (RemoteGatewayAdapter) | REAL adapter (826) نحو `gateway-service` | `/v1/describe`, `/v1/models` | `credential_ref`+`route_token_ref`; `credential_mode∈{user_key(BYOK), platform}` | 8 عمليات | — | hermetic ✅ | لا gateway حي | مع `GATEWAY_BASE_URL` فقط | **REAL BUT UNVERIFIED e2e** |
| 12 templates | يرفع `TemplateProviderInvoked` | — | `[]` | توثيقية | — | — | — | `template_disabled` | **TEMPLATE** |
| gateway-service/providers `{groq, assemblyai, fixture_echo, _example, _template}` | مشروع فرعي (194 اختبارًا هرميًا) | — | — | — | — | hermetic ✅ | — | — | **REAL BUT UNVERIFIED (gateway side)** |

**الخلاصة:** REAL+WORKING(hermetic)=2 · REAL BUT UNVERIFIED=2 · TEMPLATE=12 · BROKEN/DEFERRED=0 معروف.

### 3.3 الجدول الرئيسي: الميزة × الحالة

| الميزة | الحالة | المسارات الدالة | الاختبارات | النواقص | إعادة استخدام؟ |
|---|---|---|---|---|---|
| Provider manifest + registry + eligibility | **PROVEN** | `core/contracts/provider.py`, `core/providers/registry.py` | tests/providers, tests/contract | مسح خطي؛ in-memory | ✅ كما هي |
| Router | **PROVEN** | `core/routing/router.py` | tests/routing | لا tenant/plan filter؛ مسح خطي؛ `admin_defined_chain` بلا producer | ✅ **لا يُستبدل** |
| Resource signals | **PROVEN (process-local)** | `core/routing/capacity.py` | R188 | غير موزّع؛ RPM فقط؛ ليس حجزًا ذريًا | ✅ Protocol |
| Execution service | **PROVEN** | `core/execution/service.py` | tests/execution | sync فقط؛ لا streaming | ✅ |
| Multi-model | **PROVEN** | `core/execution/multi_model.py` | ✅ | 3 استراتيجيات ترفض | ✅ |
| Templates / General Agent / App Factory | **PARTIAL** | `core/execution/strategy.py`, `core/agent/*` | R191/R196/R202 | App Factory = خطة بيانات (`generation_deferred=True`)؛ sync فقط (R202-F1)؛ `app_factory.plan@1` مخدوم (مُثبت) | ✅ كأساس |
| Agent runtime | **PROVEN hermetic / live R165** | `core/agent/runtime.py` | tests/agent | deny-by-default | ✅ |
| `POST /v1/execute` sync | **PROVEN** | `apps/api/app.py:896` | tests/api | handler 2,608 سطر متشابك | ✅ كعقد، لا كاستيراد |
| async + worker + outbox | **PROVEN** | `core/runtime/*` | ✅ | in-process | ✅ |
| SSE lifecycle | **PROVEN** | `/v1/executions/{id}/events` | ✅ | ليس token streaming؛ polling 0.5s؛ timeout 60s | ✅ كنمط |
| Token streaming | **CONTRACT_ONLY** | `DeltaEvent` | — | `stream=true` ⇒ 422 | ❌ CE-08 |
| Realtime audio/WS | **MISSING** | — | — | — | — |
| Conversation store | **PROVEN (mem+durable)** | `core/memory/ports.py` | ✅ | لا endpoint CRUD؛ history كـ JSON في system | ✅ port |
| Context composer | **PROVEN (MVP)** | `core/context/composer.py` | ✅ | لا تلخيص/handoff | جزئيًا |
| Provider onboarding | **PROVEN (gateway only)** | `core/providers/onboarding.py`, `apps/api/provider_onboarding.py` | ✅ | `GATEWAY_BASE_URL` فقط؛ gateway providers فقط؛ لا wizard/bulk/dry-run/SSRF | ✅ walker |
| Model catalog | **PROVEN** | `core/contracts/model_listing.py` | ✅ | صف لكل key؛ لا عدّادان؛ لا بحث/ترقيم | جزئيًا |
| Canonical identity + aliases | **MISSING** | — | — | "no aliases" | ❌ CE-04 |
| Capability registry (4 حالات) | **PARTIAL** | manifest + signals + `/v1/admin/capabilities` (قدرات منصة) | ✅ | لا verified/enabled لكل provider×cap | ❌ CE-06 |
| Secrets by reference | **PROVEN** | `core/secrets`, Vault | `test_log_secret_leakage.py` | — | ✅ |
| BYOK vs platform | **PARTIAL** | عقود + gateway `user_key` | hermetic | غير مركّب؛ لا API | ✅ العقود |
| Rate limit API | **PROVEN** | `EXECUTE_RATE_LIMIT` | ✅ | process-local | ✅ |
| Audit / usage | **PROVEN** | `core/audit`, `core/usage` | ✅ | — | ✅ |
| Identity | **PROVEN** | `/v1/auth/*`, `ADMIN_EMAILS` | ✅ | admin/non-admin فقط؛ لا API keys | ✅ |
| UI مرجعية | **PROVEN (vanilla JS)** | `ui/*` | Playwright | **لن تُنسخ** | قراءة فقط |
| Gateway-service | **PROVEN hermetic (194)** | `gateway-service/*` | ✅ | لا نشر حي | مرجع Facade |

---

## 4. العقود والـ Endpoints مقابل الوثائق

### 4.1 المسارات المخدومة (85) — المهم للشات
```text
POST /v1/execute                                  المدخل الوحيد (sync | async 202)
GET  /v1/executions, /{id}, /{id}/events (SSE lifecycle)
GET  /v1/models                                   صف لكل model_key
GET  /v1/templates, /v1/templates/{ref}           app_factory.plan@1
POST /v1/agent/converse                           Bearer مطلوب
GET/POST /v1/projects, /v1/workspaces, /v1/webhooks
GET  /v1/admin/{providers,models,routing/weights,system,usage}
POST /v1/admin/providers/onboard                  غير مركّب بدون GATEWAY_BASE_URL
/v1/admin/changes/*                                draft→validate→preview→publish→rollback
```
**غير موجود:** `conversations` CRUD، `providers` لغير الأدمن، `stream` endpoint، `aliases`.

### 4.2 تعارضات الوثائق مع الكود
| الوثيقة | الكود | مصدر الحقيقة |
|---|---|---|
| 10 §11: `delta` ضمن 6 أحداث | لا يُصدر؛ `stream=true` ⇒ 422 | الكود |
| 10 §13.4: 5 استراتيجيات | 2 | الكود |
| 30 §10 account pools + leases | عقود+اختبارات بلا تركيب | freeze record Disposition C |
| `_pending_real_providers.md`: Groq live 2026-08-28 | R167: حساب مقيّد | ledger R167 |
| OPERATIONS §7: discovery عبر `GET /models` | قائمة ثابتة عند الإقلاع | `_bind_real_providers` |
| طلب المهمة: كود `no_eligible_provider` | يُخدم كـ `model_unavailable` + `details.excluded[]` | الكود §4.3 |

### 4.3 التشغيل الحي المحلي (الكامل في `live_local_smoke_0a13556.txt`)
```text
POST /v1/execute {ask}                              → 200 succeeded (local_echo, usage 1/1)
POST /v1/execute stream=true                        → 422 "Streaming is not available on this deployment slice."
POST /v1/execute selection_strategy=debate          → 422 "(supported: fallback_chain, parallel_compare)"
POST /v1/execute selection_strategy=parallel_compare→ 200 succeeded
POST /v1/execute explicit_model=gpt-nonexistent     → {"code":"model_unavailable","details":{"excluded":[{"reason":"explicit model not registered or not active"}]}}
POST /v1/execute async=true                         → 202 queued + poll_url
GET  /v1/executions/{id}/events                     → execution_started → node_started → node_completed → final
GET  /v1/models                                     → [{name:"local-echo-1", providers:["local_echo"], runtime:[{eligible:true}]}]
GET  /v1/templates                                  → [{ref:"app_factory.plan@1", origin:"system", status:"active"}]
POST /v1/admin/providers/onboard (no bearer)        → 401 ; GET /v1/admin/providers (demo) → 401
```

---

## 5. خريطة الحدود

```text
┌─ CORE (مكتبة — لا نسخ ولا استبدال) ────────────────────────────────────────┐
│ core.contracts.{provider,domain,routing,model_policy,execute,conversation,   │
│                 context,errors}                                  ← FROZEN    │
│ core.providers.{registry,ports,accounts,onboarding}                          │
│ core.routing.{router,capacity,resources}  core.execution.{service,           │
│ multi_model,strategy,templates}  core.secrets  core.audit  core.usage        │
│ core.memory.ports                                                            │
└─────────────┬───────────────────────────────────────────────────────────────┘
              │ chat_engine يستورد core؛ core لا يعرف chat_engine
┌─────────────▼─ CHAT ENGINE (جديد في chat_engen) ───────────────────────────┐
│ contracts     CanonicalChatRequest/Message/ContentPart/ToolCall + Mapper ⇄  │
│               ExecuteRequest/payload (payload["messages"] إضافي لا كاسر)    │
│ catalog       CanonicalModel + Alias(decision,confidence,source) + فهارس    │
│               O(1) + configured_provider_count / eligible_provider_count     │
│ capabilities  declared/verified/enabled/available لكل provider×capability   │
│ onboarding    wizard/bulk/dry-run + OpenAI-compatible adapter + SSRF policy │
│ admission     RPM/TPM/concurrency/budget port (in-mem + redis binding لاحقًا)│
│ streaming     StreamingAdapterPort (اختياري) + SSE/WS session + أحداث       │
│ context       ConversationService + HandoffSummary (provider-neutral)       │
│ api           FastAPI مستقل: /v1/chat/*, /v1/catalog/*, /v1/admin/providers/*│
│               + /v1/execute متوافق (نفس العقد والسلوك، contract tests)       │
│ sdk           ChatClient (Python)                                           │
└─────────────┬───────────────────────────────────────────────────────────────┘
              │ HTTP / SDK / Events فقط
┌─────────────▼───────────────┐   ┌────────────────────────────────────────┐
│ STANDALONE UI (جديدة كليًا)  │   │ APP FACTORY (core.agent.app_factory +   │
│ Overview·Providers·Catalog· │   │ GitHub read-only inspector + write      │
│ Workbench·Agent Studio·Audit│   │ grants محكومة)                          │
└─────────────────────────────┘   └────────────────────────────────────────┘
```

**فجوة تُحل في CE-02:** المرجع **ليس حزمة namespaced** — `pyproject` يسميها `ai-orchestration-platform` ويعلن `apps/core/providers/infrastructure` top-level. الخيارات: (أ) `pip install git+...@0a13556` كما هي + import-linter يمنع الاستيراد خارج `core`/`providers.real`/`infrastructure`؛ (ب) إعادة تسمية في المرجع — كاسر، مرفوض؛ (ج) subtree مقيّد. **التوصية: (أ)**.

---

## 6. الأداء (مقاس)

Scale probe هرمي (`evidence/ce00/scale_probe.py`)، Python 3.13، بلا شبكة:

| السيناريو | explicit (نموذج يخدمه **كل** المزوّدين) | explicit (3 مزوّدين) | AUTO | catalog projection |
|---|---|---|---|---|
| 1,000 مزوّد / 50 نموذج | p50 9.8ms, p95 **13.8ms** | p50 1.1ms | p50 21ms | p50 9.5ms |
| 10,000 مزوّد / 200 نموذج | p50 138ms, p95 **284ms** | p50 17.6ms, p95 25ms | p50 **664ms**, max 745ms | p50 **404ms** |

1. هدف §9 (routing p95 ≤ 150ms @10k) **لا يتحقق** للنموذج عالي التفرّع ولا لـ AUTO. السبب: `BindingRegistry.bindings_for_model` مسح خطي؛ `routing_candidates()` يفرز كل المزوّدين لكل طلب؛ `_rank` يحسب كل مرشح.
2. الكتالوج (≤500ms) يتحقق بالكاد **بلا ترقيم**.
3. **العلاج ليس Router جديدًا**: فهرس `model_id→bindings` (تغيير داخلي غير كاسر في المرجع، أو `CatalogIndex` في chat_engen يلفّ الـ registries) + top-K قبل الـ scoring. يُقاس في CE-04/CE-07.
4. **عملية واحدة:** registries، signal board، rate limiter، onboarding store تموت مع العملية (ADR-0014). multi-replica يحتاج ports مشتركة (CE-07).
5. handler `/v1/execute` 2,608 سطر غير قابل للاستيراد كوحدة؛ محرك الشات يعيد تركيب طبقة رقيقة فوق `core.execution` مع contract tests تضمن نفس السلوك.

---

## 7. التسمية والمفاهيم المؤكّدة
- **Boyk ⇒ BYOK** (ADR-0008 "BYOK custody"؛ `CredentialMode.USER_KEY`؛ `OwnerType.USER/TENANT`). المفتاح يُحل platform-side ويُرسل للـ gateway داخل envelope memory-only. **لا مسار API لتسجيل مفتاح مستخدم** (CE-05).
- "Provider health ≠ account health" مُطبّق (`aggregate_provider_health`).
- "Model identity ≠ provider capacity" مُطبّق في الـ router بلا aliases.

---

## 8. الفجوات بالأولوية

### P0
| # | الفجوة | الدليل | المهمة |
|---|---|---|---|
| P0-1 | لا token streaming ولا streaming port | `stream=true`⇒422؛ groq `"stream": False` | CE-08 |
| P0-2 | لا canonical identity/aliases؛ تكرار النموذج عند اختلاف prefix | freeze record؛ `ModelListEntry` | CE-04 |
| P0-3 | لا مسار لمزوّد OpenAI-compatible مباشر (onboarding = gateway فقط) | Decision 2 | CE-05 |
| P0-4 | التاريخ لا يصل كـ `messages[]` | `groq/adapter.py:505-527` | CE-03 |
| P0-5 | لا API محادثات/مشاريع كواجهة شات | قائمة المسارات | CE-09 |
| P0-6 | routing p95 @10k > الهدف | scale probe | CE-04/CE-07 |

### P1
| # | الفجوة | المهمة |
|---|---|---|
| P1-1 | admission process-local؛ لا TPM/concurrency/budget؛ لا حجز ذري | CE-07 |
| P1-2 | لا verified/enabled لكل provider×capability؛ `ProviderCapabilities` مغلقة | CE-06 |
| P1-3 | BYOK بلا API؛ `ResourceSelector` غير مركّب | CE-05 |
| P1-4 | أكواد API ناقصة — append-only + ADR | CE-01/CE-10 |
| P1-5 | لا تلخيص/token budget/handoff | CE-09 |
| P1-6 | المرجع ليس حزمة namespaced | CE-02 |

### P2
| # | الفجوة | المهمة |
|---|---|---|
| P2-1 | `admin_defined_chain` بلا producer | CE-07 (اختياري) |
| P2-2 | Account pools/leases غير مركّبة | مؤجل |
| P2-3 | App Factory خطة بيانات فقط | CE-14 |
| P2-4 | لا Dockerfile/compose/CI في المرجع | CE-16 |
| P2-5 | OpenAPI admin-gated على durable | CE-10 |

---

## 9. إعادة الإنتاج

**Linux/macOS:**
```bash
git clone https://github.com/belalalibb/general_ai_core.git && cd general_ai_core && git checkout 0a13556
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
env -u GSK_API_KEY -u GROQ_API_KEY -u GSK_TOKEN .venv/bin/python -m apps.cli describe
env -u GSK_API_KEY -u GROQ_API_KEY -u GSK_TOKEN .venv/bin/python -m pytest tests/contract tests/providers tests/routing tests/execution tests/api tests/verification -o addopts="" -q
DEV_DEMO_PRINCIPAL=1 PORT=8766 .venv/bin/python -m apps.main &   # ثم curl كما في evidence/ce00/live_local_smoke_0a13556.txt
.venv/bin/python /path/to/chat_engen/evidence/ce00/scale_probe.py
```
**Windows PowerShell** (**UNVERIFIED** — يُثبت في CE-16):
```powershell
git clone https://github.com/belalalibb/general_ai_core.git; cd general_ai_core; git checkout 0a13556
py -3.12 -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"
Remove-Item Env:GSK_API_KEY,Env:GROQ_API_KEY,Env:GSK_TOKEN -ErrorAction SilentlyContinue
.\.venv\Scripts\python -m apps.cli describe
.\.venv\Scripts\python -m pytest tests/contract tests/providers tests/routing tests/execution tests/api tests/verification -o addopts="" -q
$env:DEV_DEMO_PRINCIPAL="1"; $env:PORT="8766"; Start-Process .\.venv\Scripts\python -ArgumentList "-m apps.main"
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8766/v1/execute -ContentType application/json -Body '{"ask":"hello"}'
```

---

## 10. البوابة والخطوة التالية
- **المرجع:** مقفل لأي تغيير غير معلن. محرك الشات **لن يعدّل المرجع** في المرحلة الأولى؛ أي تعديل (مثل فهرس `BindingRegistry`) يُقدَّم لاحقًا كـ PR additive مع ADR.
- **`chat_engen`:** موافقة المالك على (1) هذا التدقيق، (2) `docs/adr/ADR-CE-0001-architecture-lock.md`، (3) `CHAT_ENGINE_TASKS.md`، (4) `CHAT_ENGINE_REFERENCE_ASSESSMENT.md`. **لا كود منتج قبل الموافقة.**
- **التالي بعد الموافقة:** `CE-01 Contract Freeze & Compatibility Map`.

## 11. الافتراضات الصريحة (قابلة للتعديل)
1. المرجع dependency مثبّت بـ SHA؛ لا fork.
2. أول تحقق حي عبر مزوّد OpenAI-compatible يملكه المالك، بمفتاح env وقت التنفيذ فقط.
3. UI الجديدة: vanilla TS/ES modules بلا framework ثقيل (ما لم يُقرَّر غير ذلك في ADR-CE-0001).
4. التخزين الدائم: PostgreSQL نفسه (ADR-0002) بـ migrations بادئتها `ce_`.
5. Redis اختياري للـ admission الموزّع؛ الافتراضي in-memory مع تحذير في `/healthz` عند multi-replica.
