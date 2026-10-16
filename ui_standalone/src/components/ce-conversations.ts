import { css, html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { api, sendJson } from "../api/client";

@customElement("ce-conversations")
export class CeConversations extends CeElement {
  static styles = [CeElement.baseStyles, css`
    :host { display: grid; grid-template-columns: 1fr 1fr; gap: var(--ce-gap); }
    @media (max-width: 900px) { :host { grid-template-columns: 1fr; } }
    tr.click { cursor: pointer; } tr.click:hover td { background: var(--ce-accent-soft); }
  `];

  @state() private projects: any[] = [];
  @state() private conversations: any[] = [];
  @state() private pName = "";
  @state() private pSys = "";
  @state() private filterProject = "";
  @state() private error = "";

  connectedCallback() { super.connectedCallback(); this.load(); }

  private async load() {
    try {
      this.projects = (await api.listProjects()).projects;
      const q: Record<string, unknown> = { limit: 200 };
      if (this.filterProject) q.project_id = this.filterProject;
      this.conversations = (await api.listConversations(q)).conversations;
    } catch (e) { this.error = String(e); }
  }

  private async createProject() {
    if (!this.pName.trim()) return;
    try {
      await api.createProject({ name: this.pName.trim(), system_instructions: this.pSys || undefined });
      this.pName = ""; this.pSys = ""; await this.load();
    } catch (e) { this.error = String(e); }
  }

  private async del(kind: "projects" | "conversations", id: string) {
    try { await sendJson("DELETE", `/v1/${kind}/${id}`); await this.load(); } catch (e) { this.error = String(e); }
  }

  private async archive(id: string) {
    try { await sendJson("PATCH", `/v1/conversations/${id}`, { archived: true }); await this.load(); } catch (e) { this.error = String(e); }
  }

  private async newConversation(projectId?: string) {
    try {
      const c = await api.createConversation(projectId ? { project_id: projectId } : {});
      this.dispatchEvent(new CustomEvent("open-conversation", { detail: { id: c.id }, bubbles: true, composed: true }));
    } catch (e) { this.error = String(e); }
  }

  render() {
    return html`
      <section class="card">
        <h2>${t("projects")}</h2>
        ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
        <div class="row">
          <input class="grow" placeholder=${t("name")} .value=${this.pName} @input=${(e: Event) => (this.pName = (e.target as HTMLInputElement).value)} />
          <button class="primary" @click=${this.createProject}>${t("create")}</button>
        </div>
        <textarea placeholder=${t("system_instructions")} .value=${this.pSys} @input=${(e: Event) => (this.pSys = (e.target as HTMLTextAreaElement).value)}></textarea>
        <table><thead><tr><th>${t("name")}</th><th>${t("skills")}</th><th></th></tr></thead>
          <tbody>${this.projects.map((p) => html`<tr>
            <td>${p.name}<div class="dim mono">${String(p.id).slice(0, 8)}</div></td>
            <td>${(p.skills ?? []).map((s: any) => html`<span class="pill ${s.enabled === false ? "bad" : ""}">${s.skill_id}</span> `)}</td>
            <td class="row"><button @click=${() => this.newConversation(p.id)}>${t("new_chat")}</button>
                <button @click=${() => { this.filterProject = p.id; this.load(); }}>${t("conversations")}</button>
                <button class="danger" @click=${() => this.del("projects", p.id)}>${t("delete")}</button></td></tr>`)}
            ${this.projects.length === 0 ? html`<tr><td colspan="3" class="dim">${t("empty")}</td></tr>` : nothing}
          </tbody></table>
      </section>
      <section class="card">
        <div class="row"><h2 class="grow">${t("conversations")}</h2>
          ${this.filterProject ? html`<button @click=${() => { this.filterProject = ""; this.load(); }}>×</button>` : nothing}
          <button class="primary" @click=${() => this.newConversation()}>${t("new_chat")}</button></div>
        <table><thead><tr><th>${t("name")}</th><th>${t("turns")}</th><th>${t("model")}</th><th></th></tr></thead>
          <tbody>${this.conversations.map((c) => html`<tr class="click" @click=${() => this.dispatchEvent(new CustomEvent("open-conversation", { detail: { id: c.id }, bubbles: true, composed: true }))}>
            <td>${c.title}<div class="dim mono">${String(c.id).slice(0, 8)}</div></td><td>${c.turn_count}</td><td class="mono">${c.model ?? t("auto")}</td>
            <td class="row" @click=${(e: Event) => e.stopPropagation()}><button @click=${() => this.archive(c.id)}>${t("archive")}</button><button class="danger" @click=${() => this.del("conversations", c.id)}>${t("delete")}</button></td></tr>`)}
            ${this.conversations.length === 0 ? html`<tr><td colspan="4" class="dim">${t("empty")}</td></tr>` : nothing}
          </tbody></table>
      </section>
    `;
  }
}
