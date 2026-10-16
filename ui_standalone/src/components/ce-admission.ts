import { html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { getJson } from "../api/client";

@customElement("ce-admission")
export class CeAdmission extends CeElement {
  static styles = [CeElement.baseStyles];
  @state() private health: any = null;
  @state() private adm: any = null;
  @state() private error = "";
  private timer?: number;

  connectedCallback() { super.connectedCallback(); this.load(); this.timer = window.setInterval(() => this.load(), 5000); }
  disconnectedCallback() { super.disconnectedCallback(); if (this.timer) clearInterval(this.timer); }
  private async load() {
    try { this.health = await getJson("/healthz"); this.adm = await getJson("/v1/admin/admission"); } catch (e) { this.error = String(e); }
  }
  render() {
    const h = this.health, a = this.adm;
    return html`
      <div class="card">
        <h2>${t("health")}</h2>
        ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
        ${h ? html`<div class="row">
          <span class="pill ok">${h.status}</span><span class="pill">v${h.version}</span>
          <span class="pill mono">${t("upstream")} ${String(h.upstream_sha).slice(0, 7)}</span>
          <span class="pill">${t("backend")}: ${h.admission_backend}</span>
          <span class="pill ${h.replicas_safe ? "ok" : "warn"}">${t("replicas_safe")}: ${h.replicas_safe}</span>
          <span class="pill">providers: ${(h.providers as string[]).join(", ")}</span></div>
          <details><summary>compat.not_served</summary><pre>${JSON.stringify(h.compat, null, 2)}</pre></details>` : t("loading")}
      </div>
      <div class="card" style="margin-top:var(--ce-gap)">
        <h2>${t("admission")}</h2>
        ${a ? html`<table><thead><tr><th>${t("scope")}</th><th>${t("rpm")}</th><th>${t("tpm")}</th><th>${t("concurrency")}</th><th>${t("budget")}</th><th>breaker</th></tr></thead>
          <tbody>${(a.scopes as any[]).map((s) => html`<tr><td class="mono">${s.scope}</td>
            <td>${s.rpm_used} / ${s.limits.rpm ?? "∞"}</td><td>${s.tpm_used} / ${s.limits.tpm ?? "∞"}</td>
            <td>${s.in_flight} / ${s.limits.concurrency ?? "∞"}</td><td>${s.budget_used} / ${s.limits.budget ?? "∞"}</td>
            <td>${s.breaker_open ? html`<span class="pill bad">open</span>` : html`<span class="pill ok">closed</span>`}</td></tr>`)}
          ${(a.scopes as any[]).length === 0 ? html`<tr><td colspan="6" class="dim">${t("empty")}</td></tr>` : nothing}</tbody></table>` : t("loading")}
      </div>`;
  }
}
