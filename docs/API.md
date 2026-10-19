# chat_engine — API Reference (v0.1)

> Base URL: `http://<host>:8080`. OpenAPI: `GET /openapi.json` (كل مسار يحمل `x-ce-stability: stable | experimental | admin`). Swagger UI: `/docs`.
> **دليل الاستخدام العملي (أمثلة كاملة + playbook الأخطاء):** `docs/INTEGRATION_GUIDE.md`.
> المصادقة: Phase 1 بلا auth (tenant/user demo ثابتان) — انظر `README.md` قبل أي نشر عام.

## 0. ثلاث طبقات

| الطبقة | المسارات | الاستقرار | الملاحظة |
|---|---|---|---|
| **Upstream-compatible** | `/v1/execute`, `/v1/executions*`, `/v1/models`, `/v1/events`, `/v1/skills`, `/v1/templates` | stable | متطابقة بايتًا مع `general_ai_core @0a13556` (golden في `compat/`). |
| **OpenAI-compatible** | `/v1/chat/completions`, `/v1/openai/models` | stable | تعمل مع `openai` SDK الرسمي بلا تعديل (مُختبر hermetic). |
| **chat_engine-native** | `/v1/chat`, `/v1/chat/stream*`, `/v1/projects*`, `/v1/conversations*`, `/v1/catalog*`, `/v1/skills/*` (CE-19) | experimental | قد تتغير قبل v1.0. |
| **Admin** | `/v1/admin/*`, `/v1/credentials*` | admin | BYOK، دورة حياة المزوّد، القدرات، القبول. |

## 1. OpenAI-compatible

### `POST /v1/chat/completions`
المدخلات (subset): `model` (canonical_id مثل `openai/gpt-4o` أو model_key)، `messages[]` (content نص أو parts نصية)، `stream`, `temperature`, `top_p`, `max_tokens|max_completion_tokens`, `stop`, `tools`, `tool_choice`, `response_format`, `stream_options.include_usage`.
امتدادات chat_engine (تمرَّر عبر `extra_body` في SDK): `provider`, `required_capabilities[]`, `skills[]`.

- **sync** → `chat.completion` قياسي + حقل إضافي `chat_engine{execution_id, provider_key, model_key, handoff_packet_id, latency_ms}`.
- **stream** → `chat.completion.chunk` SSE؛ أول chunk `delta.role=assistant`؛ آخر chunk `finish_reason: stop|tool_calls` (+`usage` إن طُلب) ثم `data: [DONE]`. عند الفشل بعد أول delta: chunk بـ `finish_reason:"error"`, `error{code:"stream_interrupted"}`, `chat_engine{resumable, resume_url}`.

```python
from openai import OpenAI
c = OpenAI(base_url="http://localhost:8080/v1", api_key="unused")
r = c.chat.completions.create(model="local-echo-1", messages=[{"role":"user","content":"hi"}])
```

### `GET /v1/openai/models`, `GET /v1/openai/models/{id}`
قائمة OpenAI (`{object:"list", data:[{id, object:"model", owned_by}]}`) من الكتالوج: صف لكل canonical_id وصف لكل model_key.

## 2. Conversations (chat_engine-native)

| Method | Path | الوصف |
|---|---|---|
| POST | `/v1/projects` | `{name, system_instructions?, default_model?, default_constraints[], summarize_with?, context_window_hint?, skills[], agent_profile_id?}` → 201 |
| GET/PATCH/DELETE | `/v1/projects/{id}` | |
| GET | `/v1/projects` | `{projects[], total}` |
| POST | `/v1/conversations` | `{title?, project_id?, model?, objective?, constraints[], skills[]}` → 201 |
| GET | `/v1/conversations?project_id&include_archived&limit&offset` | |
| GET/PATCH/DELETE | `/v1/conversations/{id}` | PATCH يقبل `archived` |
| GET | `/v1/conversations/{id}/turns?after_seq&limit` | `{turns[], total, conversation_id}` — `seq` يبدأ من 1 |
| POST | `/v1/chat` | `{conversation_id? \| project_id?, message, model?, provider?, generation{}, tools[], required_capabilities[], skills[]?}` → `ChatResponse{conversation_id, turn, user_turn_id, execution_id, provider_key, model_key, usage, handoff{packet_id, estimated_tokens, dropped_messages, summarized}}` |

