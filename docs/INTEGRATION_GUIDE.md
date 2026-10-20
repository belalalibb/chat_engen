# chat_engine — دليل التكامل المرجعي الكامل (Integration Guide)

> **لمن هذا الملف؟** لأي وكيل برمجي (AI coding agent) أو مهندس يريد استدعاء موارد الذكاء الاصطناعي المتاحة في المحرك وبناء تطبيق مدمج به — بدون قراءة الكود المصدري.
> **المصدر الحاكم عند أي تعارض:** `GET /openapi.json` على الخادم الحي، ثم `docs/API.md`. هذا الدليل يشرح *كيف تستخدم* ذلك العقد عمليًا.
> **الإصدار:** v0.1.3 — كل الأمثلة أدناه نُفِّذت فعلًا ضد خادم حي مربوط بـ Remote Provider Gateway (13 مزوّدًا) — انظر `evidence/ce20/live_full_check.txt`.

---

## 0. الخلاصة في 60 ثانية (Quickstart)

```bash
BASE=http://localhost:8080          # أو رابط المعاينة

# 1) هل المحرك حي؟ ما المزوّدون المربوطون؟
curl -s $BASE/healthz

# 2) ما الموديلات المتاحة الآن؟ (الأبسط: قائمة بأسلوب OpenAI)
curl -s $BASE/v1/openai/models

# 3) أرسل رسالة — المحرك يختار الموديل تلقائيًا (AUTO + failover)
curl -s -X POST $BASE/v1/chat -H 'content-type: application/json' \
  -d '{"message":"اشرح لي ما هو REST في سطرين"}'

# 4) تابع نفس المحادثة (الذاكرة تُدار على الخادم)
curl -s -X POST $BASE/v1/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"<من الرد السابق>","message":"لخّص ما قلته في جملة"}'

# 5) Streaming (SSE)
curl -N -X POST $BASE/v1/chat/stream -H 'content-type: application/json' -H 'accept: text/event-stream' \
  -d '{"message":"اكتب قصيدة قصيرة","stream_fallback":"buffer"}'
```

**القاعدة الذهبية:** إذا لم تكن متأكدًا أي موديل تستخدم — **لا تُرسل `model` إطلاقًا**. المحرك يُوجّه تلقائيًا ويتجاوز المزوّد الذي يرفض (حصة/اعتماد/انقطاع) إلى التالي.

---

## 1. النموذج الذهني (Mental Model)

```
تطبيقك ──HTTP/JSON──▶ chat_engine ──▶ Router (AUTO/explicit + failover)
                                   ├─▶ Admission (rpm / concurrency / breaker لكل مزوّد)
                                   ├─▶ HandoffPacket (ضغط السياق + تعليمات المشروع + Skills)
                                   └─▶ Provider adapters
                                         ├─ Remote Provider Gateway (gw_* : 13 مزوّدًا خلف عقد ADR-0008)
                                         ├─ OpenAI-compatible (BYOK)
                                         └─ local_echo (اختبار)
```

- **ثلاث طبقات API** (اختر واحدة حسب احتياجك):

| الطبقة | متى تستخدمها | المسارات الأساسية |
|---|---|---|
| **chat_engine-native** (موصى بها للتطبيقات الجديدة) | تريد محادثات بذاكرة، مشاريع، Skills، استراتيجيات متعددة الموديلات، SSE قابل للاستئناف | `/v1/chat`, `/v1/chat/stream`, `/v1/projects`, `/v1/conversations` |
| **OpenAI-compatible** | لديك كود/SDK يتكلم OpenAI وتريد توجيهه للمحرك بلا تعديل | `/v1/chat/completions`, `/v1/openai/models` |
| **Upstream-compatible** | تتكامل مع أنظمة تتوقع عقد `general_ai_core` الأصلي | `/v1/execute`, `/v1/executions/*`, `/v1/models` |

- **الهوية:** Phase 1 بلا مصادقة (tenant/user ثابتان). لا تُعرّض الخادم للعامة قبل وضع طبقة auth أمامه (reverse proxy / gateway).
- **كل الأجسام `extra=forbid`:** أي حقل غير معروف ⇒ `422 validation_error`. لا تخترع حقولًا.
- **كل الأخطاء بغلاف واحد:** `{"error":{"code","message","retryable","details","trace_id"}}`.

---

## 2. اكتشاف الموارد (Discovery)

### 2.1 `GET /healthz`
```json
{
  "status": "alive", "service": "chat_engine", "version": "0.1.3",
  "upstream_sha": "0a13556…", "profile": "…",
  "providers": ["local_echo","genspark_llm","gw_groq","gw_syntx","gw_deepai", "…"],
  "gateway": {
    "bound": ["gw_groq","gw_syntx","gw_deepai","gw_freebuff","gw_youmind","gw_ivycraft","gw_llamacoder","gw_upstage","gw_runpod_console","gw_parallel","gw_notegpt","gw_overchat","gw_tinyfish"],
    "skipped": {"gw_voyage": "gateway route does not declare generate_text"}
  },
  "admission_backend": "in_memory", "replicas_safe": false
}
```
- `gateway.bound` = مزوّدات البوابة التي نجح `describe` لها وقت الإقلاع. **"مربوط" لا يعني "يجيب الآن"** — الحالة الفعلية تظهر عند الطلب (انظر §7).

