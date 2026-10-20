# خطة CE-24 — الاستفادة من LiteLLM والمشاريع الشبيهة **بدون كسر العقود الحالية**

> **الحالة: PROPOSED — بانتظار موافقة المالك بندًا بندًا.** لا تغيير في الكود حتى الموافقة.
> **المبدأ الحاكم:** كل بند هنا *additive* (إضافة حقول اختيارية، وحدات جديدة، بيانات)، لا يغيّر أي عقد Pydantic موجود، لا يُدخل gateway ثانيًا في مسار الطلب (قاعدة المالك D3)، ولا يضيف تبعية تشغيلية دون قرار ledger.
> **التاريخ:** 2026-10-20 · **الأساس:** v0.1.3 · يبني على `CHAT_ENGINE_REFERENCE_ASSESSMENT.md` (مراجعة 2026-10-15) ويحدّثه.

---

## 0. الخلاصة التنفيذية (دقيقة واحدة)

| ما نأخذه من LiteLLM | كيف | الفائدة المباشرة لنا |
|---|---|---|
| **بيانات** `model_prices_and_context_window.json` (4,000+ موديل: أسعار، context، `supports_*`, `mode`) | استيراد كـ snapshot مؤرّخ في `compat/catalogs/` (مثل models.dev الحالي) ⇒ **اقتراحات** تسعير/قدرات بحالة `proposed` | أسعار حقيقية في `/v1/usage` بدل `unknown`؛ قدرات (tools/vision/json) تغذّي الـ router والـ UI |
| **أنماط** الـ Router (cooldown نسبي لكل نوع خطأ، `least_busy`, `lowest_latency` من قياسات حية، budget limiter) | نكتبها نحن داخل `AdmissionPort`/`ResourceSignal` الموجودين | ثبات أعلى مع 13 مزوّد بوابة غير متجانسة (رأينا timeouts 60s وقواطع تفتح) |
| **أنماط** الـ Proxy (virtual keys + spend tracking + guardrails hooks + cache) | وحدات chat_engine جديدة خلف feature flags؛ لا تغيير في العقود | جاهزية للنشر العام (الآن بلا auth)، تحكم في الإنفاق، أمان المحتوى |
| **تطبيع أخطاء** أدق (`context_window_exceeded`, نوع الـ rate limit RPM/TPM) | داخل `error.details` فقط (JsonObject) | failover أذكى (لا نعيد المحاولة على context_window) ورسائل أوضح للمستخدم |
| **لا** نأخذ: الحزمة كتبعية، الـ proxy كخدمة أمام محركنا، كود `enterprise/` | — | نحافظ على `general_ai_core` كمصدر وحيد للتنفيذ وعلى حجم المشروع |

**المشاريع الشبيهة المضافة للمراجعة** (كلها OSS، انظر §3): **Bifrost** (Go, Apache-2.0)، **AISIX** (Rust, Apache-2.0)، **Portkey Gateway** (TS, MIT — مُراجع سابقًا، تحديث)، **Kong AI Gateway**, **Higress**, **GoModel** (MIT). **TensorZero** مؤرشف (يونيو 2026) ⇒ مرجع أنماط فقط. **Helicone** GPL ⇒ لا كود منه.

---

## 1. ما هو LiteLLM اليوم (حقائق مُتحقَّقة 2026-10-20)