**Turn**: `{id, seq, kind: message|summary|system|tool, role, parts[], status: complete|partial|failed, provider_key, model_key, canonical_id, usage, latency_ms, handoff_packet_id, execution_id, stream_id, skills_used[{skill_id, version, source}], metadata}`.
**التلخيص**: عندما يُسقط المترجم ≥6 رسائل قديمة لتناسب نافذة السياق، يُنشأ Turn من نوع `summary` (role=system) يصبح نقطة القطع (`conversation.summary_turn_id`)؛ `metadata.summary_source ∈ {model, extractive}`.

### Streaming: `POST /v1/chat/stream` (SSE)
- **stateless**: `{messages[], model?, provider?, model_policy?, objective?, constraints[], generation{}, tools[], stream_fallback?: "buffer", deadline_ms?, required_capabilities[], skills[]}`
- **conversation-backed**: `{conversation_id? | project_id?, message, ...}` — يحفظ Turn المستخدم ثم Turn المساعد بحالة `complete|partial|failed` عند الحدث النهائي.
- الأحداث: `started, message_delta{delta}, tool_call_start/delta/end, usage, heartbeat, completed{provider_key, model_key, usage, failovers}, failed{reason, resumable, resume_url?}, cancelled`. كل حدث يحمل `seq` متزايدًا و`event_id`.
- `POST /v1/chat/stream/{id}:cancel`, `GET /v1/chat/stream/{id}` (ملخص), `POST /v1/chat/stream/{id}:resume {delivered_until_seq}` — لا يُعاد إرسال نص سبق تسليمه.

## 3. Catalog
`GET /v1/catalog/models?offset&limit&q`, `GET /v1/catalog/models/{canonical_id}`, `GET /v1/catalog/aliases?alias&decision`, `POST /v1/catalog/aliases/{alias}:decide {canonical_id, decision: confirmed|rejected}`.
قاعدة الدمج: نموذج canonical يُعرض مرة واحدة؛ aliases المقترحة لا تُستخدم للتوجيه حتى تُؤكَّد (`alias_unconfirmed` 422).

## 4. Admin
- المزوّدون (شكل المرجع): `GET /v1/admin/providers`, `GET /v1/admin/models`, `GET /v1/admin/routing/weights`.
- دورة الحياة (CE-05): `GET /v1/admin/providers/managed`, `POST/PUT /v1/admin/providers/{key}` (draft)، `:validate`, `:verify`, `:enable`, `:disable`, `:archive`, `:rollback {revision}`, `GET .../revisions` (append-only).
- BYOK: `POST /v1/credentials {provider_key, secret}` → `{credential_ref, fingerprint, last4}` (السر لا يُعاد أبدًا)، `GET /v1/credentials`, `DELETE /v1/credentials/{id}`.
- القدرات (CE-06): `GET /v1/admin/capabilities/ledger|catalog|probes`, `POST /v1/admin/capabilities/{provider}/{cap}:probe|:enable`. القاعدة: `declared ∧ verified ∧ enabled ∧ available_now`.
- القبول (CE-07): `GET /v1/admin/admission` (لقطة الحدود والعدادات).
- `GET /healthz` → `{status, version, upstream_sha, providers[], admission_backend, replicas_safe, compat{not_served[]}}`.

## 5. الأخطاء (ADR-CE-0002)

غلاف موحّد على المسارات المرجعية و chat_engine-native:
```json
{"error": {"code": "...", "message": "...", "retryable": false, "details": {}, "trace_id": null}}
```
- **كودات المرجع (11)**: `validation_error 422, unauthenticated 401, unauthorized 403, entitlement_exceeded 403, capability_denied 403, provider_unavailable 503, model_unavailable 503, tool_approval_required 409, rate_limited 429, execution_failed 502, internal_error 500`.
- **كودات chat_engine (على مساراته فقط)**: `unsupported_capability 422`, `admission_denied 429` (`details.retry_after_ms`), `idempotency_conflict 409`, `stream_interrupted 502` (resumable), `alias_unconfirmed 422`, `not_found 404`, `conflict 409`, و`execution_failed 502` بنفس دلالة المرجع.
- على `/v1/execute` تُخفَّض كودات chat_engine إلى كود مرجعي (`admission_denied → rate_limited`…).
- السطح OpenAI-compatible يستخدم `{"error": {"message", "type", "code", "param"}}` و`code` يحمل نفس القيم أعلاه.

## 6. Headers
- `Idempotency-Key` على `/v1/execute` (CE-07): نفس المفتاح + نفس الجسم ⇒ نفس النتيجة؛ جسم مختلف ⇒ `idempotency_conflict`.
- SSE: `cache-control: no-cache`, `x-accel-buffering: no`.

## 7. ما ليس هنا (FUTURE)
`/v1/embeddings`, `/v1/images`, `/v1/audio` — غير مُنفّذة عمدًا (CE-10 Out).
