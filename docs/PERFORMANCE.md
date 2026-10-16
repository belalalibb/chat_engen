# PERFORMANCE — CE-17 Scale Validation (v0.1.0)

> الأرقام أدناه مُعادة الإنتاج بـ `benchmarks/bench.py` على **صندوق بذاكرة ~1 GB وCPU مشتركة** (ليس جهاز مرجعيًا). الأحكام MET/GAP يطبعها السكربت نفسه — لا تُحرَّر يدويًا. المصدر: `evidence/ce17/bench_1k.json`, `evidence/ce17/bench_10k.json`.

## 1. النتائج

| المقياس | الميزانية (CE-17) | N=1,000 | N=10,000 | الحكم @10k |
|---|---|---|---|---|
| Onboarding N مزوّد (register + bindings + ledger) | — | 1.9 s | 348 s | — (35 ms/مزوّد) |
| RSS بعد onboarding | < 1.5 GB | 121 MB | 580 MB | **MET** |
| `catalog.entries()` p50 / p95 | — | 17.6 / 200.7 ms | 197.6 / 212.4 ms | — |
| `GET /v1/catalog/models` صفحة 50 p95 | < 50 ms | 59.6 ms | 1,264.7 ms | **GAP** |
| explicit routing (نموذج واحد تخدمه N مزوّد) p95 | < 10 ms | 32.0 ms | 1,273.7 ms | **GAP** |
| AUTO routing p95 | < 150 ms | 252.4 ms | 2,293.7 ms | **GAP** |
| stream first-byte p95 (200 / 500 متزامن، fake) | < 200 ms | 33.2 ms | 335.9 ms | GAP @500 (MET @200) |
| admission burst 200 عامل مقابل حدود conservative | — | 0 ok / 200 denied | 0 ok / 200 denied | يعمل كما صُمّم (all-or-nothing، concurrency=4) |
| RSS peak | < 1.5 GB | 130 MB | 656 MB | **MET** |

## 2. قراءة صادقة للفجوات

1. **التوجيه الصريح @10k (1.27 s)**: الفجوة داخل `BindingRegistry`/`SimpleScoringRouter` المرجعيين — ترشيح الأهلية خطي على كل الـ bindings لنفس `model_key` (10k مرشح لنموذج `shared-model`). هذا سيناريو متعمَّد الأسوأ (نموذج واحد على 10k مزوّد). في الواقع نادرًا ما يتجاوز عدد المزوّدين لنموذج واحد العشرات؛ عند 3 مزوّدين/نموذج القياس @1k يعطي 32 ms، وهو أيضًا فوق ميزانية 10 ms.
   - **الخطة (لا تعديل في المرجع هنا):** PR additive للمرجع يقدّم `BindingRegistry.bindings_for_model(model_id)` مفهرسًا + top-K قبل scoring. حتى ذلك: `CatalogIndex` في chat_engine يملك الفهرس أصلًا، ويمكن لـ `ChatService.route` تمرير `provider_id` مُسبق الاختيار عند كثرة المرشحين (علم `CE_ROUTE_PRESELECT_K`، مؤجَّل).
2. **AUTO @10k (2.3 s)**: scoring لكل مرشح مؤهل (10k×2 نموذج). نفس العلاج: top-K بعد الأهلية.
3. **صفحة الكتالوج @10k (1.26 s)**: `entries()` نفسه 198 ms؛ الباقي تسلسل JSON لـ 50 صفًا مع قوائم المزوّدين الكاملة (10k provider refs للنموذج المشترك). **علاج رخيص**: اقتطاع `providers[]` في الصفحة إلى أول 20 + `configured_provider_count` (موجود). مؤجَّل لعدم تغيير شكل الاستجابة قبل v0.1.0.
4. **first-byte @500 متزامن (336 ms)**: حلقة حدث واحدة على CPU مشتركة؛ @200 = 33 ms. ليس عائق تصميم — عائق صندوق.
5. **Onboarding 10k = 348 s**: `register_provider` يعيد بناء `CatalogIndex` ويعلن القدرات لكل مزوّد؛ O(N) لكل تسجيل ⇒ O(N²) إجمالًا. **علاج**: `register_many()` بإعادة بناء واحدة (مؤجَّل؛ onboarding 10k مزوّد دفعة واحدة ليس مسار تشغيل عادي).

## 3. ما تحسّن فعليًا في هذه المرحلة

| قبل | بعد | المصدر |
|---|---|---|
| `entries()` @10k p50 = 17,270 ms | 284 ms (اختبار) / 198 ms (bench) | a2a7913: O(1) staleness + sync مرة لكل نسخة فهرس |
| +10 MB RSS لكل Runtime (snapshot models.dev يُحلَّل لكل نسخة) | 0 MB نمو (cache على مستوى العملية + تقليم الحقول) | commit "perf(ce04/ce17)" — كان السبب الجذري لتجمّد الصندوق |
| بوابة الاختبارات تتجمّد | ~2 دقيقة كاملة | تقسيم pytest إلى شريحتين |

## 4. إعادة الإنتاج

```bash
.venv/bin/python benchmarks/bench.py --n 1000  --streams 200 --json out1k.json
.venv/bin/python benchmarks/bench.py --n 10000 --streams 500 --json out10k.json   # يحتاج ~700 MB RSS و~8 دقائق على صندوق 1 GB
```
