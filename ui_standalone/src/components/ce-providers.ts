import { css, html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { getJson, sendJson } from "../api/client";

/** Providers: lifecycle (draft → validate → verify → enable/disable/archive/rollback)
 *  + BYOK: the API key is posted ONCE to /v1/credentials and never kept in the browser. */
@customElement("ce-providers")
export class CeProviders extends CeElement {
  static styles = [CeElement.baseStyles, css`
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .state-draft { color: var(--ce-text-dim); } .state-validated, .state-verified { color: var(--ce-warn); } .state-enabled { color: var(--ce-ok); } .state-disabled, .state-archived { color: var(--ce-danger); }
  `];

  @state() private rows: any[] = [];
  @state() private creds: any[] = [];
  @state() private form = { provider_key: "", display_name: "", base_url: "", models: "", api_key: "", label: "" };
  @state() private error = "";
  @state() private busy = "";
  @state() private revisions: Record<string, any[]> = {};

  connectedCallback() { super.connectedCallback(); this.load(); }

  private async load() {
    try {
      this.rows = (await getJson<any>("/v1/admin/providers/managed")).providers;
      this.creds = (await getJson<any>("/v1/credentials")).credentials ?? [];
    } catch (e) { this.error = String(e); }
  }

  private async create() {
    const f = this.form;
    if (!f.provider_key || !f.base_url) return;
    this.busy = "create"; this.error = "";
    try {
      let credential_ref: string | undefined;
      if (f.api_key) {
        const c = await sendJson<any>("POST", "/v1/credentials", { secret: f.api_key, label: f.label || f.provider_key, provider_hint: f.provider_key });
        credential_ref = c.credential_ref;
      }
      await sendJson("POST", `/v1/admin/providers/${encodeURIComponent(f.provider_key)}`, {
        display_name: f.display_name || f.provider_key,
        base_url: f.base_url,
        static_models: f.models.split(",").map((s) => s.trim()).filter(Boolean),
        credential_ref,
      });
      this.form = { provider_key: "", display_name: "", base_url: "", models: "", api_key: "", label: "" }; // key dropped from memory
      await this.load();
    } catch (e) { this.error = String(e); }
    finally { this.busy = ""; }
  }

  private async act(key: string, action: string, body?: unknown) {
    this.busy = `${key}:${action}`; this.error = "";
    try { await sendJson("POST", `/v1/admin/providers/${encodeURIComponent(key)}:${action}`, body ?? {}); await this.load(); }
    catch (e) { this.error = String(e); }
    finally { this.busy = ""; }
  }

  private async showRevisions(key: string) {
    try { this.revisions = { ...this.revisions, [key]: (await getJson<any>(`/v1/admin/providers/${encodeURIComponent(key)}/revisions`)).revisions ?? [] }; }
    catch (e) { this.error = String(e); }
  }

  render() {
    const f = this.form;
    const set = (k: keyof typeof f) => (e: Event) => (this.form = { ...this.form, [k]: (e.target as HTMLInputElement).value });
    return html`
      <div class="card">
        <h2>${t("add_provider")}</h2>
        ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
        <div class="grid">
          <input placeholder=${t("provider_key")} .value=${f.provider_key} @input=${set("provider_key")} data-testid="pk" />
          <input placeholder=${t("name")} .value=${f.display_name} @input=${set("display_name")} />
          <input placeholder=${t("base_url")} .value=${f.base_url} @input=${set("base_url")} data-testid="base-url" />
          <input placeholder=${t("models_csv")} .value=${f.models} @input=${set("models")} />
          <input type="password" autocomplete="off" placeholder=${t("api_key")} .value=${f.api_key} @input=${set("api_key")} />
          <input placeholder="label" .value=${f.label} @input=${set("label")} />
        </div>
        <div class="row" style="margin-top:8px"><button class="primary" ?disabled=${this.busy === "create"} @click=${this.create}>${t("create")}</button>
          <span class="dim">${this.creds.length} credentials: ${this.creds.map((c) => `${c.label}…${c.last4}`).join(", ")}</span></div>
      </div>
      <div class="card" style="margin-top:var(--ce-gap)">
        <h2>${t("nav_providers")}</h2>
        <table><thead><tr><th>${t("provider_key")}</th><th>${t("state")}</th><th>${t("base_url")}</th><th>${t("model")}</th><th></th></tr></thead>
        <tbody>${this.rows.map((p) => html`<tr data-provider=${p.provider_key}>
          <td>${p.display_name}<div class="dim mono">${p.provider_key} · rev ${p.revision}${p.credential_configured ? " · 🔑" : ""}</div></td>
          <td><span class="pill state-${p.state}">${p.state}</span></td>
          <td class="mono">${p.base_url}</td>
          <td>${p.model_count}<div class="dim mono">${(p.discovered_models ?? []).slice(0, 4).join(", ")}</div></td>
          <td class="row">
            <button @click=${() => this.act(p.provider_key, "validate")}>${t("validate")}</button>
            <button @click=${() => this.act(p.provider_key, "verify")}>${t("verify")}</button>
            ${p.state !== "enabled" ? html`<button class="primary" @click=${() => this.act(p.provider_key, "enable")}>${t("enable")}</button>` : html`<button @click=${() => this.act(p.provider_key, "disable")}>${t("disable")}</button>`}
            <button class="danger" @click=${() => this.act(p.provider_key, "archive")}>${t("archive_p")}</button>
            <button @click=${() => this.showRevisions(p.provider_key)}>${t("revisions")}</button>
          </td></tr>
          ${this.revisions[p.provider_key] ? html`<tr><td colspan="5"><table><tbody>${this.revisions[p.provider_key].map((r: any) => html`<tr><td>#${r.revision}</td><td>${r.state}</td><td class="dim">${r.reason ?? ""} · ${r.changed_by ?? ""}</td><td><button @click=${() => this.act(p.provider_key, "rollback", { revision: r.revision })}>${t("rollback")}</button></td></tr>`)}</tbody></table></td></tr>` : nothing}`)}
          ${this.rows.length === 0 ? html`<tr><td colspan="5" class="dim">${t("empty")}</td></tr>` : nothing}
        </tbody></table>
      </div>`;
  }
}