- OSS AI Gateway بترخيص **MIT** للكود خارج `enterprise/` (ترخيص منفصل) — نستورد **أنماطًا وبيانات فقط**، ولا نلمس `enterprise/`.
- شكلان: Python SDK (`litellm-core`) + Proxy Server (virtual keys, spend tracking, guardrails, load balancing, admin UI, MCP gateway, A2A).
- سطح متوافق مع OpenAI: `/chat/completions`, `/responses`, `/embeddings`, `/images`, `/audio`, `/batches`, `/rerank`, `/messages` (Anthropic dialect).
- Router: استراتيجيات `lowest_latency`, `lowest_cost`, `least_busy`, `lowest_tpm_rpm`, `simple_shuffle`, `tag_based`; cooldown بعتبة فشل نسبية وسياسة لكل نوع استثناء؛ budget limiter.
- ملف البيانات العام: `model_prices_and_context_window.json` (أسعار per-token، `max_input_tokens/max_output_tokens`, `mode`, `supports_function_calling/vision/response_schema/prompt_caching/reasoning…`, `source` URL).
- **تنبيه أمني مسجّل (Wiz, 2026-09):** ثغرات في LiteLLM Proxy (مفاتيح افتراضية، جلسات MCP غير مصادقة، custom-code guardrails ⇒ RCE). هذا **سبب إضافي** لعدم تشغيله كخدمة أمام محركنا، ودرس لتصميم طبقة المفاتيح عندنا (§2-D).

**ما يملكه محركنا بالفعل ولا نكرره:** `ProviderAdapterPort` + 12 فئة خطأ مطبّعة، `SimpleScoringRouter` + `FallbackScope.max_escalation`، `AdmissionPort` (rpm/tpm/concurrency/budget + breaker)، `HandoffPacket`، SSE resumable، orchestration (3 استراتيجيات)، catalog canonical + aliases، OpenAI-compatible surface، usage ledger + `PricingRecord{source, verified_at}`.

---

## 2. البنود المقترحة من LiteLLM (كل بند: ماذا/كيف/الفائدة/ضمان عدم الكسر/الحجم)

### A. استيراد بيانات الأسعار والقدرات من `model_prices_and_context_window.json` — **أولوية 1**
- **ماذا:** سكربت `scripts/import_litellm_catalog.py` يحمّل الملف (MIT) ويحفظه كـ `compat/catalogs/litellm_prices_snapshot_<date>.json` + يُنتج `PricingRecord(source="litellm", verified_at=<date>)` و**اقتراحات** قدرات (`supports_function_calling` ⇒ `tools`، `supports_vision` ⇒ `vision`، `supports_response_schema` ⇒ `structured_output`، `max_input_tokens` ⇒ `context`).
- **كيف لا نكسر:** نفس آلية models.dev الحالية — كل ما يُستورد يُسجَّل كـ `proposed` (A1 في المراجعة)؛ `PricingRecord` موجود أصلًا بحقل `source` فيتسع لمصدر جديد بلا تعديل. مطابقة الأسماء عبر `normalize_name` الحالية + aliases المؤكدة فقط.
- **الفائدة:** `/v1/usage` و`usage_records` تعرض **تكلفة فعلية** لمعظم موديلات البوابة (deepseek, claude, gpt, gemini, qwen…) بدل `cost: unknown`؛ `cost_tiered` يرتّب بأسعار حقيقية بدل `cost_score` الثابت؛ الـ UI يعرض context window صحيحًا.
- **الحجم:** S (سكربت + 2 اختبار hermetic + صف ledger). **لا تبعية جديدة** (ملف JSON يُحمَّل بـ `httpx` الموجود، وقت التشغيل يقرأ snapshot محلي فقط).
- **مخاطر:** أسعار قديمة تُعرض كحقيقة ⇒ `verified_at` إلزامي + وسم `stale` بعد N يومًا (A2 موجود).

