# CHAT_ENGINE_REFERENCE_ASSESSMENT — تقييم المراجع الخارجية لتجميع مزوّدي الذكاء الاصطناعي

```text
TASK:     جزء من CE-00 (مراجعة إلزامية قبل التنفيذ)
DATE:     2026-10-15
RULE:     لا اعتماد برمجي جديد لمجرد وجوده هنا. كل قرار يحتاج إثبات أن الفائدة > التعقيد.
          كتالوجات النماذج/الأسعار = مصادر إثراء بيانات فقط، لا مصدر حقيقة للصحة/التوافر/الاعتماد.
METHOD:   قراءة README/الملفات ذات الصلة عبر GitHub API + raw files (لم تُستنسخ المستودعات كاملة)؛
          السطر "HEAD" هو آخر commit على الفرع الافتراضي وقت المراجعة.
```

## 1. المراجع المُراجَعة (الإصدار/الترخيص)

| # | المستودع | الفرع | HEAD وقت المراجعة | الترخيص (مُتحقق من ملف LICENSE / API) | اللغة | ملاحظة ترخيصية |
|---|---|---|---|---|---|---|
| 1 | BerriAI/litellm | main | `e65e5abc4e79` (2026-10-15) | **MIT** للكود خارج `enterprise/`؛ `enterprise/` بترخيص منفصل | Python | GitHub API يقول `NOASSERTION` بسبب الترخيص المزدوج — تحققت من LICENSE يدويًا |
| 2 | Portkey-AI/gateway | main | `669825cbe89e` (2026-05-25) | **MIT** | TypeScript | — |
| 3 | anomalyco/models.dev | dev | `1e1bcbdb7d2f` (2026-10-15) | **MIT** | TypeScript + TOML data | البيانات نفسها community-contributed |
| 4 | Portkey-AI/models | main | `4f6dcee74792` (2026-10-15) | **MIT** (badge يشير لترخيص gateway) | JS/JSON data | API مجاني بلا auth |
| 5 | Helicone/ai-gateway | main | `9649b27bdc9f` (2025-11-21) | **GPL-3.0** | Rust | **copyleft** — لا يُستورد كود منه إطلاقًا؛ قراءة أنماط فقط |
| 6 | vercel/ai | main | `ffcd52f0ec69` (2026-10-15) | **Apache-2.0** (API: NOASSERTION) | TypeScript | Backend لدينا Python — أنماط فقط |
| 7 | lm-sys/RouteLLM | main | `0b64fdafe049` (2024-08-10) | **Apache-2.0** | Python | آخر commit قبل سنتين — مرجع بحثي فقط |

**ثغرات أمنية:** لم أُجرِ فحص CVE لأي منها لأن **لا واحد منها سيُضاف كـ dependency** في هذه المرحلة (انظر §3). إذا تغيّر القرار لاحقًا، يلزم `pip-audit`/`npm audit` + مراجعة GitHub Security Advisories قبل الاعتماد.

---

## 2. جدول المقارنة: الميزة × المصدر × النمط × الانطباق × ما لدينا

