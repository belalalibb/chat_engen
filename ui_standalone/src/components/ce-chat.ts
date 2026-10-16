import { css, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
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
      :host { display: grid; grid-template-rows: auto 1fr auto; height: calc(100vh - 2 * var(--ce-gap)); gap: var(--ce-gap); }
      .log { overflow: auto; display: flex; flex-direction: column; gap: 10px; padding: 4px; }
      .msg { max-width: 78ch; padding: 10px 14px; border-radius: var(--ce-radius); border: 1px solid var(--ce-border); white-space: pre-wrap; line-height: 1.5; }
      .msg.user { align-self: flex-end; background: var(--ce-bg-elev-2); }
      .msg.assistant { align-self: flex-start; background: var(--ce-bg-elev); }
      .msg.system { align-self: center; color: var(--ce-text-dim); font-size: .85rem; border-style: dashed; }
      .meta { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
      .composer textarea { width: 100%; }
      .cursor::after { content: "▍"; animation: blink 1s steps(1) infinite; }
      @keyframes blink { 50% { opacity: 0; } }
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
    this.dispatchEvent(new CustomEvent("conversation-changed", { detail: { id: null } }));
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
    return html`
      <div class="row card">
        <button @click=${this.newChat}>${t("new_chat")}</button>
        <label class="dim">${t("model")}</label>
        <select .value=${this.model} @change=${(e: Event) => (this.model = (e.target as HTMLSelectElement).value)}>
          <option value="">${t("auto")}</option>
          ${this.models.map((m) => html`<option value=${m.id}>${m.id} <span class="dim">(${m.owned_by})</span></option>`)}
        </select>
        <label class="dim">${t("strategy")}</label>
        <select .value=${this.strategy} @change=${(e: Event) => (this.strategy = (e.target as HTMLSelectElement).value)}>
          <option value="single">${t("strategy_single")}</option>
          <option value="sequential_handoff">${t("strategy_handoff")}</option>
          <option value="judge_select">${t("strategy_judge")}</option>
          <option value="cost_tiered">${t("strategy_cost")}</option>
        </select>
        ${this.strategy !== "single" ? html`<input class="grow" placeholder="model-a, model-b" .value=${this.stages} @input=${(e: Event) => (this.stages = (e.target as HTMLInputElement).value)} />` : nothing}
        ${this.conversationId ? html`<span class="dim mono">${this.conversationId.slice(0, 8)}</span>` : nothing}
      </div>
      <div class="log" data-testid="log">
        ${this.msgs.length === 0 ? html`<div class="dim">${t("empty")}</div>` : nothing}
        ${this.msgs.map((m) => html`
          <div class="msg ${m.role} ${m.status === "streaming" ? "cursor" : ""}" data-role=${m.role} data-status=${m.status ?? ""}>${m.text}${m.text === "" && m.status === "failed" ? html`<span class="err">${t("failed")}</span>` : nothing}
            ${m.role === "assistant" ? html`<div class="meta">
              ${m.provider_key ? html`<span class="pill">${m.provider_key} / ${m.model_key ?? ""}</span>` : nothing}
              ${m.status && m.status !== "complete" && m.status !== "streaming" ? html`<span class="pill ${m.status === "failed" ? "bad" : "warn"}">${t(m.status as any)}</span>` : nothing}
              ${m.resumable ? html`<button class="primary" @click=${() => this.resume(m)}>${t("resume")}</button>` : nothing}
              ${(m.skills_used ?? []).map((s) => html`<span class="pill" title=${s.source ?? ""}>${s.skill_id}@${s.version ?? "?"}</span>`)}
            </div>${this.renderTrace(m.trace)}` : nothing}
          </div>`)}
      </div>
      <div class="composer card">
        ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
        <div class="row">
          <textarea class="grow" data-testid="composer" .value=${this.text} placeholder=${t("placeholder")}
            @input=${(e: Event) => (this.text = (e.target as HTMLTextAreaElement).value)}
            @keydown=${(e: KeyboardEvent) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) this.send(); }}></textarea>
          ${this.streaming
            ? html`<button class="danger" data-testid="cancel" @click=${this.cancel}>${t("cancel")}</button>`
            : html`<button class="primary" data-testid="send" @click=${this.send}>${t("send")}</button>`}
        </div>
      </div>
    `;
  }
}