### B. Cooldown نسبي بسياسة لكل نوع خطأ (`allowed_fails_policy`) — **أولوية 1**
- **ماذا:** في `InMemoryAdmission`/`RedisAdmission`: بدل `BREAKER_FAILS=5/10` الموحّد ⇒ سياسة قابلة للضبط لكل `provider_error_category`: مثال `{timeout: 3, provider_unavailable: 3, rate_limited: 10, invalid_credential: 1 (فوري، 10 دقائق), quota_exceeded: 1 (فوري حتى نهاية النافذة), context_window: 0 (لا يُحتسب)}` + مدة فتح لكل فئة.
- **كيف لا نكسر:** `AdmissionPort` لا يتغيّر (نفس `reserve/settle/release`)؛ `settle()` يأخذ `category: str | None = None` **اختياري** جديد (إضافة معامل بقيمة افتراضية = متوافق). القيم من env `CE_BREAKER_POLICY_JSON` مع افتراضي = السلوك الحالي تمامًا.
- **الفائدة:** رأينا حيًّا: `gw_groq` (مفتاح غير مهيأ) و`gw_runpod_console` (`invalid_credential`) يُستدعيان مرارًا ويهدران 2–4 ثوانٍ لكل محاولة؛ `gw_upstage/youmind` timeouts 60s تُحتسب كالأخطاء العابرة. السياسة تُسقطهم فورًا من الترشيح وتُسرّع AUTO.
- **الحجم:** S–M (admission + 4 اختبارات). يستبدل الـ `_recent_failures` المؤقت في orchestration بمصدر واحد.

### C. تغذية الـ router بقياسات حيّة (`lowest_latency` / `least_busy`) — **أولوية 2**
- **ماذا:** `ResourceSignalPort` جديد في chat_engine يجمع EWMA latency + in-flight لكل `provider:model` من `UsageRecorder` + admission، ويُعيد ترتيب `fallback_candidates` **داخل chat_engine قبل التنفيذ**.
- **كيف لا نكسر:** لا router ثانٍ (قاعدة المالك)؛ لا تعديل في `core.routing`؛ إعادة الترتيب تحدث على `RoutingDecision` الراجع (قائمة) قبل `execute_single`. Feature flag `CE_ROUTING_LIVE_SIGNALS=1`.
- **الفائدة:** AUTO يفضّل `gw_deepai/deepseek-v3.2` (800ms) على `gw_overchat/gpt-5-mini` (17s) تلقائيًا؛ يتجنّب مزوّدًا عند امتلاء تزامنه (4) بدل 429.
- **الحجم:** M. يحتاج قياسًا قبل/بعد (p50/p95 لـ 50 طلب AUTO) يُسجَّل في `evidence/`.

### D. Virtual keys + spend tracking لكل مفتاح — **أولوية 1 للنشر العام**
- **ماذا:** وحدة `chat_engine/auth/`: `POST /v1/admin/keys` تُنشئ مفتاحًا (hash فقط يُخزَّن) بخصائص `{tenant_id, project_ids[], models_allowed[], rpm, tpm, budget_usd, expires_at, metadata}`؛ middleware يقرأ `Authorization: Bearer` ويحقن `tenant_id/user_id` في الـ Runtime context؛ الإنفاق يُحتسب من `PricingRecord` (بند A) ويُخصم من ميزانية المفتاح عبر scope جديد `key:<id>` في Admission (الـ scopes نصّية — لا تغيير عقد).
- **كيف لا نكسر:** `CE_AUTH_MODE=off|optional|required` (افتراضي `off` = السلوك الحالي بالكامل؛ كل الاختبارات الحالية تمر). **لا** مفتاح افتراضي ولا `master_key` ثابت (درس Wiz): المفتاح الإداري يُولَّد عند الإقلاع ويُطبع مرة واحدة أو يُقرأ من env.
- **الفائدة:** المحرك قابل للنشر خارج الـ sandbox؛ تطبيقات متعددة بمفاتيح وميزانيات مستقلة؛ `GET /v1/usage?key=` لكل عميل.
- **الحجم:** M–L (store + API + middleware + 10 اختبارات + docs §Auth + UI شاشة Keys). SDKs الحالية تقبل `apiKey` أصلًا.

