import { css, html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { t } from "../i18n";
import { getJson, sendJson } from "../api/client";

const ROW_H = 40;
const PAGE = 500;

/** Virtualized catalog: only visible rows are rendered (fine at 10k+). */
@customElement("ce-catalog")
export class CeCatalog extends CeElement {
  static styles = [CeElement.baseStyles, css`
    .viewport { height: calc(100vh - 220px); overflow: auto; border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm); position: relative; }
    .spacer { position: relative; }
    .r { position: absolute; left: 0; right: 0; height: ${ROW_H}px; display: grid; grid-template-columns: 2fr 2fr 1fr 1fr 1fr; align-items: center; padding: 0 10px; border-bottom: 1px solid var(--ce-border); font-size: .88rem; }
    .r:hover { background: var(--ce-bg-elev-2); }
    .hdr { display: grid; grid-template-columns: 2fr 2fr 1fr 1fr 1fr; padding: 6px 10px; color: var(--ce-text-dim); font-size: .8rem; }
  `];

  @state() private items: any[] = [];
  @state() private total = 0;
  @state() private q = "";
  @state() private scrollTop = 0;
  @state() private aliases: any[] = [];
  @state() private error = "";
  private loading = false;

  connectedCallback() { super.connectedCallback(); this.reload(); }

  private async reload() {
    this.items = []; this.total = 0;
    await this.more();
    try { this.aliases = (await getJson<any>("/v1/catalog/aliases?decision=proposed")).aliases ?? []; } catch { this.aliases = []; }
  }

  private async more() {
    if (this.loading) return;
    if (this.total && this.items.length >= this.total) return;
    this.loading = true;
    try {
      const qs = new URLSearchParams({ offset: String(this.items.length), limit: String(PAGE) });
      if (this.q) qs.set("q", this.q);
      const page = await getJson<any>(`/v1/catalog/models?${qs}`);
      this.items = [...this.items, ...page.items];
      this.total = page.total;
    } catch (e) { this.error = String(e); }
    finally { this.loading = false; }
  }

  private onScroll(e: Event) {
    const el = e.target as HTMLElement;
    this.scrollTop = el.scrollTop;
    if (el.scrollTop + el.clientHeight > el.scrollHeight - ROW_H * 20) this.more();
  }

  private async decide(alias: string, canonical_id: string, decision: "confirmed" | "rejected") {
    try { await sendJson("POST", `/v1/catalog/aliases/${encodeURIComponent(alias)}:decide`, { canonical_id, decision }); await this.reload(); }
    catch (e) { this.error = String(e); }
  }

  render() {
    const vh = Math.max(1, Math.ceil(((this.renderRoot.querySelector(".viewport") as HTMLElement)?.clientHeight ?? 600) / ROW_H) + 6);
    const start = Math.max(0, Math.floor(this.scrollTop / ROW_H) - 3);
    const end = Math.min(this.items.length, start + vh);
    return html`
      <div class="card">
        <div class="row">
          <h2 class="grow">${t("nav_catalog")} <span class="dim">(${this.total} ${t("rows")})</span></h2>
          <input placeholder=${t("search")} .value=${this.q} @input=${(e: Event) => (this.q = (e.target as HTMLInputElement).value)} @keydown=${(e: KeyboardEvent) => e.key === "Enter" && this.reload()} />
        </div>
        ${this.error ? html`<div class="err">${this.error}</div>` : nothing}
        <div class="hdr"><span>${t("canonical")}</span><span>${t("name")}</span><span>${t("providers_col")}</span><span>${t("eligible")}</span><span>${t("proposed")}</span></div>
        <div class="viewport" data-testid="catalog-viewport" @scroll=${this.onScroll}>
          <div class="spacer" style="height:${this.items.length * ROW_H}px">
            ${this.items.slice(start, end).map((it, i) => html`<div class="r" style="top:${(start + i) * ROW_H}px" data-row=${start + i}>
              <span class="mono">${it.canonical.canonical_id}</span>
              <span>${it.canonical.display_name}</span>
              <span>${it.configured_provider_count} <span class="dim">(${(it.providers ?? []).map((p: any) => p.provider_key).join(", ")})</span></span>
              <span>${it.eligible_provider_count}</span>
              <span>${it.proposed_alias_count || ""}</span>
            </div>`)}
          </div>
        </div>
        ${this.aliases.length ? html`<details open><summary>${t("aliases")} — ${t("proposed")}: ${this.aliases.length}</summary>
          <table><tbody>${this.aliases.slice(0, 50).map((a) => html`<tr><td class="mono">${a.alias}</td><td class="mono">→ ${a.canonical_id}</td>
            <td class="row"><button class="primary" @click=${() => this.decide(a.alias, a.canonical_id, "confirmed")}>${t("confirm")}</button><button @click=${() => this.decide(a.alias, a.canonical_id, "rejected")}>${t("reject")}</button></td></tr>`)}</tbody></table></details>` : nothing}
      </div>`;
  }
}
