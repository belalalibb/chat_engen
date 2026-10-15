# ADR-CE-0002 — Error codes: upstream `ErrorCode` + chat_engine additive codes + OpenAI envelope

- **Status:** ACCEPTED (chat_engine side) / **PROPOSED to upstream** (append-only addition to `core.contracts.errors.ErrorCode`)
- **Date:** 2026-10-15
- **Relates to:** ADR-CE-0001 D0 (additive-only), D8 (compat gates); `docs/COMPATIBILITY_MAP.md` §5

## السياق (Context)

المرجع `general_ai_core` يعرّف 11 كود خطأ مغلقة (`ErrorCode`) ويخدمها بغلاف `ErrorEnvelope{error:{code,message,retryable,details,trace_id}}`. يحتاج chat_engine إلى حالات لا يغطيها المرجع (تدفق منقطع قابل للاستئناف، رفض القبول قبل التنفيذ، alias غير مؤكّد…) ويجب ألا يغيّر الكودات المرجعية أو شكل الغلاف على المسارات المتوافقة بايتًا.

## القرار (Decision)

1. **المسارات المرجعية** (`/v1/execute`, `/v1/executions/*`, `/v1/models`, `/v1/events`, `/v1/skills`, `/v1/templates`) تخدم **حصريًا** الكودات المرجعية الـ 11 وبنفس HTTP status (`HTTP_STATUS_BY_CODE`). أي كود chat_engine يصل إليها يُخفَّض عبر `DOWNGRADE_FOR_EXECUTE` (مثلاً `admission_denied → rate_limited`).
2. **الكودات الإضافية** (`ChatEngineErrorCode`) تُخدم فقط على مسارات chat_engine الجديدة:

   | code | HTTP | متى |
   |---|---|---|
   | `unsupported_capability` | 422 | `required_capabilities` تطلب قدرة غير قابلة للتنفيذ على المزوّد المختار (CapabilityLedger gate) |
   | `admission_denied` | 429 | AdmissionPort رفض الحجز (rpm/tpm/concurrency/budget/breaker) — `details.retry_after_ms` |
   | `idempotency_conflict` | 409 | نفس `Idempotency-Key` بجسم مختلف |
   | `stream_interrupted` | 502 | فشل التدفق بعد أول delta — `resumable:true` + `resume_url` |
   | `alias_unconfirmed` | 422 | النموذج له aliases مقترحة فقط؛ يلزم `:decide` |
   | `not_found` | 404 | مورد chat_engine غير موجود (project/conversation/stream/alias) |
   | `conflict` | 409 | حالة غير صالحة للانتقال (lifecycle/stream not resumable) |
   | `execution_failed` | 502 | (مُستعار من المرجع بنفس الدلالة) آخر خطأ مزوّد مطبَّع في `details.provider_error_category` |

3. **الغلاف على مسارات chat_engine** يطابق شكل المرجع حرفيًا (`error.{code,message,retryable,details,trace_id}`) لتمكين عميل واحد.
4. **السطح المتوافق مع OpenAI** (`/v1/chat/completions`, `/v1/openai/models`) يستخدم غلاف OpenAI `error.{message,type,code,param}` حيث `type` يُشتق من HTTP status (`invalid_request_error|authentication_error|permission_error|not_found_error|rate_limit_error|api_error`) و`code` يحمل الكود المرجعي/الإضافي **بلا تغيير** — فلا تضيع المعلومات عند استخدام SDK قياسي. أخطاء التدفق تُرسل كـ chunk أخير بـ `finish_reason:"error"` و`error{code}` ثم `data: [DONE]`.
5. **اقتراح للمرجع (append-only):** إضافة القيم `UNSUPPORTED_CAPABILITY`, `ADMISSION_DENIED`, `IDEMPOTENCY_CONFLICT`, `STREAM_INTERRUPTED` إلى `ErrorCode` مع صفوف `HTTP_STATUS_BY_CODE`؛ لا يُدمج هنا ولا يُفترض وجوده. حتى يحدث ذلك يبقى الخفض (`DOWNGRADE_FOR_EXECUTE`) هو العقد.

## خريطة التطبيع من `ProviderErrorCategory` (12) → كود

| category | upstream code | OpenAI-compat (status, code) |
|---|---|---|
| rate_limited | rate_limited (429) | 429 `rate_limited` |
| quota_exceeded | entitlement_exceeded (403) | 502 `execution_failed` + details.category |
| model_unavailable | model_unavailable (503) | 503 `model_unavailable` |
| provider_unavailable | provider_unavailable (503) | 503 `provider_unavailable` |
| auth_failed / invalid_credentials | execution_failed (502) | 401 `unauthenticated` |
| كل ما تبقى | execution_failed (502) | 502 `execution_failed` |

## العواقب (Consequences)

- عميل واحد يستطيع التعامل مع المسارين؛ `code` ثابت عبر الأغلفة.
- أي كود جديد يتطلب: صف في هذا الجدول + اختبار في `tests/api/test_error_mapping.py` + ذكر في `docs/API.md` — وإلا تفشل بوابة المراجعة (قاعدة "لا كود خطأ بلا توثيق").
- Rollback: الكودات الإضافية خلف مسارات chat_engine فقط؛ حذف المسار يحذف الكود.
