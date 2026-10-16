import { css, html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { api, getJson, sendJson } from "../api/client";

/** CE-19: select skills, assign to agent profiles / projects, enable/disable, see versions. */
@customElement("ce-skills")
export class CeSkills extends CeElement {
  static styles = [CeElement.baseStyles, css`
    :host { display: grid; grid-template-columns: 1fr 1fr; gap: var(--ce-gap); }
    @media (max-width: 900px) { :host { grid-template-columns: 1fr; } }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  `];

  @state() private skills: any[] = [];
  @state() private detail: any = null;
  @state() private profiles: any[] = [];
  @state() private projects: any[] = [];
  @state() private selectedProject = "";
  @state() private projectSkills: any = null;
  @state() private form = { id: "", name: "", version: "1.0.0", instructions: "" };
  @state() private profName = "";
  @state() private profSkills: string[] = [];
  @state() private error = "";

  connectedCallback() { super.connectedCallback(); this.load(); }

  private async load() {
    try {
      this.skills = (await getJson<any>("/v1/skills")).skills;
      this.profiles = (await getJson<any>("/v1/agent-profiles")).profiles;
      this.projects = (await api.listProjects()).projects;
      if (this.selectedProject) this.projectSkills = await getJson(`/v1/projects/${this.selectedProject}/skills`);
    } catch (e) { this.error = String(e); }
  }

  private async register() {
    const f = this.form;
    if (!f.id) return;
    try {
      await sendJson("POST", "/v1/admin/skills", { id: f.id, name: f.name || f.id, version: f.version, instructions: f.instructions || undefined });
      this.form = { id: "", name: "", version: "1.0.0", instructions: "" };
      await this.load();
    } catch (e) { this.error = String(e); }
  }

  private async show(id: string) {
    try { this.detail = await getJson(`/v1/skills/${id}`); } catch (e) { this.error = String(e); }
  }

  private async toggleStatus(id: string, enable: boolean) {
    try { await sendJson("POST", `/v1/admin/skills/${id}:${enable ? "enable" : "disable"}`); await this.load(); if (this.detail?.id === id) await this.show(id); }
    catch (e) { this.error = String(e); }
  }

  private async createProfile() {
    if (!this.profName) return;
    try {
      await sendJson("POST", "/v1/agent-profiles", { name: this.profName, skills: this.profSkills.map((skill_id) => ({ skill_id })) });
      this.profName = ""; this.profSkills = []; await this.load();
    } catch (e) { this.error = String(e); }
  }

  private async projectAction(skillId: string, action: "assign" | "enable" | "disable" | "remove") {
    if (!this.selectedProject) return;
    try {
      const base = `/v1/projects/${this.selectedProject}/skills`;
      if (action === "assign") await sendJson("POST", `${base}:assign`, { skill_id: skillId });
      else if (action === "remove") await sendJson("DELETE", `${base}/${skillId}`);
      else await sendJson("POST", `${base}/${skillId}:${action}`);
      await this.load();
    } catch (e) { this.error = String(e); }
  }

  private async bindProfile(projectId: string, profileId: string) {
    try { await sendJson("PATCH", `/v1/projects/${projectId}`, { agent_profile_id: profileId || null }); await this.load(); }
    catch (e) { this.error = String(e); }
  }

  render() {
    const f = this.form;
    const set = (k: keyof typeof f) => (e: Event) => (this.form = { ...this.form, [k]: (e.target as HTMLInputElement).value });
    const ps = this.projectSkills;
    const assigned: any[] = ps?.assigned ?? [];
    return html`
      <section>
        <div class="card">
          <h2>${t("skills")} <span class="dim">(${this.skills.length})</span></h2>
          ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
          <table><thead><tr><th>${t("skill_id")}</th><th>${t("version")}</th><th>${t("status")}</th><th>tools</th><th></th></tr></thead>
          <tbody>${this.skills.map((s) => html`<tr data-skill=${s.id}>
            <td><a href="#skills" @click=${(e: Event) => { e.preventDefault(); this.show(s.id); }}>${s.id}</a></td><td class="mono">${s.version}</td>
            <td><span class="pill ok">${s.status}</span></td><td class="dim">${(s.requires_tools ?? []).join(", ")}</td>
            <td class="row"><button @click=${() => this.toggleStatus(s.id, false)}>${t("disable")}</button>
              ${this.selectedProject ? html`<button class="primary" @click=${() => this.projectAction(s.id, "assign")}>${t("assign")}</button>` : nothing}</td></tr>`)}
            ${this.skills.length === 0 ? html`<tr><td colspan="5" class="dim">${t("empty")}</td></tr>` : nothing}
          </tbody></table>
          ${this.detail ? html`<details open><summary>${this.detail.name} @${this.detail.version} — ${this.detail.type} · ${this.detail.status} ${this.detail.selectable ? "" : "(not selectable)"}</summary>
            <div class="dim">versions: ${this.detail.versions.join(", ")} · registry: <span class="mono">${this.detail.registry_id}</span></div>
            ${this.detail.instructions ? html`<pre>${this.detail.instructions}</pre>` : nothing}
            ${!this.detail.selectable ? html`<button class="primary" @click=${() => this.toggleStatus(this.detail.id, true)}>${t("enable")}</button>` : nothing}</details>` : nothing}
        </div>
        <div class="card" style="margin-top:var(--ce-gap)">
          <h2>${t("register_skill")}</h2>
          <div class="grid">
            <input placeholder=${t("skill_id")} .value=${f.id} @input=${set("id")} data-testid="skill-id" />
            <input placeholder=${t("name")} .value=${f.name} @input=${set("name")} />
            <input placeholder=${t("version")} .value=${f.version} @input=${set("version")} />
          </div>
          <textarea placeholder=${t("instructions")} .value=${f.instructions} @input=${set("instructions")}></textarea>
          <button class="primary" @click=${this.register} data-testid="register-skill">${t("create")}</button>
        </div>
      </section>
      <section>
        <div class="card">
          <h2>${t("agent_profiles")}</h2>
          <div class="row"><input class="grow" placeholder=${t("name")} .value=${this.profName} @input=${(e: Event) => (this.profName = (e.target as HTMLInputElement).value)} />
            <select multiple @change=${(e: Event) => (this.profSkills = [...(e.target as HTMLSelectElement).selectedOptions].map((o) => o.value))}>
              ${this.skills.map((s) => html`<option value=${s.id}>${s.id}@${s.version}</option>`)}</select>
            <button class="primary" @click=${this.createProfile}>${t("create")}</button></div>
          <table><tbody>${this.profiles.map((p) => html`<tr><td>${p.name}<div class="dim mono">${String(p.id).slice(0, 8)}</div></td>
            <td>${p.skills.map((s: any) => html`<span class="pill ${s.enabled ? "" : "bad"}">${s.skill_id}${s.version ? "@" + s.version : ""}</span> `)}</td>
            <td><button class="danger" @click=${async () => { await sendJson("DELETE", `/v1/agent-profiles/${p.id}`); this.load(); }}>${t("delete")}</button></td></tr>`)}
            ${this.profiles.length === 0 ? html`<tr><td class="dim">${t("empty")}</td></tr>` : nothing}</tbody></table>
        </div>
        <div class="card" style="margin-top:var(--ce-gap)">
          <h2>${t("projects")} → ${t("skills")}</h2>
          <div class="row">
            <select .value=${this.selectedProject} @change=${(e: Event) => { this.selectedProject = (e.target as HTMLSelectElement).value; this.load(); }}>
              <option value="">—</option>${this.projects.map((p) => html`<option value=${p.id}>${p.name}</option>`)}</select>
            ${this.selectedProject ? html`<label class="dim">${t("agent_profiles")}</label>
              <select @change=${(e: Event) => this.bindProfile(this.selectedProject, (e.target as HTMLSelectElement).value)}>
                <option value="">—</option>${this.profiles.map((p) => html`<option value=${p.id} ?selected=${this.projects.find((x) => x.id === this.selectedProject)?.agent_profile_id === p.id}>${p.name}</option>`)}</select>` : nothing}
          </div>
          ${ps ? html`
            <h2 style="margin-top:8px">${t("assign")}</h2>
            <table><tbody>${assigned.map((a) => html`<tr><td class="mono">${a.skill_id}${a.version ? "@" + a.version : ""}</td>
              <td><span class="pill ${a.enabled ? "ok" : "bad"}">${a.enabled ? t("enable") : t("disable")}</span></td>
              <td class="row"><button @click=${() => this.projectAction(a.skill_id, a.enabled ? "disable" : "enable")}>${a.enabled ? t("disable") : t("enable")}</button>
                <button class="danger" @click=${() => this.projectAction(a.skill_id, "remove")}>${t("delete")}</button></td></tr>`)}</tbody></table>
            <h2 style="margin-top:8px">${t("effective")}</h2>
            <div class="row">${(ps.effective as any[]).map((e) => html`<span class="pill ok" title=${e.source}>${e.skill_id}@${e.version} <span class="dim">${e.source}</span></span>`)}
              ${(ps.effective as any[]).length === 0 ? html`<span class="dim">${t("empty")}</span>` : nothing}</div>` : nothing}
        </div>
      </section>`;
  }
}