### 2.2 `GET /v1/openai/models` — الأبسط لقائمة اختيار
```json
{"object":"list","data":[{"id":"deepseek-v3.2","object":"model","created":0,"owned_by":"gw_deepai"}, …]}
```
`id` يُستخدم مباشرة في حقل `model`. يوجد صف لكل `canonical_id` وصف لكل `model_key` ملموس.

### 2.3 `GET /v1/models` — تفاصيل أكثر (أي مزوّد يخدم أي موديل)
```json
{"models":[{"name":"deepseek-v3.2","providers":["gw_deepai","gw_overchat"],"capabilities":["chat"], "…":"…"}]}
```
استخدمه عندما تريد تثبيت الزوج `(model, provider)` صراحة.

### 2.4 `GET /v1/catalog/models?offset&limit&q=` — كتالوج canonical (10k+ صف، مُرقّم)
`GET /v1/catalog/models/{canonical_id}` لصف واحد، `GET /v1/catalog/aliases` للأسماء البديلة.

### 2.5 `GET /v1/admin/admission` — لماذا رُفض طلبي؟
لقطة حيّة لكل scope: `rpm_used`, `in_flight`, `breaker_open`. مفيدة جدًا أثناء التطوير.

---

## 3. المحادثة الأساسية: `POST /v1/chat` (sync)

### 3.1 الطلب — كل الحقول
```json
{
  "message": "نص رسالة المستخدم (إلزامي، ≤ 400k حرف)",
  "conversation_id": "uuid | null  — null ⇒ تُنشأ محادثة جديدة",
  "project_id":      "uuid | null  — يرث تعليمات/موديل/Skills المشروع عند إنشاء محادثة جديدة",
  "model":    "canonical_id أو model_key | null ⇒ AUTO",
  "provider": "provider_key | null — يثبّت المزوّد (مثل gw_deepai)",
  "generation": {"temperature": 0.2, "top_p": 1, "max_tokens": 512, "stop": ["###"], "response_format": {"type":"json_object"}},
  "tools": [],
  "required_capabilities": [],
  "skills": ["skill_id"],
  "attachments": [],
  "strategy": "single | sequential_handoff | judge_select | cost_tiered",
  "strategy_options": {},
  "stream_fallback": "buffer  — مقبول ومُتجاهَل هنا (للتوافق مع جسم /v1/chat/stream)"
}
```
> `generation` تُمرَّر كما هي للمزوّد؛ المزوّدات التي لا تدعم مفتاحًا تتجاهله. لا توجد حقول إضافية.

### 3.2 الرد — `ChatResponse`
```json
{
  "conversation_id": "0d8e5924-…",
  "user_turn_id": "…",
  "turn": {
    "id": "df0b126e-…", "seq": 2, "kind": "message", "role": "assistant",
    "parts": [{"type":"text","text":"OK"}],
    "status": "complete",
    "provider_key": "gw_llamacoder", "model_key": "gpt-oss-120b", "canonical_id": "gpt-oss-120b",
    "usage": {"input_tokens": null, "output_tokens": null, "units": 1},
    "latency_ms": 1621,
    "handoff_packet_id": "…", "execution_id": "3976a6ec-…",
    "skills_used": [], "created_at": "2026-10-18T18:34:32…", "metadata": {}
  },
  "execution_id": "…", "provider_key": "gw_llamacoder", "model_key": "gpt-oss-120b",
  "usage": {"…":"…"},
  "handoff": {"packet_id":"…","estimated_tokens":123,"dropped_messages":0,"summarized":false},
  "trace": null
}
```
**استخرج النص هكذا:** `"".join(p["text"] for p in turn["parts"] if p["type"]=="text")`.
**الموديل الذي أجاب فعلًا** هو `turn.provider_key/model_key` (قد يختلف عن طلبك عند failover).

### 3.3 أمثلة جاهزة

**AUTO (موصى به):**
```json
{"message":"ما عاصمة مصر؟"}
```
**موديل صريح على مزوّد صريح (مُختبر حيًّا):**
```json
{"message":"Reply with exactly: OK","model":"deepseek-v3.2","provider":"gw_deepai"}
{"message":"Reply with exactly: OK","model":"claude-4.5-haiku","provider":"gw_overchat"}
{"message":"Reply with exactly: OK","model":"gpt-oss-120b","provider":"gw_llamacoder"}
```
**JSON مضبوط الشكل:**
```json
{"message":"أعطني {\"city\":..., \"country\":...} لمدينة باريس","generation":{"response_format":{"type":"json_object"},"temperature":0}}
```
**متابعة محادثة:**
```json
{"conversation_id":"<id>","message":"وما عدد سكانها؟"}
```

---

## 4. Streaming: `POST /v1/chat/stream` (SSE)

### 4.1 الطلب
نفس حقول `/v1/chat` مع فروق:
- **وضعان:** conversation-backed (`conversation_id`/`project_id` + `message`) **أو** stateless (`messages[]` بدل `message`).
- `stream_fallback: "buffer"` — **ضعه دائمًا** إذا كنت تستهدف مزوّدات البوابة: هي sync-only، وبدونه يُعاد `failed{reason: unsupported_capability}` بصدق. مع `buffer` تحصل على دفعة واحدة موسومة `synthesized: true`.
- `deadline_ms` (≥1000)، `model_policy` (بديل متقدم لـ `model`).