### E. Guardrails hooks (قبل/بعد) بلا تنفيذ كود مخصّص — **أولوية 2**
- **ماذا:** `GuardrailPort` مع hooks `pre_request(packet) -> allow|block|redact` و`post_response(text) -> allow|block|redact`؛ تنفيذان مدمجان: `keyword_block` (قوائم من env/ملف) و`pii_redact` (regex للبريد/الهاتف/البطاقات). تُربط على مستوى المشروع (`Project.guardrails[]` **حقل اختياري جديد** = default list فارغ) أو عالميًا.
- **كيف لا نكسر:** إضافة حقل اختياري على `Project` (append-only؛ العقد `extra=forbid` لا يتأثر لأن الحقل معرّف). الرفض يُعاد بكود موجود `capability_denied 403` مع `details.guardrail`. **لا تنفيذ لكود مرسَل من المستخدم** (ثغرة LiteLLM RCE).
- **الفائدة:** حماية من تسريب PII إلى مزوّدات طرف ثالث (البوابة تمرّ عبر 13 حسابًا خارجيًا)؛ سياسة محتوى لكل تطبيق.
- **الحجم:** M.

### F. Exact-match response cache (اختياري، off افتراضيًا) — **أولوية 3**
- **ماذا:** cache بمفتاح `sha256(model, messages, generation)` في الذاكرة أو Redis الموجود؛ TTL من env؛ يُطبَّق فقط عند `generation.temperature == 0` أو عند `cache: true` صريح في `generation` (JsonObject — لا تغيير عقد). الرد المخزَّن يُوسَم `usage.cached: true` و`turn.metadata.cache_hit`.
- **كيف لا نكسر:** `CE_CACHE=off` افتراضيًا. **لا semantic cache** (يحتاج embeddings ونموذج — ليس الآن).
- **الفائدة:** توفير تكلفة/زمن على الأسئلة المتكررة في تطبيقات الدعم؛ مفيد للاختبارات.
- **الحجم:** S–M.

### G. تطبيع أدق للأخطاء داخل `details` — **أولوية 2**
- **ماذا:** في `chat_engine/providers/gateway.py` و adapter الـ OpenAI-compatible: استنتاج `details.rate_limit_type ∈ {rpm, tpm, concurrency, budget}` و`details.reason = "context_window_exceeded"` من رسائل المزوّد (أنماط litellm `exceptions.py`)؛ `cost_tiered`/failover **لا يعيد المحاولة** عند `context_window_exceeded` بل يرفع `422 validation_error` مع `details.max_input_tokens`.
- **كيف لا نكسر:** لا فئات أخطاء جديدة (ADR-CE-0002 ثابت)؛ كل الإضافة داخل `details` (JsonObject).
- **الفائدة:** لا نهدر 3 مزوّدين على رسالة أكبر من النافذة؛ الـ UI يعرض "الرسالة أطول من حد الموديل (128k)".
- **الحجم:** S.

### H. `/v1/embeddings` عبر البوابة (`gw_voyage` المتخطّى حاليًا) — **أولوية 3**
- **ماذا:** مسار OpenAI-compatible `POST /v1/embeddings {model, input}` يُربط بعملية `embed` التي تعلنها البوابة لـ voyage؛ `chat_engine/providers/gateway.py` يسجّل مزوّدات `embed` في سجل منفصل بدل تخطّيها.
- **كيف لا نكسر:** مسار جديد؛ `/v1/chat` لا يتأثر؛ docs §15 ("ما ليس موجودًا") تُحدَّث.
- **الفائدة:** يفتح RAG/semantic search للتطبيقات المبنية على المحرك، ويفعّل `gw_voyage` (مورد من حساب المالك غير مستخدم).
- **الحجم:** M. يعتمد على تحقق حي من رد البوابة لعملية embed.

### I. Anthropic Messages dialect `/v1/messages` — **أولوية 3 / FUTURE**
- **ماذا:** mapper ثانٍ (مثل OpenAI-compatible) لعملاء Claude SDK. **الفائدة:** تبنّي أوسع. **الحجم:** M. **التوصية:** تأجيل حتى يطلبه مستهلك فعلي.