| الميزة | المصدر (ملف/وحدة) | النمط المفيد | الانطباق على محركنا | الموجود في `general_ai_core` | القرار |
|---|---|---|---|---|---|
| توحيد استدعاء المزوّدين خلف واجهة واحدة | litellm `litellm/main.py` + `llms/*`; Portkey `src/providers/*`; vercel `packages/provider/src/language-model/v3/*` | Provider adapter بواجهة ثابتة؛ طبقة تطبيع في/خارج | عالٍ | `ProviderAdapterPort` (30 §8) + 12 فئة خطأ مطبّعة + gateway Facade ثلاثي الطبقات | **REUSE_EXISTING_CORE** |
| تطبيع الأخطاء | litellm `exceptions.py` (~25 فئة مبنية على openai.*; `RateLimitErrorCategory`, `RateLimitType`, `ContextWindowExceededError`, `BudgetExceededError`) | تمييز **نوع** rate limit (RPM/TPM/…) وتمييز `context_window_exceeded` عن `bad_request` العام | متوسط | 12 فئة فقط؛ لا تمييز context_too_large ولا نوع الـ rate limit | **ADAPT_PATTERN** — إضافة `details.rate_limit_type` و`details.reason=context_window` داخل `ProviderError.safe_message/details` **بدون** إضافة فئات (العقد append-only؛ فئة جديدة تحتاج ADR) |
| Cooldown / deployment health | litellm `router_utils/cooldown_handlers.py` (`allowed_fails`, `allowed_fails_policy` لكل نوع خطأ, `DEFAULT_FAILURE_THRESHOLD_PERCENT`, `cooldown_time`), `health_state_cache.py` | عتبة فشل **نسبية** + سياسة لكل نوع استثناء بدل cooldown ثابت | عالٍ | `ResourceSignalBoard`: cooldown 30s ثابت / unavailable 60s ثابت؛ لا عتبة نسبية | **ADAPT_PATTERN** في `AdmissionPort` (D5): `allowed_fails_policy{category: n}` + نافذة؛ يبقى الـ board المرجعي هو المستهلك |
| Routing strategies | litellm `router_strategy/` (`lowest_latency`, `lowest_cost`, `least_busy`, `lowest_tpm_rpm_v2`, `simple_shuffle`, `tag_based_routing`, `budget_limiter`) | فصل الاستراتيجية كـ pluggable scorer؛ `least_busy` يعتمد عدّاد الطلبات الجارية | متوسط | `SimpleScoringRouter` بأوزان 6 أبعاد versioned؛ لا بُعد "in-flight concurrency" حقيقي (latency من `speed_score` الثابت) | **ADAPT_PATTERN** — تغذية `reliability/latency` من قياسات حية عبر `ResourceSignalPort` بدل الثوابت؛ **لا router ثانٍ** |
| Retry/fallback config | Portkey `src/handlers/retryHandler.ts`, config `{"retry":{"attempts":5}, "strategy":{"mode":"fallback"|"loadbalance"|"conditional"}}` | سياسة retry/fallback **تصريحية** قابلة للإصدار؛ conditional routing بشروط على metadata | عالٍ | `FallbackScope` (6) + `PROVIDER_MAX_RETRIES` + Retry-After ≤60s؛ لا conditional routing | **REUSE_EXISTING_CORE** للمحرك؛ **FUTURE_REFERENCE** للـ conditional routing (يحتاج ADR + حالة استخدام) |
| Load balancing بالأوزان | Portkey `loadbalance` weights; Helicone weighted + P2C+PeakEWMA | P2C (power of two choices) + EWMA latency لاختيار المزوّد الأسرع فعليًا | متوسط | لا | **FUTURE_REFERENCE** — يُقاس بعد CE-07؛ Helicone **GPL** فلا كود منه |
| Realtime/WebSocket handlers | Portkey `realtimeHandler.ts`, `websocketUtils.ts`, `streamHandler.ts` | فصل stream handler عن request handler؛ تطبيع SSE لكل مزوّد | عالٍ (CE-08) | لا streaming | **ADAPT_PATTERN** — بنية `StreamingAdapterPort` + normalizer لكل مزوّد (D7) |
| Stream part vocabulary | vercel `language-model-v3-stream-part.ts`: `stream-start, text-start/delta/end, reasoning-start/delta/end, tool-input-start/delta/end, response-metadata, finish, error, raw` | **أحداث start/delta/end مزدوجة** لكل جزء + `reasoning` كنوع منفصل + `raw` للتشخيص | عالٍ | `StreamEvent` 6 أنواع (`delta` بلا تمييز نص/أداة) | **ADAPT_PATTERN** — عقد أحداث chat_engine (D7) يأخذ start/delta/end للنص والأدوات؛ **لا** `reasoning-delta` يُخزَّن (قاعدة no hidden chain-of-thought — يُسمح بعرض لحظي فقط إن أعاده المزوّد، لا حفظ) |
| Prompt/tool contract | vercel `language-model-v3-prompt.ts`, `-tool-call.ts`, `-tool-result.ts`, `-tool-approval-request.ts` | content parts متعددة الوسائط؛ `tool-approval-request` كجزء من البروتوكول | عالٍ (CE-03) | `Message.content: str` + `attachments[]`; `tool_approval_required` كـ ErrorCode | **ADAPT_PATTERN** — `CanonicalMessage.parts[]` (D4) + mapping إلى `payload["messages"]` |
| Canonical model metadata منفصل عن التقديم | models.dev `models/<lab>/<model>.toml` (حقائق) + `providers/<p>/models/<m>.toml` مع `base_model` + حقل `canonical_model_id` في `api.json` | **base-model inheritance**: المزوّد يرث حقائق النموذج ويغلب عليها بـ provider overrides؛ `canonical_model_id` صريح | **عالٍ جدًا** (D1) | لا | **ADAPT_PATTERN** — `CanonicalModel` + `ModelAlias.source=catalog:models.dev`؛ الاستيراد **يُنتج aliases بحالة `proposed`** لا `confirmed` |
| حقول القدرات/الحدود القياسية | models.dev: `attachment, reasoning, tool_call, structured_output, temperature, [limit]{context,input,output}, [modalities]{input,output}, knowledge, release_date, open_weights` | مفردات مستقرة للقدرات؛ **modalities منفصلة input/output** | عالٍ | `Model.modalities` قائمة واحدة (لا تمييز input/output)؛ `capabilities` مفتوحة | **ADAPT_PATTERN** في `CanonicalModel` (input/output modalities)؛ `Model` المرجعي يبقى |
| بيانات الأسعار | Portkey/models `configs.portkey.ai/pricing/{provider}.json` (2,000+ نموذج، units متعددة، hidden dimensions: cache read/write, thinking tokens, per-request fees)؛ litellm `model_prices_and_context_window.json` (4,180 إدخالًا، `source` URL لكل سعر، `mode`, `supports_*`) | **حقل `source` + `last_verified` لكل سعر**؛ تمييز units | متوسط (Observability) | لا بيانات أسعار | **ADAPT_PATTERN** — `PricingRecord{source_url, verified_at, units}`؛ UI يعرض `unknown` بلا سعر؛ **لا استيراد تلقائي** للبيانات كحقيقة (إثراء فقط) |
| كتالوج كـ dependency برمجية | litellm (حزمة كاملة ~ضخمة)؛ models.dev كـ npm | — | منخفض | — | **REJECT** كتبعية؛ البيانات تُستورد كملفات JSON/TOML بـ snapshot مؤرّخ عند الحاجة |
| Observability/usage | Helicone (Rust, GPL); litellm callbacks/spend tracking; Portkey logs | request→provider→cost trace؛ حدود إنفاق لكل مفتاح/فريق | متوسط | `core.usage` reserve/settle + audit + `/v1/admin/usage` | **REUSE_EXISTING_CORE** + إضافة أبعاد (provider_key, canonical_id, attempts) في `UsageLedger.details` (JsonObject — لا contract change) |
| Routing بين نماذج حسب جودة/تكلفة | RouteLLM (`mf`, `bert`, `causal_llm`, `sw_ranking` routers; `calibrate_threshold`) | router مُدرَّب يقرر strong/weak حسب عتبة تكلفة | منخفض الآن | `TierModelPolicy` + أوزان cost/quality ثابتة | **FUTURE_REFERENCE** — يحتاج بيانات تقييم؛ المرجع متوقف منذ 2024 |
| Guardrails hooks | Portkey `before_request_hooks/after_request_hooks` | نقاط التقاط قبل/بعد | متوسط | Capability Firewall + evaluation policy في المرجع | **REUSE_EXISTING_CORE** |
| OpenAI-compatible surface | litellm proxy؛ Portkey `/v1/chat/completions`؛ Helicone | تقديم `POST /v1/chat/completions` متوافق مع OpenAI schema كمدخل | عالٍ (SDK adoption) | `POST /v1/execute` فقط (عقد خاص) | **ADAPT_PATTERN** — chat_engine يقدّم `/v1/chat/completions` (OpenAI-compatible subset موثّق) **كـ mapper** إلى `ExecuteRequest`؛ `/v1/execute` يبقى كما هو (D3) |