```json
{"conversation_id":"<id>","message":"اكتب 3 نقاط عن الطاقة الشمسية","stream_fallback":"buffer"}
```
```json
{"messages":[{"role":"system","content":"أجب بإيجاز"},{"role":"user","content":"مرحبا"}],"model":"deepseek-v3.2","stream_fallback":"buffer"}
```
Headers: `accept: text/event-stream`. الرد: `content-type: text/event-stream`, `cache-control: no-cache`.

### 4.2 الأحداث (كل سطر `data:` = JSON واحد)
```
data: {"seq":0,"event_id":"<stream_id>:0","type":"started","stream_id":"…","data":{"provider_key":"gw_deepai","model_key":"deepseek-v3.2"}}
data: {"seq":1,"event_id":"…:1","type":"message_delta","stream_id":"…","data":{"delta":"نص جزئي","synthesized":true}}
data: {"seq":2,"event_id":"…:2","type":"usage","stream_id":"…","data":{"input_tokens":…,"output_tokens":…}}
data: {"seq":3,"event_id":"…:3","type":"completed","stream_id":"…","data":{"provider_key":"gw_deepai","model_key":"deepseek-v3.2","usage":{},"failovers":0,"text_length":…}}
```
| `type` | متى | حقول `data` المهمة |
|---|---|---|
| `started` | دائمًا أولًا | `provider_key`, `model_key` |
| `message_delta` | نص | `delta`, `synthesized?` |
| `tool_call_start/delta/end` | أدوات | `tool_call_id`, `name`, `arguments_delta` |
| `usage` | قبل النهاية | أرقام الاستهلاك |
| `heartbeat` | كل بضع ثوانٍ أثناء الانتظار | — |
| `completed` | **نهائي** | `provider_key`, `model_key`, `usage`, `failovers` |
| `failed` | **نهائي** | `reason` ∈ `stream_interrupted, provider_error, no_candidate, first_byte_timeout, idle_timeout, deadline_exceeded, unsupported_capability, admission_denied`؛ `resumable`, `delivered_until_seq`, `detail` |
| `cancelled` | **نهائي** | `delivered_until_seq`, `partial_text_length` |

**قواعد العميل:**
1. اقرأ حتى حدث نهائي (`completed|failed|cancelled`).
2. احفظ `stream_id` من أول حدث و`seq` آخر حدث استلمته.
3. إذا `failed.resumable == true` ⇒ `POST /v1/chat/stream/{stream_id}:resume {"delivered_until_seq": <seq>}` — لا يُعاد نص سبق تسليمه.
4. للإلغاء: `POST /v1/chat/stream/{stream_id}:cancel` (يُحرّر خانة المزوّد فورًا — حتى في وضع buffer) ثم أغلق الاتصال.
5. `GET /v1/chat/stream/{stream_id}` ⇒ ملخص الحالة.

### 4.3 عميل SSE بـ 15 سطرًا (JavaScript، بلا مكتبات)
```js
const res = await fetch(`${BASE}/v1/chat/stream`, {
  method: "POST", headers: {"content-type":"application/json","accept":"text/event-stream"},
  body: JSON.stringify({ message, conversation_id, stream_fallback: "buffer" }), signal,
});
const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "";
for (;;) {
  const { value, done } = await reader.read(); if (done) break;
  buf += dec.decode(value, { stream: true });
  let i; while ((i = buf.indexOf("\n\n")) >= 0) {
    const frame = buf.slice(0, i); buf = buf.slice(i + 2);
    const line = frame.split("\n").find(l => l.startsWith("data:")); if (!line) continue;
    const ev = JSON.parse(line.slice(5));
    if (ev.type === "message_delta") onDelta(ev.data.delta);
    if (["completed","failed","cancelled"].includes(ev.type)) onEnd(ev);
  }
}
```

---

## 5. ذاكرة طويلة: المشاريع والمحادثات

| الكائن | ماذا يحمل | لماذا |
|---|---|---|
| **Project** | `system_instructions`, `default_model`, `default_constraints[]`, `skills[]`, `agent_profile_id`, `context_window_hint` | "شخصية" التطبيق/الوكيل. كل محادثة تُنشأ تحته ترثه. |
| **Conversation** | `title`, `model`, `objective`, `constraints[]`, `skills[]`, `turn_count`, `summary_turn_id` | الخيط الواحد. التاريخ يُضغط تلقائيًا (Turn من نوع `summary`) عند تجاوز النافذة. |
| **Turn** | رسالة واحدة (user/assistant/system/tool) بـ `seq` متزايد | السجل الكامل — `GET /v1/conversations/{id}/turns?after_seq=` للتحميل التدريجي. |

