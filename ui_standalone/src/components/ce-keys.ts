import { html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";

/** CE-24/D — virtual keys admin. The master key is kept in sessionStorage only (never
 *  persisted); every call sends it as `Authorization: Bearer`. When CE_AUTH_MODE=off the
 *  server has no /v1/admin/keys and the screen explains how to enable it. */
const STORE = "ce.master_key";

async function call<T = any>(method: string, path: string, key: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: { "content-type": "application/json", ...(key ? { authorization: `Bearer ${key}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  if (!r.ok) {
    let msg = text;
    try { msg = JSON.parse(text).error?.message ?? text; } catch { /* raw */ }
    throw new Error(`${r.status}: ${msg}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

@customElement("ce-keys")
export class CeKeys extends CeElement {
  static styles = [CeElement.baseStyles];
  @state() private authMode = "";
  @state() private master = sessionStorage.getItem(STORE) ?? "";
  @state() private keys: any[] = [];
  @state() private error = "";
  @state() private created: { key: string; record: any } | null = null;
  @state() private form = { name: "", models_allowed: "", rpm: "", rpd: "", budget_usd: "" };

  connectedCallback() { super.connectedCallback(); this.load(); }

  private async load() {
    this.error = "";
    try {
      const h = await (await fetch("/healthz")).json();
      this.authMode = String(h.auth_mode ?? "off");
      if (this.authMode === "off" || !this.master) { this.keys = []; return; }
      const res = await call<{ keys: any[] }>("GET", "/v1/admin/keys", this.master);
      this.keys = res.keys;
    } catch (e) { this.error = String(e); this.keys = []; }
  }
  private setMaster(v: string) { this.master = v.trim(); sessionStorage.setItem(STORE, this.master); this.load(); }
  private async create() {
    this.error = ""; this.created = null;
    const f = this.form;
    const body: any = { name: f.name.trim() || "key" };
    if (f.models_allowed.trim()) body.models_allowed = f.models_allowed.split(",").map((s) => s.trim()).filter(Boolean);
    if (f.rpm) body.rpm = Number(f.rpm);
    if (f.rpd) body.rpd = Number(f.rpd);
    if (f.budget_usd) body.budget_usd = Number(f.budget_usd);
    try {
      this.created = await call("POST", "/v1/admin/keys", this.master, body);
      this.form = { name: "", models_allowed: "", rpm: "", rpd: "", budget_usd: "" };
      await this.load();
    } catch (e) { this.error = String(e); }
  }
  private async toggle(k: any) {
    try { await call("PATCH", `/v1/admin/keys/${k.key_id}`, this.master, { disabled: !k.disabled }); await this.load(); } catch (e) { this.error = String(e); }
  }
  private async removeKey(k: any) {
    if (!confirm(`${t("delete")} ${k.name}?`)) return;
    try { await call("DELETE", `/v1/admin/keys/${k.key_id}`, this.master); await this.load(); } catch (e) { this.error = String(e); }
  }

  render() {
    return html`
      <div class="card">
        <h2>${t("nav_keys")}</h2>
        <div class="row"><span class="pill ${this.authMode === "off" ? "warn" : "ok"}">CE_AUTH_MODE: ${this.authMode || "…"}</span></div>
        ${this.authMode === "off" ? html`<p class="dim">${t("keys_off_hint")}</p>` : html`
          <label>${t("master_key")}
            <input class="mono" type="password" .value=${this.master} data-role="master" placeholder="ce_live_…"
              @change=${(e: Event) => this.setMaster((e.target as HTMLInputElement).value)} /></label>
          <p class="dim">${t("keys_master_hint")}</p>`}
        ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
      </div>
      ${this.authMode !== "off" && this.master ? html`
      <div class="card" style="margin-top:var(--ce-gap)">
        <h2>${t("create")}</h2>
        <div class="row">
          <input placeholder=${t("name")} .value=${this.form.name} data-role="key-name" @input=${(e: Event) => (this.form = { ...this.form, name: (e.target as HTMLInputElement).value })} />
          <input placeholder="models_allowed (a, b)" .value=${this.form.models_allowed} @input=${(e: Event) => (this.form = { ...this.form, models_allowed: (e.target as HTMLInputElement).value })} />
          <input placeholder="rpm" type="number" style="width:6em" .value=${this.form.rpm} @input=${(e: Event) => (this.form = { ...this.form, rpm: (e.target as HTMLInputElement).value })} />
          <input placeholder="rpd" type="number" style="width:6em" .value=${this.form.rpd} @input=${(e: Event) => (this.form = { ...this.form, rpd: (e.target as HTMLInputElement).value })} />
          <input placeholder="budget $" type="number" step="0.01" style="width:7em" .value=${this.form.budget_usd} @input=${(e: Event) => (this.form = { ...this.form, budget_usd: (e.target as HTMLInputElement).value })} />
          <button class="primary" data-role="key-create" @click=${this.create}>${t("create")}</button>
        </div>
        ${this.created ? html`<div class="row" style="margin-top:8px"><span class="pill warn">${t("key_shown_once")}</span>
          <code class="mono" data-role="created-key">${this.created.key}</code></div>` : nothing}
      </div>
      <div class="card" style="margin-top:var(--ce-gap)">
        <h2>${t("keys")} (${this.keys.length})</h2>
        <table><thead><tr><th>${t("name")}</th><th>prefix</th><th>models</th><th>rpm/rpd</th><th>${t("budget")}</th><th>${t("spend")}</th><th></th></tr></thead>
        <tbody>${this.keys.map((k) => html`<tr data-key-row=${k.key_id}>
          <td>${k.name} ${k.is_master ? html`<span class="pill">master</span>` : nothing} ${k.disabled ? html`<span class="pill bad">disabled</span>` : nothing}</td>
          <td class="mono">${k.prefix}</td>
          <td>${(k.models_allowed ?? []).join(", ") || "∞"}</td>
          <td>${k.rpm ?? "∞"} / ${k.rpd ?? "∞"}</td>
          <td>${k.budget_usd ?? "∞"}</td>
          <td>${k.spend?.requests ?? 0} req · ${k.spend?.tokens_total ?? 0} tok · $${Number(k.spend?.cost_usd ?? 0).toFixed(4)}</td>
          <td>${k.is_master ? nothing : html`<button @click=${() => this.toggle(k)}>${k.disabled ? t("enable") : t("disable")}</button>
            <button class="danger" @click=${() => this.removeKey(k)}>${t("delete")}</button>`}</td></tr>`)}
          ${this.keys.length === 0 ? html`<tr><td colspan="7" class="dim">${t("empty")}</td></tr>` : nothing}</tbody></table>
      </div>` : nothing}`;
  }
}
