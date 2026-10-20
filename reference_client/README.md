# reference_client — المرجع التنفيذي لكل طلبات chat_engine

ملف بايثون واحد (`chat_engine_requests.py`) فيه **دالة لكل request** في المحرك (72 طلبًا) مع توثيق شكل الجسم/الاستعلام/الهيدرز وشكل الرد داخل docstring كل دالة — مكتبة قياسية فقط (`urllib`)، بلا تثبيت.
التوأم النصّي له: `docs/INTEGRATION_GUIDE.md`.

## رابط الـ endpoint (خارجي، في مجلد منفصل)

`config/endpoint.json` — عدّل `base_url` فقط:
```json
{ "base_url": "https://<host>", "timeout_s": 180, "stream_fallback": "buffer", "api_key": null, "headers": {} }
```
أولوية الحل: `--base-url` (CLI) ← `CHAT_ENGINE_URL` (env) ← `config/endpoint.json`.

## الاستخدام

```bash
python reference_client/chat_engine_requests.py --list            # كل الطلبات في سطر لكل واحد
python reference_client/chat_engine_requests.py                   # جولة حيّة تنفّذ الطلبات المرجعية
python reference_client/chat_engine_requests.py --only chat       # قسم واحد: discovery|chat|stream|strategies|memory|skills|openai|execute|admin|errors
CHAT_ENGINE_URL=http://localhost:8080 python reference_client/chat_engine_requests.py
```

## كمكتبة داخل تطبيقك

```python
import sys

sys.path.insert(0, "reference_client")
from chat_engine_requests import (
    Client,
    Config,
    chat_auto,
    chat_stream,
    collect_stream,
    text_of,
    chat_robust,
)

c = Client(Config.load())  # يقرأ config/endpoint.json
r = chat_auto(c, "مرحبا")  # AUTO + failover
print(r["provider_key"], r["model_key"], text_of(r))
text, terminal, stream_id = collect_stream(
    chat_stream(c, message="تابع", conversation_id=r["conversation_id"])
)
r = chat_robust(
    c, "سؤال مهم", model="deepseek-v3.2", provider="gw_deepai"
)  # يسقط التثبيت ويعيد المحاولة عند 429/502
```

## الأقسام
| # | القسم | الدوال |
|---|---|---|
| 1 | Discovery | `healthz`, `openai_models`, `openai_model`, `models`, `catalog_models`, `catalog_model`, `catalog_aliases`, `catalog_alias_decide`, `admission_snapshot` |
| 2 | Chat (sync) | `chat` (كل الحقول)، `chat_auto`, `chat_explicit`, `chat_json`, `chat_cost_tiered`, `chat_sequential_handoff`, `chat_judge_select` |
| 3 | Streaming | `chat_stream`, `stream_cancel`, `stream_summary`, `stream_resume`, `collect_stream` |
| 4 | Memory | `create/list/get/update/delete_project`, `create/list/get/update/delete_conversation`, `turns` |
| 5 | Skills & Profiles | `register_skill`, `list_skills`, `get_skill`, `disable_skill_globally`, `assign_skill`, `replace_skills`, `list_assigned_skills`, `enable/disable/remove_skill`, `create/list/get/update/delete_agent_profile` |
| 6 | OpenAI-compatible | `chat_completions` (sync/stream) |
| 7 | Upstream | `execute`, `list_executions`, `get_execution`, `execution_events` |
| 8 | Usage | `usage`, `usage_records`, `usage_record` |
| 9 | Admin | `admin_providers[_managed]`, `admin_provider`, `admin_provider_draft`, `admin_provider_transition`, `admin_provider_revisions`, `create/list/delete_credential`, `capabilities_ledger/catalog/probes`, `capability_probe/enable`, `admin_models`, `admin_pricing`, `admin_routing_weights` |
| 9b | CE-24 (v0.2.0) | `admin_pricing_sources`, `admission_breaker_policy`, `whoami`, `create/list/get/update/delete_key`, `create_guarded_project`, `routing_signals`, `provider_credentials`, `turn_feedback`, `embeddings_models`, `embeddings`, `cache_stats`, `cache_clear`, `anthropic_messages`, `anthropic_messages_stream` |
| 10 | Playbook | `chat_robust` — سياسة الاسترداد الموصى بها ككود |

آخر جولة حيّة ضد البوابة: `evidence/ce24/reference_client_tour.txt` (`TOUR DONE failures=0`، تشمل قسم `ce24`). قسم `--only ce24` يتصرّف بصدق مع `CE_AUTH_MODE=off` (مسارات المفاتيح غير مركّبة) ومع رفض upstream لـ embeddings.