```bash
# مشروع = وكيل خدمة عملاء
curl -s -X POST $BASE/v1/projects -H 'content-type: application/json' -d '{
  "name":"support-bot",
  "system_instructions":"أنت مساعد دعم فني لشركة X. أجب بالعربية، بإيجاز، ولا تخمّن سياسات غير موجودة.",
  "default_model":"deepseek-v3.2",
  "skills":[{"skill_id":"refund_policy"}]
}'
# أول رسالة من مستخدم جديد: تُنشأ محادثة تحت المشروع
curl -s -X POST $BASE/v1/chat -H 'content-type: application/json' \
  -d '{"project_id":"<project_id>","message":"كيف أسترد المبلغ؟"}'
# احفظ conversation_id في جلسة المستخدم واستخدمه في كل رسالة لاحقة
```
CRUD كامل: `GET/PATCH/DELETE /v1/projects/{id}`, `POST/GET /v1/conversations`, `PATCH /v1/conversations/{id} {"title"|"archived":true}`, `DELETE`.

---

## 6. استراتيجيات متعددة الموديلات (Orchestration) — عبر `/v1/chat` فقط (sync)

| `strategy` | ماذا يفعل | `strategy_options` |
|---|---|---|
| `single` (افتراضي) | موديل واحد | — |
| `cost_tiered` | يبدأ بالأرخص؛ إن فشل `output_spec` يرقّي مرة واحدة؛ إن رفض المزوّد ينتقل للتالي | `tiers: ["cheap","better"]` (تُشتق تلقائيًا إن تُركت فارغة) |
| `sequential_handoff` | مسودة من الموديل 1 ثم تحسين من الموديل 2 (الـ HandoffPacket ينتقل بينهما) | `stages: ["m1","m2",…]` (≥2)، `stage_instructions: []` |
| `judge_select` | عدة مرشحين بالتوازي ثم حكم (موديل أو heuristic على `output_spec`) | `candidates: [...]` (≥2)، `judge: "model"|null`, `max_candidates: 3` |

`output_spec` (مشترك): `{"min_chars", "max_chars", "must_include": [], "must_not_include": [], "json_object": false, "language_hint": "ar"}`.

```json
{"message":"اكتب تعريفًا لـ API في ≤ 200 حرف بالعربية",
 "strategy":"cost_tiered",
 "strategy_options":{"output_spec":{"max_chars":200,"language_hint":"ar"}}}
```
```json
{"message":"اقترح اسمًا لمنتج قهوة","strategy":"judge_select",
 "strategy_options":{"candidates":["deepseek-v3.2","claude-4.5-haiku"],"output_spec":{"max_chars":40}}}
```
الرد يحمل `trace`:
```json
{"trace":{"strategy":"cost_tiered","selected_step":2,"decision_reason":"upgraded tier result (max 1 upgrade)",
 "steps":[{"step":1,"role":"tier","model":"…","provider_key":"…","status":"failed","reason":"execution_failed: …"},
          {"step":2,"role":"tier","model":"…","provider_key":"gw_overchat","model_key":"claude-4.5-haiku","status":"succeeded","spec_score":1.0}],
 "upgrades":1}}
```
> كل خطوة تمر عبر Admission منفصلة؛ `sequential_handoff`/`judge_select` بأقل من موديلين ⇒ `422`.

---

## 7. Skills وAgent Profiles

**Skill** = تعليمات معيارية قابلة لإعادة الاستخدام تُحقن في النظام وتُسجَّل في `turn.skills_used`.
```bash
curl -s -X POST $BASE/v1/admin/skills -H 'content-type: application/json' -d '{
  "id":"refund_policy","name":"سياسة الاسترداد","version":"1.0.0",
  "instructions":"الاسترداد خلال 14 يومًا فقط وبإيصال. لا تَعِد بأي استثناء."
}'
# ربط: بالمشروع / بالمحادثة / بالطلب الواحد
curl -s -X POST $BASE/v1/projects/{id}/skills:assign -d '{"skill_id":"refund_policy"}'
curl -s -X POST $BASE/v1/conversations/{id}/skills:assign -d '{"skill_id":"refund_policy","config":{}}'
# أو في الطلب مباشرة:  {"message":"…","skills":["refund_policy"]}
```
`:enable` / `:disable` / `DELETE` على مستوى المشروع والمحادثة؛ `POST /v1/admin/skills/{id}:disable` يُعطّل الـ Skill عالميًا (الربط بها بعدها ⇒ `422 skill_not_selectable`).

**Agent Profile** = قالب لمشاريع متعددة: `POST /v1/agent-profiles {"name","system_instructions","default_model","skills":[{"skill_id"}]}` ثم `POST /v1/projects {"agent_profile_id": "…"}`.

---

## 8. التوافق مع OpenAI SDK (بلا تعديل كود)

```python
from openai import OpenAI
client = OpenAI(base_url=f"{BASE}/v1", api_key="unused")
r = client.chat.completions.create(
    model="deepseek-v3.2",
    messages=[{"role": "user", "content": "مرحبا"}],
    extra_body={"provider": "gw_deepai"},   # امتداد اختياري
)
print(r.choices[0].message.content, r.model_extra["chat_engine"])  # {execution_id, provider_key, model_key, …}
# stream=True يعمل أيضًا (chunks + data: [DONE])
```
Node: `new OpenAI({ baseURL: `${BASE}/v1`, apiKey: "unused" })`.

---