---

## 3. القرارات الملخّصة

| القرار | العناصر |
|---|---|
| **REUSE_EXISTING_CORE** | ProviderAdapterPort + 12 فئة خطأ؛ SimpleScoringRouter + FallbackScope؛ ExecutionService retry/failover؛ core.usage/audit؛ Capability Firewall |
| **ADAPT_PATTERN** (أنماط فقط، تُكتب في chat_engen) | base-model inheritance + canonical_model_id (models.dev) · start/delta/end stream parts (vercel) · content parts + tool approval (vercel) · allowed_fails_policy بعتبة نسبية (litellm) · rate_limit_type/context_window تمييز داخل details (litellm) · PricingRecord{source, verified_at} (Portkey/models, litellm) · stream handler منفصل (Portkey) · OpenAI-compatible mapper surface |
| **CANDIDATE_DEPENDENCY** | **لا شيء** في هذه المرحلة. مرشّح وحيد قابل لإعادة النظر بعد CE-07 بقياس: `fakeredis` (dev-only للاختبارات) — ليس من القائمة |
| **REJECT** | litellm/Portkey/Helicone كـ gateway داخل مسار الطلب (gateway ثانٍ بلا سبب معماري — قاعدة المالك)؛ Helicone كود (GPL)؛ استيراد بيانات الأسعار كمصدر حقيقة |
| **FUTURE_REFERENCE** | P2C+PeakEWMA (Helicone)؛ conditional routing (Portkey)؛ RouteLLM quality routers؛ `reasoning` stream parts (عرض لحظي فقط إن سُمح) |

---

## 4. المخاطر المسجّلة
| # | الخطر | التعامل |
|---|---|---|
| A1 | دمج نماذج بتشابه الاسم عند استيراد كتالوج | كل استيراد ⇒ `ModelAlias.decision=proposed`؛ لا يؤثر على الكتالوج حتى `confirmed` يدويًا |
| A2 | بيانات أسعار قديمة تُعرض كحقيقة | `verified_at` إلزامي؛ أقدم من N يومًا ⇒ `stale` في UI |
| A3 | ادعاءات أداء المراجع (Helicone "<5ms p95") | غير مُعاد إنتاجها؛ لا تُنقل كحقائق |
| A4 | ترخيص مزدوج litellm (`enterprise/`) | لا يُستورد أي كود منه؛ الأنماط فقط |
| A5 | RouteLLM متوقف | مرجع بحثي فقط |

## 5. ما يستحق الاختبار لاحقًا (بعد الموافقة وفي مهامه)
- CE-04: استيراد `models.dev/api.json` snapshot → aliases proposed → تأكيد يدوي → العدّادات. 
- CE-07: `allowed_fails_policy` نسبي مقابل cooldown ثابت على fake provider بـ 429 متقطع.
- CE-08: مفردات الأحداث start/delta/end ضد fake streaming adapter + حالات الفشل الخمس.
- CE-10: `/v1/chat/completions` subset ضد عميل OpenAI SDK رسمي (Python) في وضع hermetic.
