import { html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { getJson, sendJson } from "../api/client";

/** Capability ledger: executable = declared ∧ verified ∧ enabled ∧ available_now. */
@customElement("ce-capabilities")
export class CeCapabilities extends CeElement {
  static styles = [CeElement.baseStyles];
  @state() private entries: any[] = [];
  @state() private filter = "";
  @state() private error = "";

  connectedCallback() { super.connectedCallback(); this.load(); }
  private async load() {
    try { this.entries = (await getJson<any>("/v1/admin/capabilities/ledger")).entries; } catch (e) { this.error = String(e); }
  }
  private async act(e: any, action: "probe" | "enable") {
    try { await sendJson("POST", `/v1/admin/capabilities/${e.provider_key}/${e.capability}:${action}`, action === "enable" ? { enabled: !e.enabled.by_admin } : {}); await this.load(); }
    catch (err) { this.error = String(err); }
  }
  private pill(ok: boolean, label: string) { return html`<span class="pill ${ok ? "ok" : "bad"}">${label}</span>`; }

  render() {
    const rows = this.entries.filter((e) => !this.filter || `${e.provider_key} ${e.capability}`.includes(this.filter));
    return html`<div class="card">
      <div class="row"><h2 class="grow">${t("ledger")} <span class="dim">(${rows.length})</span></h2>
        <input placeholder=${t("search")} .value=${this.filter} @input=${(ev: Event) => (this.filter = (ev.target as HTMLInputElement).value)} /></div>
      ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
      <table><thead><tr><th>${t("provider_key")}</th><th>${t("capability")}</th><th>${t("declared")}</th><th>${t("verified")}</th><th>${t("enabled_c")}</th><th>${t("available")}</th><th></th></tr></thead>
      <tbody>${rows.map((e) => html`<tr>
        <td class="mono">${e.provider_key}</td><td class="mono">${e.capability}</td>
        <td>${this.pill(e.declared.value, e.declared.source)}</td>
        <td>${this.pill(e.verified.status === "verified", e.verified.status)}${e.verified.detail ? html`<div class="dim">${e.verified.detail}</div>` : nothing}</td>
        <td>${this.pill(e.enabled.by_admin || e.enabled.auto, e.enabled.by_admin ? "admin" : e.enabled.auto ? "auto" : "off")}</td>
        <td>${this.pill(e.available_now, e.available_now ? "yes" : "no")}</td>
        <td class="row"><button @click=${() => this.act(e, "probe")}>${t("probe")}</button><button @click=${() => this.act(e, "enable")}>${e.enabled.by_admin ? t("disable") : t("enable")}</button></td>
      </tr>`)}
      ${rows.length === 0 ? html`<tr><td colspan="7" class="dim">${t("empty")}</td></tr>` : nothing}</tbody></table></div>`;
  }
}