## 9. السطح المتوافق مع upstream: `POST /v1/execute`
```json
{"ask":"Say OK"}
```
```json
{"ask":"…","model_policy":{"type":"explicit","model_id":"…"},"execution_policy":{"async":true},"payload":{}}
```
- `Idempotency-Key` header مدعوم (نفس المفتاح + نفس الجسم ⇒ نفس النتيجة).
- `async:true` ⇒ `202 {execution_id}` ثم `GET /v1/executions/{id}` و`GET /v1/executions/{id}/events` (SSE lifecycle).
- الأخطاء هنا بكودات المرجع فقط (`entitlement_exceeded 403`, `execution_failed 502`, …).

---

## 10. الأخطاء وكيف تتصرف (Error Handling Playbook)

| HTTP | `error.code` | المعنى | ماذا تفعل |
|---|---|---|---|
| 422 | `validation_error` | حقل ناقص/زائد/خارج النطاق | أصلح الجسم؛ `details.errors[]` |
| 422 | `alias_unconfirmed` | الاسم موجود كـ alias مقترح فقط | استخدم `canonical_id` أو أكّد الـ alias عبر الكتالوج |
| 422 | `conflict` | شرط استراتيجية غير مستوفى (مثل <2 stages) | أصلح `strategy_options` |
| 404 | `not_found` | محادثة/مشروع/skill غير موجود — **أو** لا مرشح قابل للتوجيه الآن (`details.excluded[]` يشرح السبب لكل مزوّد) | في الحالة الثانية: احذف `provider` واترك AUTO، أو جرّب لاحقًا |
| 429 | `admission_denied` | `details.scope` يبدأ بـ `provider:` ⇒ سقف/قاطع ذلك المزوّد (`dimension`: rpm/concurrency، `retry_after_ms`) ؛ `tenant:` ⇒ سقفك أنت | انتظر `retry_after_ms` **أو** أعد الطلب بـ AUTO (مزوّد آخر) |
| 502 | `execution_failed` | المزوّد نفسه رفض: `details.provider_error_category` ∈ `quota_exceeded, invalid_credential, auth_expired, provider_unavailable, timeout, non_retryable_error` | ليس خطأك ولا خطأ المحرك. مع AUTO يحدث failover تلقائيًا؛ مع `provider` صريح جرّب مزوّدًا آخر |
| 503 | `provider_unavailable` / `model_unavailable` | لا مزوّد مؤهل | تحقق من `/healthz` و`/v1/admin/admission` |
| 409 | `idempotency_conflict` | نفس المفتاح بجسم مختلف | مفتاح جديد |

**نمط العميل القوي (pseudo):**
```
try: r = chat(body)
except 429 where details.scope startswith "provider:" and body.provider: body.provider=None; body.model=None; retry once
except 429: sleep(details.retry_after_ms); retry
except 502 execution_failed and body.provider: body.provider=None; retry once (AUTO)
```
الـ SDKs الرسمية (§12) تطبّق هذا الـ backoff تلقائيًا على 429/502/503/504.

---

## 11. حقائق تشغيلية عن مزوّدات البوابة (مهم جدًا)

- **sync-only:** لا SSE حقيقي من `gw_*` ⇒ استخدم `stream_fallback: "buffer"`.
- **حدود محافظة لكل مزوّد:** `rpm 60`, `concurrency 4` (قابلة للتعديل عبر `limits_metadata`/admin). تجاوز التزامن ⇒ 429 فوري بـ `retry_after_ms: 250`.
- **قاطع الدائرة (breaker):** 5 إخفاقات من آخر 10 طلبات على **نفس المزوّد** ⇒ 429 لمدة 30 ثانية على ذلك المزوّد فقط (المستأجر لا يتأثر — v0.1.3).
- **الإجابة الفعلية تتغير مع حسابات/كوكيز المالك على البوابة.** آخر مصفوفة حيّة (2026-10-18): يجيب `gw_deepai`, `gw_llamacoder`, `gw_overchat`, `gw_parallel`؛ يرفض upstream: `gw_groq` (مفتاح غير مهيأ)، `gw_freebuff`/`gw_ivycraft`/`gw_runpod_console` (`invalid_credential`)، `gw_notegpt` (`auth_expired`)، `gw_syntx`/`gw_tinyfish` (`provider_unavailable`/timeout)، `gw_upstage`/`gw_youmind` (timeout 60s). **لا تُثبّت هذه القائمة في كودك** — اعتمد AUTO أو استعلم `/v1/models` وقت التشغيل.
- **بعض "الموديلات" ليست دردشة:** مثل `gw_tinyfish/web-search`, `gw_parallel/parallel-extract`, `gw_ivycraft/text-to-speech` — تُعرض لأن البوابة تعلن `generate_text` لها، لكن مخرجاتها قد تكون نص بحث/استخراج لا إجابة محادثة. استخدمها عن قصد فقط.
- **`gw_voyage`** embeddings فقط ⇒ غير مربوط (`/v1/embeddings` غير مُنفَّذ عمدًا في هذا الإصدار).

---

## 11b. السكربت المرجعي التنفيذي: `reference_client/chat_engine_requests.py`

دالة لكل طلب في هذا الدليل (72 دالة) بتوثيق الجسم والرد داخل docstring، مكتبة قياسية فقط. رابط الـ endpoint خارجي في `reference_client/config/endpoint.json` (أولوية: `--base-url` ← `CHAT_ENGINE_URL` ← الملف).
`--list` يعرض كل الطلبات، التشغيل بلا وسائط ينفّذ جولة حيّة (`evidence/ce20/reference_client_tour.txt`). راجع `reference_client/README.md`.

