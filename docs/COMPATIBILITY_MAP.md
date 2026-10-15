# COMPATIBILITY_MAP — ما يُحفظ من المرجع وما يُضاف في chat_engen (CE-01)

```text
UPSTREAM:  general_ai_core @ 0a13556305bcf40cbf1b750d7ef1d81f7850cabf
BASELINE:  compat/upstream_freeze_baseline_0a13556.json  (15 modules · 117 contracts · 46 routes — نسخة حرفية من المرجع؛ سجل المرجع يقول 116 لأنه قيس على commit أقدم 9831c81b)
GOLDEN:    compat/golden/execute/*.json (14 حالة) · compat/golden/models/ · compat/golden/events/
MANIFESTS: compat/manifests/*.json (14 = 12 template + groq + genspark_llm)  — gateway manifest يُبنى ديناميكيًا (build_gateway_manifest)
GUARD:     tests/compat/  (يُشغَّل في كل release gate)
```

## 1. قاعدة التوافق

| الفئة | القاعدة | الحارس |
|---|---|---|
| عقود المرجع المجمّدة (15 module) | **لا تُلمس.** chat_engine يستوردها فقط. أي حاجة لحقل جديد ⇒ contract جديد في `chat_engine.*` يلفّ/يشير إلى عقد المرجع | `test_upstream_freeze.py`: يشتق الأشكال من الحزمة المثبّتة ويقارنها بالـ baseline؛ أي اختلاف = فشل (ترقية SHA تحتاج تحديث baseline بقرار ledger) |
| `POST /v1/execute` | نفس `ExecuteRequest` (`extra=forbid`) ونفس أشكال الاستجابة والأخطاء | `test_golden_execute.py`: الحالات الـ14 تُعاد ضد chat_engine وتُقارن بعد تطبيع الحقول المتقلبة |
| `GET /v1/models` | نفس `ModelListEntry` (R188 C4 + C-12 runtime) | golden `models/list_in_memory.json` |
| `GET /v1/executions/{id}` و `/events` | نفس `ExecutionStatusResponse` ونفس الأحداث الستة | golden `events/*` |
| Manifests | كل manifest من المرجع يُحمَّل بـ `ProviderManifest` بلا تعديل | `test_manifests_load.py` |
| `ErrorCode` (11) | لا إزالة/تغيير. أكواد chat_engine الإضافية تعيش في `chat_engine.api.errors` وتُسقَط إلى أقرب كود مرجعي في `/v1/execute` | `test_error_codes_superset.py` |

## 2. الحقول المتقلبة (تُطبَّع قبل المقارنة)

`execution_id`, `poll_url` (يحمل الـ id), `id` (UUID لنماذج), `time`, `decided_at`, `created_at`, `trace_id`.
كل ما عداها يُقارن حرفيًا — بما فيه نص الرسائل وأكواد HTTP و`details`.

## 3. خريطة المسارات الـ46 المجمّدة

| المسار | في chat_engine | ملاحظة |
|---|---|---|
| `POST /v1/execute` | **SERVED — متوافق** (CE-02) | نفس العقد؛ `stream=true` يبقى 422 مع `hint` إضافي نحو `/v1/chat/stream` (إضافة داخل `details`) |
| `GET /v1/executions`, `GET /v1/executions/{id}` | **SERVED** (CE-02) | in-memory execution store |
| `GET /v1/models` | **SERVED** (CE-02) + امتداد `GET /v1/catalog/models` (CE-04) | الصف لكل `model_key` كالمرجع؛ الكتالوج الموحّد مسار جديد |
| `GET /v1/skills` | **SERVED** (CE-19) | نفس `SkillsListResponse`؛ الإسناد والإصدارات مسارات جديدة |
| `GET /v1/templates`, `/v1/templates/{ref}` | **SERVED** (CE-19) | نفس `TemplatesListResponse` |
| `GET/POST /v1/projects`, `/v1/projects/{id}` | **SERVED** (CE-09) | نفس الأشكال + حقول chat_engine تحت مسارات `/v1/chat/*` فقط |
| `GET /v1/usage` | **SERVED** (CE-15) | نفس `UsageSummary` |
| `GET/POST /v1/webhooks`, `DELETE /v1/webhooks/{id}` | **NOT SERVED — OUT OF SCOPE** | ليست من نطاق محرك الشات؛ موثّقة كـ `not_served` في `/healthz.compat` |
| `GET/POST /v1/workspaces`, `/v1/workspaces/{id}` | **NOT SERVED — OUT OF SCOPE** | كذلك |
| `/v1/admin/capabilities*` (4) | **NOT SERVED** — بديل chat_engine: `/v1/admin/capabilities/ledger` (CE-06) | مسار المرجع = قدرات المنصة الداخلية |
| `/v1/admin/changes*` (7) | **NOT SERVED** — بديل: `ProviderConfigRevision` (CE-05) | |
| `/v1/admin/providers`, `/v1/admin/models` | **SERVED** (CE-05) بنفس شكل القراءة | onboarding chat_engine تحت `/v1/admin/providers/*` مسارات جديدة |
| `/v1/admin/routing/weights` | **SERVED** (CE-05) | يقرأ `router.default_weights` |
| `/v1/admin/evaluations*`, `/v1/admin/learning*`, `/v1/admin/plans*`, `/v1/admin/scenarios*`, `/v1/admin/self-review`, `/v1/admin/source-changes*` (17) | **NOT SERVED — OUT OF SCOPE** | أنظمة التعلّم/التقييم/تعديل المصدر — ليست محرك شات |

