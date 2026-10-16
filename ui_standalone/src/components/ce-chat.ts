import { css, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { icons, logo } from "./icons";
import { api, getJson, sendJson } from "../api/client";
import type { StreamEvent, Turn } from "../api/sdk";

interface Msg {
  id: string;
  role: string;
  text: string;
  status?: string;
  provider_key?: string | null;
  model_key?: string | null;
  skills_used?: Array<{ skill_id: string; version?: string; source?: string }>;
  trace?: Record<string, unknown> | null;
  stream_id?: string | null;
  resumable?: boolean;
  delivered_until_seq?: number;
  usage?: Record<string, unknown>;
}

@customElement("ce-chat")
export class CeChat extends CeElement {
  static styles = [
    CeElement.baseStyles,
    css`
      :host { display: grid; grid-template-rows: var(--ce-header) 1fr auto; height: 100vh; min-width: 0; }

      /* header */
      header {
        display: flex; align-items: center; gap: 10px; padding: 0 18px; border-bottom: 1px solid var(--ce-border);
        background: var(--ce-glass); backdrop-filter: var(--ce-blur); -webkit-backdrop-filter: var(--ce-blur); min-width: 0;
      }
      header .title { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      header .title h2 { margin: 0; font-size: var(--ce-fs-base); font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .badge { display: inline-flex; align-items: center; gap: 6px; font-size: var(--ce-fs-xs); color: var(--ce-text-dim); font-family: var(--ce-mono); }
      header .spacer { flex: 1; }
      header .controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
      header select, header input { background: var(--ce-glass); border-radius: var(--ce-radius-full); padding: 6px 12px; max-width: 220px; }
      header label { font-size: var(--ce-fs-xs); color: var(--ce-text-muted); }

      /* log */
      .log { overflow-y: auto; padding: 20px 18px 8px; scroll-behavior: smooth; }
      .inner { max-width: var(--ce-content-max); margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }

      .msg { display: flex; gap: 12px; align-items: flex-start; animation: ce-fade-up .28s var(--ce-ease) both; }
      .avatar { width: 34px; height: 34px; border-radius: var(--ce-radius-sm); flex: none; display: grid; place-items: center; font-weight: 700; font-size: var(--ce-fs-sm); }
      .avatar svg { width: 18px; height: 18px; }
      .msg.user .avatar { background: var(--ce-gradient); color: #fff; box-shadow: 0 2px 10px var(--ce-accent-glow); }
      .msg.assistant .avatar { background: var(--ce-bg-3); border: 1px solid var(--ce-border-strong); color: var(--ce-accent); }
      .msg.system .avatar { background: transparent; border: 1px dashed var(--ce-border-strong); color: var(--ce-text-muted); }
      .body { flex: 1; min-width: 0; }
      .head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; min-height: 22px; }
      .sender { font-size: var(--ce-fs-xs); font-weight: 700; color: var(--ce-text-dim); letter-spacing: .02em; }
      .actions { display: flex; gap: 2px; opacity: 0; transition: opacity var(--ce-dur) var(--ce-ease); margin-inline-start: auto; }
      .msg:hover .actions, .actions:focus-within { opacity: 1; }
      .actions button { width: 26px; height: 26px; padding: 0; }
      .actions button svg { width: 14px; height: 14px; }
      .text { white-space: pre-wrap; line-height: 1.65; word-break: break-word; }
      .msg.user .text { background: var(--ce-glass); border: 1px solid var(--ce-border); border-radius: var(--ce-radius) var(--ce-radius) var(--ce-radius) 4px; padding: 10px 14px; display: inline-block; max-width: 100%; }
      .msg.system .text { color: var(--ce-text-dim); font-size: var(--ce-fs-sm); }
      .cursor .text::after { content: "▍"; color: var(--ce-accent); animation: blink 1s steps(1) infinite; margin-inline-start: 1px; }
      @keyframes blink { 50% { opacity: 0; } }
      .meta { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
      details { margin-top: 10px; background: var(--ce-glass); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm); padding: 8px 10px; }
      details table { margin-top: 8px; }

      /* welcome */
      .welcome { position: relative; text-align: center; padding: 56px 16px 24px; display: flex; flex-direction: column; align-items: center; gap: 12px; }
      .welcome .glow { position: absolute; top: 20px; left: 50%; width: 360px; height: 360px; transform: translateX(-50%); border-radius: 50%; pointer-events: none;
        background: radial-gradient(circle, var(--ce-accent-glow), transparent 65%); filter: blur(30px); opacity: .7; }
      .welcome .mark { animation: ce-float 5s ease-in-out infinite; filter: drop-shadow(0 8px 24px var(--ce-accent-glow)); position: relative; }
      .welcome h1 { margin: 6px 0 0; font-size: var(--ce-fs-2xl); font-weight: 800; letter-spacing: -.02em; position: relative; }
      .welcome p { margin: 0; color: var(--ce-text-dim); max-width: 520px; position: relative; }
      .chips { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 12px; position: relative; }
      .chip { border-radius: var(--ce-radius-full); background: var(--ce-glass); padding: 8px 14px; font-weight: 500; }
      .chip:hover:not(:disabled) { border-color: var(--ce-accent); color: var(--ce-accent); background: var(--ce-accent-soft); transform: translateY(-1px); }

      /* composer */
      .composer { padding: 8px 18px 14px; }
      .composer .inner { gap: 6px; }
      .bar {
        display: flex; align-items: flex-end; gap: 8px; padding: 8px 8px 8px 16px; border-radius: var(--ce-radius-xl);
        background: var(--ce-glass); border: 1px solid var(--ce-border); backdrop-filter: var(--ce-blur); -webkit-backdrop-filter: var(--ce-blur);
        box-shadow: var(--ce-shadow-sm); transition: border-color var(--ce-dur) var(--ce-ease), box-shadow var(--ce-dur) var(--ce-ease);
      }
      .bar:focus-within { border-color: var(--ce-accent); box-shadow: 0 0 0 3px var(--ce-accent-glow); }
      .bar textarea { flex: 1; background: transparent; border: none; padding: 8px 0; min-height: 24px; max-height: 200px; resize: none; font-size: var(--ce-fs-base); }
      .bar textarea:focus-visible { box-shadow: none; }
      .bar .send { width: 40px; height: 40px; padding: 0; border-radius: var(--ce-radius-full); flex: none; }
      .bar .send svg { width: 18px; height: 18px; }
      :host-context([dir="rtl"]) .bar .send.primary svg { transform: scaleX(-1); }
      .hint { text-align: center; color: var(--ce-text-muted); font-size: var(--ce-fs-xs); }
      @media (max-width: 760px) { :host { height: calc(100vh - 56px); grid-template-rows: auto 1fr auto; } header { flex-wrap: wrap; padding: 8px 12px; } .log { padding: 12px; } .composer { padding: 6px 10px 10px; } }
    `,
  ];

  @property({ attribute: false }) conversationId: string | null = null;
  @state() private msgs: Msg[] = [];
  @state() private models: Array<{ id: string; owned_by: string }> = [];
  @state() private model = "";
  @state() private strategy = "single";
  @state() private stages = "";
  @state() private text = "";
  @state() private streaming = false;
  @state() private error = "";
  private abort?: AbortController;

  async connectedCallback() {
    super.connectedCallback();
    try { this.models = (await api.models()).data; } catch (e) { this.error = String(e); }
    if (this.conversationId) await this.loadTurns();
  }

  updated(ch: Map<string, unknown>) {
    if (ch.has("conversationId") && this.conversationId && ch.get("conversationId") !== undefined) this.loadTurns();
    const log = this.renderRoot.querySelector(".log");
    if (log) log.scrollTop = log.scrollHeight;
  }

  private async loadTurns() {
    if (!this.conversationId) return;
    try {
      const page = await api.turns(this.conversationId);
      this.msgs = page.turns.map((tn: Turn) => ({
        id: tn.id,
        role: tn.kind === "summary" ? "system" : tn.role,
        text: tn.parts.map((p) => p.text ?? "").join(""),
        status: tn.status,
        provider_key: tn.provider_key,
        model_key: tn.model_key,
        skills_used: tn.skills_used,
        trace: (tn.metadata as any)?.trace ?? null,
        stream_id: tn.stream_id,
        usage: tn.usage,
      }));
    } catch (e) { this.error = String(e); }
  }

  private newChat() {
    this.conversationId = null;
    this.msgs = [];
    this.error = "";
    this.dispatchEvent(new CustomEvent("conversation-changed", { detail: { id: null } }));
  }
  /** Public hook used by the shell's "new chat" button. */
  reset() { this.newChat(); }

  private async copy(text: string, btn: HTMLButtonElement) {
    try { await navigator.clipboard.writeText(text); btn.title = t("copied"); setTimeout(() => (btn.title = t("copy")), 1200); } catch { /* clipboard unavailable */ }
  }

  private useChip(text: string) {
    this.text = text;
    this.updateComplete.then(() => this.renderRoot.querySelector<HTMLTextAreaElement>("textarea")?.focus());
  }

  private strategyOptions(): Record<string, unknown> | undefined {
    const list = this.stages.split(",").map((s) => s.trim()).filter(Boolean);
    if (this.strategy === "sequential_handoff") return { stages: list };
    if (this.strategy === "judge_select") return { candidates: list };
    if (this.strategy === "cost_tiered") return { tiers: list };
    return undefined;
  }

  private async send() {
    const message = this.text.trim();
    if (!message || this.streaming) return;
    this.error = "";
    this.text = "";
    this.msgs = [...this.msgs, { id: crypto.randomUUID(), role: "user", text: message }];
    const body: Record<string, unknown> = { message };
    if (this.conversationId) body.conversation_id = this.conversationId;
    if (this.model) body.model = this.model;

    if (this.strategy !== "single") {
      // orchestration is sync (CE-12) — show the trace
      try {
        const r = await api.chat({ ...body, strategy: this.strategy, strategy_options: this.strategyOptions() } as any);
        this.setConversation(r.conversation_id);
        this.msgs = [...this.msgs, this.fromTurn(r.turn, (r as any).trace)];
      } catch (e) { this.error = String(e); }
      return;
    }
    await this.stream(body);
  }

  private fromTurn(tn: Turn, trace?: Record<string, unknown> | null): Msg {
    return {
      id: tn.id, role: tn.role, text: tn.parts.map((p) => p.text ?? "").join(""), status: tn.status,
      provider_key: tn.provider_key, model_key: tn.model_key, skills_used: tn.skills_used, trace: trace ?? null, usage: tn.usage,
    };
  }

  private setConversation(id: string) {
    if (this.conversationId !== id) {
      this.conversationId = id;
      this.dispatchEvent(new CustomEvent("conversation-changed", { detail: { id } }));
    }
  }

  private async stream(body: Record<string, unknown>, resume?: { stream_id: string; seq: number; msgId: string }) {
    this.streaming = true;
    const live: Msg = resume
      ? this.msgs.find((m) => m.id === resume.msgId)!
      : { id: crypto.randomUUID(), role: "assistant", text: "", status: "streaming" };
    if (!resume) this.msgs = [...this.msgs, live];
    const patch = (p: Partial<Msg>) => { Object.assign(live, p); this.msgs = [...this.msgs]; };
    patch({ status: "streaming", resumable: false });
    this.abort = new AbortController();
    try {
      const it = resume ? api.resumeStream(resume.stream_id, resume.seq) : api.chatStream(body as any);
      for await (const ev of it as AsyncGenerator<StreamEvent>) {
        if (this.abort.signal.aborted) break;
        if (ev.type === "message_delta") patch({ text: live.text + String(ev.data.delta ?? ""), stream_id: ev.stream_id });
        else if (ev.type === "completed") patch({ status: "complete", provider_key: ev.data.provider_key as string, model_key: ev.data.model_key as string, usage: ev.data.usage as any, stream_id: ev.stream_id });
        else if (ev.type === "failed") patch({ status: live.text ? "partial" : "failed", resumable: !!ev.data.resumable, delivered_until_seq: Number(ev.data.delivered_until_seq ?? 0), stream_id: ev.stream_id, provider_key: ev.data.provider_key as string });
        else if (ev.type === "cancelled") patch({ status: "partial", stream_id: ev.stream_id, delivered_until_seq: Number(ev.data.delivered_until_seq ?? 0) });
        else if (ev.type === "started") patch({ stream_id: ev.stream_id });
      }
      // conversation id: the server created one if we had none — refresh list of turns
      if (!this.conversationId && !resume) {
        const convs = await api.listConversations({ limit: 1 });
        const first = convs.conversations[0];
        if (first) this.setConversation(String(first.id));
      }
    } catch (e) { this.error = String(e); patch({ status: live.text ? "partial" : "failed" }); }
    finally { this.streaming = false; }
  }

  private async cancel() {
    const live = [...this.msgs].reverse().find((m) => m.status === "streaming");
    if (live?.stream_id) { try { await api.cancelStream(live.stream_id); } catch { /* ignore */ } }
    this.abort?.abort();
    this.streaming = false;
  }

  private async resume(m: Msg) {
    if (!m.stream_id) return;
    await this.stream({}, { stream_id: m.stream_id, seq: m.delivered_until_seq ?? 0, msgId: m.id });
  }

  private renderTrace(trace: any) {
    if (!trace) return nothing;
    return html`<details><summary>${t("trace")}: ${trace.strategy} → ${t("selected")} #${trace.selected_step} — ${trace.decision_reason}</summary>
      <table><thead><tr><th>${t("step")}</th><th>role</th><th>${t("model")}</th><th>${t("status")}</th><th>spec</th><th>${t("reason")}</th></tr></thead>
      <tbody>${(trace.steps as any[]).map((s) => html`<tr><td>${s.step}</td><td>${s.role}</td><td class="mono">${s.model_key ?? s.model}${s.provider_key ? html` <span class="dim">@${s.provider_key}</span>` : ""}</td><td><span class="pill ${s.status === "succeeded" ? "ok" : "bad"}">${s.status}</span></td><td>${s.spec_score ?? ""}</td><td class="dim">${s.reason ?? ""}</td></tr>`)}</tbody></table></details>`;
  }

  render() {
    const modelLabel = this.model || t("auto");
    return html`
      <header>
        <div class="title">
          <h2>${this.conversationId ? html`${t("nav_chat")} <span class="dim mono">${this.conversationId.slice(0, 8)}</span>` : t("new_chat")}</h2>
          <span class="badge"><span class="dot"></span>${modelLabel}${this.strategy !== "single" ? html` · ${t(("strategy_" + { sequential_handoff: "handoff", judge_select: "judge", cost_tiered: "cost" }[this.strategy as string]!) as any)}` : nothing}</span>
        </div>
        <span class="spacer"></span>
        <div class="controls">
          <label>${t("model")}</label>
          <select .value=${this.model} @change=${(e: Event) => (this.model = (e.target as HTMLSelectElement).value)}>
            <option value="">${t("auto")}</option>
            ${this.models.map((m) => html`<option value=${m.id}>${m.id} (${m.owned_by})</option>`)}
          </select>
          <label>${t("strategy")}</label>
          <select .value=${this.strategy} @change=${(e: Event) => (this.strategy = (e.target as HTMLSelectElement).value)}>
            <option value="single">${t("strategy_single")}</option>
            <option value="sequential_handoff">${t("strategy_handoff")}</option>
            <option value="judge_select">${t("strategy_judge")}</option>
            <option value="cost_tiered">${t("strategy_cost")}</option>
          </select>
          ${this.strategy !== "single" ? html`<input placeholder="model-a, model-b" .value=${this.stages} @input=${(e: Event) => (this.stages = (e.target as HTMLInputElement).value)} />` : nothing}
          <button class="ghost icon" @click=${this.newChat} title=${t("new_chat")}>${icons.plus}</button>
        </div>
      </header>

      <div class="log" data-testid="log">
        <div class="inner">
          ${this.msgs.length === 0 ? html`
            <div class="welcome">
              <div class="glow"></div>
              <div class="mark">${logo(84)}</div>
              <h1 class="grad-text">${t("welcome_title")}</h1>
              <p>${t("welcome_sub")}</p>
              <div class="chips">
                ${(["chip_1", "chip_2", "chip_3", "chip_4"] as const).map((k) => html`<button class="chip" @click=${() => this.useChip(t(k))}>${t(k)}</button>`)}
              </div>
            </div>` : nothing}
          ${this.msgs.map((m) => html`
            <div class="msg ${m.role} ${m.status === "streaming" ? "cursor" : ""}" data-role=${m.role} data-status=${m.status ?? ""}>
              <div class="avatar">${m.role === "user" ? icons.user : m.role === "assistant" ? logo(20) : icons.spark}</div>
              <div class="body">
                <div class="head">
                  <span class="sender">${m.role === "user" ? t("you") : m.role === "assistant" ? t("assistant") : t("system_lbl")}</span>
                  ${m.role === "assistant" && m.provider_key ? html`<span class="pill accent">${m.provider_key} / ${m.model_key ?? ""}</span>` : nothing}
                  ${m.text ? html`<div class="actions"><button class="ghost" title=${t("copy")} @click=${(e: Event) => this.copy(m.text, e.currentTarget as HTMLButtonElement)}>${icons.copy}</button></div>` : nothing}
                </div>
                <div class="text">${m.text}${m.text === "" && m.status === "failed" ? html`<span class="err">${t("failed")}</span>` : nothing}</div>
                ${m.role === "assistant" ? html`<div class="meta">
                  ${m.status && m.status !== "complete" && m.status !== "streaming" ? html`<span class="pill ${m.status === "failed" ? "bad" : "warn"}">${t(m.status as any)}</span>` : nothing}
                  ${m.resumable ? html`<button class="primary" @click=${() => this.resume(m)}>${t("resume")}</button>` : nothing}
                  ${(m.skills_used ?? []).map((s) => html`<span class="pill" title=${s.source ?? ""}>${s.skill_id}@${s.version ?? "?"}</span>`)}
                  ${m.usage && (m.usage as any).total_tokens ? html`<span class="muted mono">${(m.usage as any).total_tokens} tok</span>` : nothing}
                </div>${this.renderTrace(m.trace)}` : nothing}
              </div>
            </div>`)}
        </div>
      </div>

      <div class="composer">
        <div class="inner">
          ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
          <div class="bar">
            <textarea rows="1" data-testid="composer" .value=${this.text} placeholder=${t("placeholder")}
              @input=${(e: Event) => { const el = e.target as HTMLTextAreaElement; this.text = el.value; el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px"; }}
              @keydown=${(e: KeyboardEvent) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) this.send(); }}></textarea>
            ${this.streaming
              ? html`<button class="send danger" data-testid="cancel" @click=${this.cancel} title=${t("cancel")}>${icons.stop}</button>`
              : html`<button class="send primary" data-testid="send" @click=${this.send} title=${t("send")} ?disabled=${!this.text.trim()}>${icons.send}</button>`}
          </div>
          <div class="hint">${t("hint_send")}</div>
        </div>
      </div>
    `;
  }
}