### J. ما **نرفضه** صراحة من LiteLLM (ولماذا)
| البند | السبب |
|---|---|
| تثبيت `litellm` كتبعية | حزمة ضخمة (مئات التبعيات)، تكرار لـ `general_ai_core`، وتُدخل سطح هجوم (Wiz 2026-09) |
| تشغيل LiteLLM Proxy أمام/خلف محركنا | gateway ثانٍ بلا سبب معماري (قاعدة المالك)؛ البوابة الحالية ADR-0008 تؤدي الدور |
| custom-code guardrails (تنفيذ كود يُرسل في الطلب) | RCE بالتعريف |
| استيراد الأسعار كمصدر حقيقة | تبقى اقتراحات موسومة بالمصدر والتاريخ (A1/A2) |
| كود `enterprise/` | ترخيص غير MIT |

---

## 3. المشاريع الشبيهة الأخرى (مُحدَّث 2026-10-20) وماذا نأخذ من كل واحد

| المشروع | اللغة/الترخيص | ماذا يفعل جيدًا | ما نأخذه (أنماط فقط) | القرار |
|---|---|---|---|---|
| **Bifrost** (maximhq) | Go / Apache-2.0 | load balancing عبر **عدة مفاتيح لنفس المزوّد** مع failover؛ virtual keys/teams/budgets هرمية؛ plugin middleware | نمط **multi-key per provider** (مفيد جدًا للبوابة: حسابات متعددة لنفس المزوّد بدل حساب واحد) ⇒ بند **K**؛ تسلسل الميزانيات key→project→tenant (يدعم بند D) | **ADAPT_PATTERN** |
| **AISIX** (api7) | Rust / Apache-2.0 | semantic routing؛ **ensemble models** (fan-out + judge) في الـ OSS؛ حدود RPD/TPD؛ governance موحّدة LLM+MCP+A2A | يؤكد تصميم `judge_select` عندنا؛ نأخذ أبعاد **RPD/TPD** (يومي) كإضافة اختيارية لـ `Limits` ⇒ بند **L** | **ADAPT_PATTERN** |
| **Portkey Gateway** (مُراجع سابقًا) | TS / MIT | config تصريحي `{retry, fallback, loadbalance, conditional}`؛ 40+ guardrails | **conditional routing** بشروط على metadata (مثلاً: لغة عربية ⇒ موديل X) — FUTURE بعد D/E | **FUTURE_REFERENCE** |
| **Kong AI Gateway / Higress** | Lua / Go, Apache-2.0 | AI كـ plugin على API gateway قائم | لا شيء مباشر — نموذج نشر: محركنا خلف أي reverse proxy؛ يُذكر في OPERATIONS | **REJECT** (لا نحتاج) |
| **GoModel** (Enterpilot) | Go / MIT | proxy صغير ثنائي اللهجة OpenAI+Anthropic؛ hard spend limits | يؤكد بند I كـ FUTURE؛ **hard stop** عند تجاوز الميزانية (بند D) | **ADAPT_PATTERN** |
| **TensorZero** | Rust / Apache-2.0 — **مؤرشف 06/2026** | حلقة observe→evaluate→optimize؛ تخزين inferences+feedback في DB المستخدم | نمط **feedback على الـ Turn** يغذّي `judge_select`/جودة الموديلات لاحقًا ⇒ بند **M** | **ADAPT_PATTERN** (أنماط فقط، لا كود) |
| **Helicone** | Rust / **GPL-3.0** | P2C+PeakEWMA | لا كود؛ فكرة P2C تُقاس داخل بند C | **FUTURE_REFERENCE** |
| **RouteLLM** | Python / Apache-2.0 (متوقف 2024) | routers مدرَّبة strong/weak | FUTURE؛ يحتاج بيانات تقييم (بند M يبدأ جمعها) | **FUTURE_REFERENCE** |