## 12. الـ SDKs الرسمية (اختصار لكل ما سبق)

**Python** (`pip install -e sdk/python`, تبعية واحدة `httpx`):
```python
from chat_engine_client import ChatEngine
with ChatEngine(BASE) as ce:
    proj = ce.create_project("my-app", system_instructions="أجب بالعربية بإيجاز.")
    r = ce.chat("مرحبا", project_id=proj["id"])            # ينشئ محادثة
    r = ce.chat("تابع", conversation_id=r.conversation_id)  # ذاكرة
    for ev in ce.chat_stream("اشرح أكثر", conversation_id=r.conversation_id, stream_fallback="buffer"):
        if ev.type == "message_delta": print(ev.delta, end="")
    c = ce.completions("deepseek-v3.2", [{"role":"user","content":"hi"}])  # OpenAI-shape
```
`AsyncChatEngine` بنفس الواجهة. الأخطاء: `ChatEngineAPIError{status, code, message, details, retryable}`.

**TypeScript** (`sdk/typescript`, zero-dependency, يعمل في المتصفح وNode):
```ts
import { ChatEngine } from "chat-engine-client";
const ce = new ChatEngine({ baseUrl: BASE });
const r = await ce.chat({ message: "مرحبا" });
for await (const ev of ce.chatStream({ conversation_id: r.conversation_id, message: "تابع", stream_fallback: "buffer" })) {
  if (ev.type === "message_delta") process.stdout.write(String(ev.data.delta ?? ""));
}
await ce.cancelStream(streamId);   // أثناء stream جارٍ
```
مثال مشروع مستهلك كامل: `examples/consumer_project/main.py`.

---

## 13. وصفة بناء تطبيق مدمج (Recipe)

1. **Boot:** `GET /healthz` → إن `gateway.bound` فارغ أظهر تحذيرًا، لا تفشل.
2. **Setup مرة واحدة:** أنشئ Project بتعليمات تطبيقك (+ Skills) واحفظ `project_id` في إعداداتك.
3. **لكل مستخدم/جلسة:** أول رسالة بـ `project_id` ⇒ احفظ `conversation_id` الراجع.
4. **الإرسال:** `POST /v1/chat/stream` مع `stream_fallback: "buffer"` وبدون `model` (AUTO). اعرض `message_delta` تدريجيًا، وعند `completed` اعرض `provider_key/model_key` بشفافية.
5. **الأخطاء:** طبّق جدول §10؛ لا تُعد المحاولة تلقائيًا أكثر من مرتين.
6. **التاريخ:** عند فتح محادثة قديمة `GET /v1/conversations/{id}/turns`.
7. **مهام تحتاج جودة/تكلفة محسوبة:** استخدم `cost_tiered` أو `judge_select` مع `output_spec` بدل الاعتماد على موديل واحد.
8. **القياس:** `GET /v1/usage/records` (لكل طلب: provider, model, usage, latency, status) و`GET /v1/executions/{id}`.

---

## 14. مرجع سريع لكل المسارات

| فئة | المسارات |
|---|---|
| صحة/اكتشاف | `GET /healthz`, `GET /openapi.json`, `GET /docs`, `GET /v1/models`, `GET /v1/openai/models[/{id}]`, `GET /v1/catalog/models[?q]`, `GET /v1/catalog/models/{canonical_id}`, `GET /v1/catalog/aliases`, `POST /v1/catalog/aliases/{alias}:decide` |
| محادثة | `POST /v1/chat`, `POST /v1/chat/stream`, `POST /v1/chat/stream/{id}:cancel`, `POST /v1/chat/stream/{id}:resume`, `GET /v1/chat/stream/{id}` |
| OpenAI-compatible | `POST /v1/chat/completions` |
| Upstream | `POST /v1/execute`, `GET /v1/executions`, `GET /v1/executions/{id}`, `GET /v1/executions/{id}/events` |
| مشاريع/محادثات | `POST/GET /v1/projects`, `GET/PATCH/DELETE /v1/projects/{id}`, `POST/GET /v1/conversations`, `GET/PATCH/DELETE /v1/conversations/{id}`, `GET /v1/conversations/{id}/turns` |
| Skills | `GET /v1/skills`, `GET /v1/skills/{id}`, `POST /v1/admin/skills`, `POST /v1/admin/skills/{id}:disable`, `{GET,PUT} /v1/{projects,conversations}/{id}/skills`, `POST …/skills:assign`, `POST …/skills/{sid}:enable|:disable`, `DELETE …/skills/{sid}` |
| Agent profiles | `POST/GET /v1/agent-profiles`, `GET/PATCH/DELETE /v1/agent-profiles/{id}` |
| الاستهلاك | `GET /v1/usage`, `GET /v1/usage/records[/{request_id}]` |
| Admin (مزوّدات) | `GET /v1/admin/providers[/managed]`, `GET /v1/admin/providers/{key}[/revisions]`, `POST/PUT /v1/admin/providers/{key}` (draft OpenAI-compatible: `{display_name, base_url, static_models[], credential_ref?, extra_headers{}}`), `:validate`, `:verify`, `:enable`, `:disable`, `:archive`, `:rollback` |
| BYOK | `POST /v1/credentials {label, secret, provider_hint?}` → `{credential_ref, fingerprint, last4}` (السر لا يُعاد)، `GET /v1/credentials`, `DELETE /v1/credentials/{id}` |
| Admin (أخرى) | `GET /v1/admin/models`, `GET /v1/admin/pricing`, `PUT /v1/admin/pricing`, `GET /v1/admin/admission`, `GET /v1/admin/routing/weights`, `GET /v1/admin/capabilities/{ledger,catalog,probes}`, `POST /v1/admin/capabilities/{key}/{cap}:probe|:enable` |
| CE-24 (v0.2.0) | `POST /v1/embeddings`, `GET /v1/embeddings/models`, `POST /v1/messages` (Anthropic dialect)، `POST /v1/conversations/{id}/turns/{turn_id}:feedback`، `GET /v1/auth/whoami` + `GET/POST /v1/admin/keys`, `GET/PATCH/DELETE /v1/admin/keys/{id}` (فقط عند `CE_AUTH_MODE≠off`)، `GET /v1/admin/routing/signals`, `GET /v1/admin/providers/{key}/credentials`, `GET/DELETE /v1/admin/cache` |
| App factory (تجريبي) | `POST /v1/apps:generate`, `GET /v1/apps[/{job_id}]`, `:approve`, `:cancel`, `GET /v1/apps/{job_id}/artifacts/{name}` |

