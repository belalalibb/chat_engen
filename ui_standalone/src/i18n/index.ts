export type Lang = "ar" | "en";

const dict = {
  ar: {
    app_title: "chat_engine",
    nav_chat: "المحادثة", nav_conversations: "المحادثات والمشاريع", nav_catalog: "الكتالوج",
    nav_providers: "المزوّدون", nav_capabilities: "القدرات", nav_admission: "القبول والصحة", nav_skills: "المهارات",
    send: "إرسال", cancel: "إلغاء", resume: "استئناف", new_chat: "محادثة جديدة", model: "النموذج", auto: "تلقائي",
    strategy: "الاستراتيجية", strategy_single: "نموذج واحد", strategy_handoff: "تسليم تسلسلي", strategy_judge: "حَكَم يختار", strategy_cost: "طبقات الكلفة",
    placeholder: "اكتب رسالتك…", streaming: "جارٍ البث…", partial: "جزئي", failed: "فشل", resumable: "قابل للاستئناف",
    projects: "المشاريع", conversations: "المحادثات", create: "إنشاء", name: "الاسم", system_instructions: "تعليمات النظام", delete: "حذف", archive: "أرشفة", turns: "الأدوار",
    search: "بحث", canonical: "المعرّف القياسي", providers_col: "المزوّدون", eligible: "مؤهل", aliases: "الأسماء البديلة", confirm: "تأكيد", reject: "رفض", proposed: "مقترح",
    provider_key: "مفتاح المزوّد", base_url: "Base URL", api_key: "مفتاح API (لا يُخزَّن في المتصفح)", state: "الحالة", validate: "تحقّق", verify: "فحص حي", enable: "تفعيل", disable: "تعطيل", archive_p: "أرشفة", rollback: "تراجع", add_provider: "إضافة مزوّد OpenAI-compatible", models_csv: "النماذج (مفصولة بفواصل)", revisions: "الإصدارات",
    capability: "القدرة", declared: "مُعلنة", verified: "مُتحقَّقة", enabled_c: "مفعّلة", available: "متاحة الآن", probe: "فحص", ledger: "سجل القدرات",
    health: "الصحة", upstream: "المرجع", admission: "حدود القبول", backend: "الخلفية", replicas_safe: "آمن للنسخ المتعددة", scope: "النطاق", rpm: "طلبات/د", tpm: "رموز/د", concurrency: "التزامن", budget: "الميزانية",
    skills: "المهارات", skill_id: "المعرّف", version: "الإصدار", status: "الحالة", register_skill: "تسجيل مهارة محلية", instructions: "التعليمات", assign: "إسناد", effective: "الفعّالة", agent_profiles: "ملفات الوكيل", source: "المصدر", skills_used: "المهارات المستخدمة",
    trace: "الأثر", step: "الخطوة", reason: "السبب", selected: "المختار", usage: "الاستهلاك", latency: "زمن الاستجابة",
    lang: "English", empty: "لا توجد عناصر", loading: "جارٍ التحميل…", error: "خطأ", ok: "تم", rows: "صفوف",
    theme: "المظهر", collapse: "طيّ القائمة", workspace: "مساحة العمل", admin_section: "الإدارة",
    welcome_title: "مرحباً بك في chat_engine", welcome_sub: "محرّك محادثة متعدد المزوّدين. اختر نموذجاً أو اتركه تلقائياً، وابدأ الحوار.",
    chip_1: "لخّص لي هذا النص", chip_2: "اكتب كود Python يقرأ ملف CSV", chip_3: "اشرح مفهوم Handoff بين النماذج", chip_4: "ساعدني في صياغة بريد احترافي",
    you: "أنت", assistant: "المساعد", system_lbl: "النظام", copy: "نسخ", copied: "نُسخ", hint_send: "Ctrl+Enter للإرسال", online: "متصل", offline: "غير متصل",
  },
  en: {
    app_title: "chat_engine",
    nav_chat: "Chat", nav_conversations: "Conversations & Projects", nav_catalog: "Catalog",
    nav_providers: "Providers", nav_capabilities: "Capabilities", nav_admission: "Admission & Health", nav_skills: "Skills",
    send: "Send", cancel: "Cancel", resume: "Resume", new_chat: "New chat", model: "Model", auto: "Auto",
    strategy: "Strategy", strategy_single: "Single model", strategy_handoff: "Sequential handoff", strategy_judge: "Judge select", strategy_cost: "Cost tiered",
    placeholder: "Type your message…", streaming: "Streaming…", partial: "partial", failed: "failed", resumable: "resumable",
    projects: "Projects", conversations: "Conversations", create: "Create", name: "Name", system_instructions: "System instructions", delete: "Delete", archive: "Archive", turns: "Turns",
    search: "Search", canonical: "Canonical ID", providers_col: "Providers", eligible: "Eligible", aliases: "Aliases", confirm: "Confirm", reject: "Reject", proposed: "proposed",
    provider_key: "Provider key", base_url: "Base URL", api_key: "API key (never stored in the browser)", state: "State", validate: "Validate", verify: "Verify (live)", enable: "Enable", disable: "Disable", archive_p: "Archive", rollback: "Rollback", add_provider: "Add OpenAI-compatible provider", models_csv: "Models (comma-separated)", revisions: "Revisions",
    capability: "Capability", declared: "Declared", verified: "Verified", enabled_c: "Enabled", available: "Available now", probe: "Probe", ledger: "Capability ledger",
    health: "Health", upstream: "Upstream", admission: "Admission limits", backend: "Backend", replicas_safe: "Replicas safe", scope: "Scope", rpm: "RPM", tpm: "TPM", concurrency: "Concurrency", budget: "Budget",
    skills: "Skills", skill_id: "ID", version: "Version", status: "Status", register_skill: "Register local skill", instructions: "Instructions", assign: "Assign", effective: "Effective", agent_profiles: "Agent profiles", source: "Source", skills_used: "Skills used",
    trace: "Trace", step: "Step", reason: "Reason", selected: "Selected", usage: "Usage", latency: "Latency",
    lang: "العربية", empty: "Nothing here", loading: "Loading…", error: "Error", ok: "OK", rows: "rows",
    theme: "Theme", collapse: "Collapse sidebar", workspace: "Workspace", admin_section: "Administration",
    welcome_title: "Welcome to chat_engine", welcome_sub: "A multi-provider chat engine. Pick a model or leave it on auto, and start the conversation.",
    chip_1: "Summarize this text for me", chip_2: "Write Python that reads a CSV file", chip_3: "Explain model handoff", chip_4: "Help me draft a professional email",
    you: "You", assistant: "Assistant", system_lbl: "System", copy: "Copy", copied: "Copied", hint_send: "Ctrl+Enter to send", online: "Online", offline: "Offline",
  },
} as const;

export type Key = keyof (typeof dict)["en"];

let current: Lang = (localStorage.getItem("ce.lang") as Lang) || "ar";
const listeners = new Set<() => void>();

export const t = (k: Key): string => (dict[current] as Record<string, string>)[k] ?? k;
export const lang = () => current;
export const setLang = (l: Lang) => {
  current = l;
  localStorage.setItem("ce.lang", l);
  document.documentElement.lang = l;
  document.documentElement.dir = l === "ar" ? "rtl" : "ltr";
  listeners.forEach((f) => f());
};
export const onLang = (f: () => void) => { listeners.add(f); return () => listeners.delete(f); };
setLang(current);

/* theme (dark-first; persisted) */
export type Theme = "dark" | "light";
let theme: Theme = (localStorage.getItem("ce.theme") as Theme) || "dark";
export const getTheme = () => theme;
export const setTheme = (th: Theme) => {
  theme = th;
  localStorage.setItem("ce.theme", th);
  document.documentElement.setAttribute("data-theme", th);
  listeners.forEach((f) => f());
};
setTheme(theme);