### بنود إضافية مستوحاة من الشبيهة
- **K. Multi-credential per provider** (Bifrost): `Provider` يحمل `credential_refs[]` (حقل اختياري جديد بجانب `credential_ref` الحالي)؛ التنفيذ يدوّر بينها ويفتح breaker لكل `provider:account` (scope موجود في تصميم D5). **الفائدة:** البوابة تعاني `quota_exceeded`/`auth_expired` لحساب واحد — مفتاح ثانٍ لنفس المزوّد يرفع التوافر فورًا. **الحجم:** M. **لا كسر:** الحقل القديم يبقى ويُعامل كعنصر أول.
- **L. حدود يومية RPD/TPD** (AISIX): `Limits.rpd/tpd: int | None` اختياريان + نافذة 24h في admission. **الحجم:** S.
- **M. Turn feedback** (TensorZero): `POST /v1/conversations/{id}/turns/{tid}:feedback {score, comment}` + `Turn.metadata.feedback` (JsonObject). **الحجم:** S. يبدأ تجميع بيانات لتحسين `cost_tiered`/`judge_select` لاحقًا.

---

## 4. ترتيب التنفيذ المقترح (كل مرحلة تنتهي بـ gate GREEN + فحص حي + ledger + preview)

| المرحلة | البنود | المدة التقديرية | المخرَج القابل للقياس |
|---|---|---|---|
| **P1 — ثبات وتكلفة** | A (أسعار/قدرات litellm) + B (cooldown بسياسة) + G (تطبيع أخطاء) | جلسة واحدة | `usage` يعرض تكلفة لـ ≥70% من موديلات البوابة؛ مصفوفة live_full_check تُظهر سقوط المزوّدات الميتة من AUTO خلال محاولة واحدة؛ p50 لـ AUTO ينخفض (يُقاس) |
| **P2 — جاهزية النشر** | D (virtual keys + spend) + L (RPD/TPD) + E (guardrails مدمجة) | جلسة–جلستان | `CE_AUTH_MODE=required` يعمل مع UI وSDKs؛ ميزانية مفتاح تُستنفد ⇒ 429 `admission_denied scope=key:*`؛ PII redaction مُختبر |
| **P3 — توجيه أذكى** | C (قياسات حية) + K (multi-credential) + M (feedback) | جلسة | p95 AUTO قبل/بعد في `evidence/`؛ breaker لكل حساب |
| **P4 — سطوح جديدة** | H (embeddings/voyage) + F (cache) + I (Anthropic dialect — إن طُلب) | حسب الطلب | `/v1/embeddings` حي عبر gw_voyage |

**قواعد ثابتة لكل مرحلة:** additive-only · لا تبعية جديدة بلا قرار ledger (P1–P3 **صفر تبعيات جديدة**) · أي حقل جديد اختياري بقيمة افتراضية تُبقي السلوك الحالي · golden/compat suite تمر دون تعديل · `INTEGRATION_GUIDE.md` و`reference_client` يُحدَّثان مع كل مسار جديد · أرقام الأداء تُقاس لا تُنقل.

---

## 5. ما أحتاج موافقتك عليه (ضع ✓/✗ أمام كل بند أو رقم المرحلة)

- [ ] **A** استيراد بيانات litellm (أسعار + قدرات) كاقتراحات موسومة
- [ ] **B** cooldown بسياسة لكل نوع خطأ
- [ ] **C** تغذية الـ router بقياسات حية (feature flag)
- [ ] **D** virtual keys + spend tracking (`CE_AUTH_MODE`, افتراضي off)
- [ ] **E** guardrails مدمجة (keyword/PII) بلا كود مخصّص
- [ ] **F** exact-match cache (افتراضي off)
- [ ] **G** تطبيع أخطاء أدق داخل `details` + لا retry على context_window
- [ ] **H** `/v1/embeddings` عبر gw_voyage
- [ ] **I** Anthropic `/v1/messages` dialect (أوصي بالتأجيل)
- [ ] **K** multi-credential لكل مزوّد (Bifrost)
- [ ] **L** حدود RPD/TPD (AISIX)
- [ ] **M** Turn feedback (TensorZero)
- [ ] **الموافقة على الترتيب P1→P4** أو ترتيب بديل
- [ ] **الموافقة على قائمة الرفض §2-J** (لا تبعية litellm، لا proxy ثانٍ، لا custom-code guardrails)

بعد الموافقة: أبدأ بـ **P1** فورًا، وأسلّم رابط المعاينة فقط عند اكتمال كل مرحلة.