---

## 15. ما ليس موجودًا (حتى لا تبحث عنه)
`/v1/images`, `/v1/audio`, WebSocket, تخزين دائم افتراضي (in-memory ما لم يُهيّأ Postgres/Redis — انظر `docs/OPERATIONS.md`). المصادقة المدمجة (مفاتيح افتراضية) موجودة منذ v0.2.0 لكنها **معطّلة افتراضيًا** (`CE_AUTH_MODE=off`) — انظر §16.4.

---

## 16. سطوح CE-24 (v0.2.0) — ما أُضيف من منظومة LiteLLM دون تبنّي الحزمة

كل ما يلي **إضافي** (additive): الحقول الجديدة اختيارية، والسلوك القديم محفوظ عند غياب الإعدادات. لا حزمة `litellm`، لا بوابة أمام المحرّك، لا guardrails بكود مخصّص.

### 16.1 الأسعار: مصدر ثانٍ (LiteLLM prices snapshot) — `A`
`GET /v1/admin/pricing` يعيد الآن `litellm_loaded`, `litellm_fetched_at` إلى جانب `snapshot_loaded`. الترتيب: overrides اليدوية (PUT) > models.dev snapshot > LiteLLM snapshot (`compat/catalogs/litellm_prices_snapshot.json`, 3.3k موديل). التحديث: `python scripts/import_litellm_catalog.py` (stdlib فقط)؛ التعطيل: `CE_PRICING_LITELLM=0`. المقترحات من الكتالوج تحمل `source: "catalog:litellm"` ولا تُستخدم للتوجيه حتى تُؤكَّد.

### 16.2 قاطع الدائرة حسب فئة الخطأ + حدود يومية — `B`
`GET /v1/admin/admission → breaker_policy` يصف السياسة الفعّالة (`legacy` عند غياب القواعد). تفعيل: `CE_BREAKER_POLICY_JSON='recommended'` أو JSON مثل `{"timeout":3,"invalid_credential":{"fails":1,"open_seconds":600},"context_window":0}` (0 = لا يفتح القاطع لأخطاء الطلب نفسه). النطاقات الجديدة: `key:<key_id>`، والأبعاد `rpd`/`tpd` (نافذة 24 ساعة متدحرجة). `details.breaker_reason` في `admission_denied` يذكر الفئة.

### 16.3 أخطاء نافذة السياق — `G`
400/413/422 من المزوّد التي تعني تجاوز السياق تعود `400 bad_request` (OpenAI: `context_length_exceeded`) مع `details.provider_code`, `details.cause`, `details.context_window{limit?, requested?}` — ولا تُطلق failover ولا تفتح القاطع. على البث: `failed{reason:"validation_error"}`.

### 16.4 مفاتيح افتراضية (Virtual Keys) — `D` + `L`
- `CE_AUTH_MODE=off` (الافتراضي): لا middleware، ومسارات `/v1/auth/*` و`/v1/admin/keys*` **غير مركّبة** (404). `healthz.auth_mode` يخبرك.
- `optional`: الطلبات المجهولة تمر؛ من يرسل `Authorization: Bearer ce_live_…` يُحلّ إلى principal وتطبَّق حدوده.
- `required`: 401 `unauthenticated` بلا مفتاح (ما عدا `/healthz`, `/openapi.json`, `/docs`, UI).
- `CE_MASTER_KEY` اختياري؛ إن غاب يُولَّد ويُطبع في السجل مرة واحدة.
- إدارة (المفتاح الرئيسي فقط): `POST /v1/admin/keys {name, project_ids[]?, models_allowed[]?, rpm?, tpm?, rpd?, tpd?, concurrency?, budget_usd?, expires_at?, metadata?}` → `{key (مرة واحدة), record}`; `GET /v1/admin/keys`, `GET/PATCH/DELETE /v1/admin/keys/{id}`; `GET /v1/auth/whoami`.
- التنفيذ: موديل خارج `models_allowed` أو مشروع خارج `project_ids` ⇒ `403 capability_denied`؛ نفاد `budget_usd` ⇒ `403 entitlement_exceeded`؛ تجاوز rpm/tpm/rpd/tpd ⇒ `429 admission_denied` بنطاق `key:<id>`. الإنفاق يُحتسب لكل مفتاح في `/v1/usage` (`keys`). شاشة **Keys** في UI تعرض الوضع وتدير المفاتيح عند التفعيل.