**المبدأ:** chat_engine لا يدّعي تقديم المرجع كاملًا (D3: ليس proxy). ما يقدّمه منه يكون **متطابق العقد**؛ ما لا يقدّمه يُعلن في `GET /healthz` تحت `compat.not_served` حتى لا يُفاجأ عميل.

## 4. خريطة العقود الـ116 حسب المهمة

| Module (مجمّد) | استخدام chat_engine | المهمة |
|---|---|---|
| `core.contracts.provider` | `ProviderAdapterPort` كما هو؛ `OpenAICompatibleAdapter` يطبّقه؛ `StreamingAdapterPort` **port جديد منفصل** | CE-05, CE-08 |
| `core.contracts.domain` | `Provider/Model/ProviderModelBinding/Credential` كما هي؛ `CanonicalModel/ModelAlias` عقود جديدة تشير إلى `model_key` | CE-04 |
| `core.contracts.routing` | `RoutingRequest/Decision` كما هي عبر `SimpleScoringRouter` | CE-02 |
| `core.contracts.model_policy` | الخمسة كما هي؛ `CanonicalResolver` ينتج `ExplicitModelPolicy/ExplicitModelsPolicy` | CE-04 |
| `core.contracts.execute` | `ExecuteRequest/…` كما هي؛ `StreamEvent` الستة تبقى على `/v1/executions/{id}/events`؛ أحداث `/v1/chat/stream` عقد جديد | CE-02, CE-08 |
| `core.contracts.execution` | `Execution/ExecutionNode` عبر `ExecutionService` | CE-02 |
| `core.contracts.execution_strategy` | يُستهلك كما هو عند `execution_strategy` في `/v1/execute` | CE-02 (قراءة) / CE-12 |
| `core.contracts.model_listing` | `ModelListEntry.from_model` لـ `/v1/models` | CE-02 |
| `core.contracts.usage` | `UsageLedger` عبر `ExecutionService.usage` (اختياري) | CE-15 |
| `core.contracts.learning`, `core.contracts.evaluation`, `core.learning.gates` | **لا تُستخدم** (خارج النطاق) — تبقى في الـ baseline للتحقق من الحزمة فقط | — |
| `core.contracts.errors` | `ErrorCode/ErrorDetail/ErrorEnvelope` كما هي؛ أكواد إضافية في chat_engine فقط | CE-02, CE-10 |
| `core.routing.capacity` | `ResourceSignalBoard` + `ResourceSignalPort` كما هي؛ `AdmissionPort` يلفّها | CE-07 |
| `apps.api.provider_onboarding` | **لا يُستورد** (`apps.*` ممنوع) — الشكل محفوظ في الـ baseline فقط؛ onboarding chat_engine عقد خاص | CE-05 |

## 5. أكواد الأخطاء

| كود المرجع (11) | HTTP | في chat_engine |
|---|---|---|
| validation_error 422 · unauthenticated 401 · unauthorized 403 · entitlement_exceeded 403 · capability_denied 403 · provider_unavailable 503 · model_unavailable 503 · tool_approval_required 409 · rate_limited 429 · execution_failed 502 · internal_error 500 | كما هي | تُستخدم حرفيًا في كل المسارات |

| كود chat_engine إضافي (proposed) | HTTP | يُسقَط على `/v1/execute` إلى |
|---|---|---|
| `unsupported_capability` | 422 | `validation_error` |
| `admission_denied` | 429 | `rate_limited` |
| `idempotency_conflict` | 409 | `validation_error` (كما يفعل المرجع اليوم بـ `details.reason=idempotency_conflict`) |
| `stream_interrupted` | 502 | `execution_failed` |
| `alias_unconfirmed` | 422 | `validation_error` |
| `not_found` | 404 | — (المرجع يستخدم أجسام 404 خاصة لكل مورد) |

الأكواد الإضافية تظهر **فقط** على مسارات `/v1/chat/*`, `/v1/catalog/*`, `/v1/admin/providers/*` الجديدة. ADR-CE-0002 (CE-10) يوثّق اقتراح إضافتها append-only للمرجع.

## 6. ما يُثبته CE-01 (أدلة)
- `compat/upstream_freeze_baseline_0a13556.json` مطابق لملف المرجع `engineering/verification/contract_freeze_baseline.json` (sha256 متطابق — انظر `evidence/ce01/`).
- 14 golden لـ `/v1/execute` مأخوذة من `apps.main` المرجعي (in-memory, DEV_DEMO_PRINCIPAL=1, بلا مفاتيح).
- 14 manifest تُحمَّل بـ `ProviderManifest` بلا خطأ.
