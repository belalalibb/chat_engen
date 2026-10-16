# consumer_project — إثبات الاستقلال (CE-11)

مشروع مستقل يستهلك chat_engine **بلا أي ملف من `apps/` أو `ui/` المرجعية**:

```bash
python -m venv .venv && . .venv/bin/activate
pip install "chat_engine @ git+https://github.com/belalalibb/chat_engen.git" -e ../../sdk/python
python -m chat_engine serve &          # PORT=8080
python main.py                         # محادثة متعددة الأدوار + stream + OpenAI-compatible
```

`main.py` يفحص أيضًا R1 (تصادم اسم الحزمة `core`): يتأكد أن `core` المستورَد هو حزمة general_ai_core المثبتة وليس مجلدًا محليًا.