### 16.5 Guardrails مدمجة فقط — `E`
`Project.guardrails: [{type:"keyword_block", keywords[], action:"block"|"redact", mask?, stage:"pre"|"post"|"both"}, {type:"pii_redact", kinds?:["email","card","iban","phone"], stage}]`. أي `type` آخر ⇒ `422 validation_error`. افتراضيات العملية: `CE_GUARDRAILS_JSON='[...]'`. `pre` يعدّل/يمنع رسالة المستخدم قبل الإرسال (`403 capability_denied` مع `details.guardrail/stage`)؛ `post` يعدّل الرد أو يمنعه (الدور يُسجَّل `FAILED`). ما طُبّق يظهر في `turn.metadata.guardrails`.

### 16.6 إشارات التوجيه الحيّة — `C`
`GET /v1/admin/routing/signals → {enabled, providers:[{provider_key, ewma_latency_ms, in_flight, …}]}`. القياس يعمل دائمًا؛ إعادة الترتيب فقط عند `CE_ROUTING_LIVE_SIGNALS=1` وبين مرشّحين متقاربي الدرجة (±0.15) وبمكسب ≥ 20%. الدليل القياسي: `evidence/ce24/live_signals_before_after.json`.

### 16.7 مجموعة مفاتيح لكل مزوّد (Credential Pool) — `K`
`CE_GATEWAY_ROUTES="gw_x=tok1|tok2"` أو مفاتيح مفصولة بفاصلة في البوتستراب ⇒ عند `invalid_credential`/`auth_expired`/`quota_exceeded`/`rate_limited` يُركَن المفتاح (600s أو `retry_after`) ويُستخدم التالي في نفس الطلب التالي. `GET /v1/admin/providers/{key}/credentials` يعرض الحالة (مراجع فقط، لا أسرار).

### 16.8 تقييم الدور — `M`
`POST /v1/conversations/{id}/turns/{turn_id}:feedback {score:-1|0|1, comment?, tags[]}` على أدوار المساعد فقط → الدور مع `metadata.feedback` و`feedback_history`.

### 16.9 Embeddings — `H`
`GET /v1/embeddings/models → {object:"list", data:[{id, owned_by, provider_key}]}`؛ `POST /v1/embeddings {model, input: str|[…], provider?, dimensions?}` (شكل OpenAI) → `{object:"list", data:[{embedding, index}], model, provider_key, dimensions, usage}`. المزوّدون embed-only في البوابة (مثل `gw_voyage`) يظهرون في `healthz.gateway.embed_only` ولا يُرشَّحون للدردشة. **واقع الآن:** مفتاح voyage غير مفعّل في البوابة ⇒ `502 execution_failed` مع `provider_error_category: invalid_credential` — خطأ upstream صادق وليس خطأ في المحرّك.

### 16.10 ذاكرة تطابق تامّ — `F`
`CE_CACHE=memory` (الافتراضي `off`)، `CE_CACHE_TTL_S=300`، `CE_CACHE_MAX=1000`. مفتاح الذاكرة = (tenant, model, provider, messages, generation). الإصابة تعود كدور عادي موسوم `usage.cached:true` و`metadata.cache_hit` بتكلفة صفر؛ لا تُخزَّن ردود استدعاءات الأدوات. `GET /v1/admin/cache`, `DELETE /v1/admin/cache`.

### 16.11 لهجة Anthropic Messages — `I`
`POST /v1/messages` (يُعطَّل بـ `CE_FEATURE_ANTHROPIC_COMPAT=0`) يقبل `{model, max_tokens (إلزامي), messages[], system?, tools?, tool_choice?, stream?, stop_sequences?, metadata.user_id?}` + امتدادات `provider`, `required_capabilities`, `skills`, `stream_fallback`. الرد `{type:"message", content:[{type:"text"}|{type:"tool_use"}], stop_reason, usage{input_tokens,output_tokens}}`؛ البث `message_start → content_block_* → message_delta → message_stop`. الأخطاء بغلاف Anthropic `{type:"error", error:{type, message}}` حتى لأخطاء التحقق (422).

### 16.12 `stream_fallback` على اللهجات
مزوّدو البوابة متزامنون؛ `/v1/chat/completions` و`/v1/messages` يقبلان الآن `stream_fallback:"buffer"` (دفعة واحدة مركّبة، `finish_reason:"stop"`). بدونه يبقى الجواب الصادق: `finish_reason:"error"` / `unsupported_capability`.

---

*كل جسم طلب في هذا الملف مطابق لعقود Pydantic في `chat_engine/*/contracts.py` و`chat_engine/api/*.py` بإصدار v0.2.0؛ عند الشك، `GET /openapi.json` هو الحكم.*
