import { css, html } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { lang, setLang, t } from "../i18n";
import "./ce-chat";
import "./ce-conversations";
import "./ce-catalog";
import "./ce-providers";
import "./ce-capabilities";
import "./ce-admission";
import "./ce-skills";

type Screen = "chat" | "conversations" | "catalog" | "providers" | "capabilities" | "admission" | "skills";
const SCREENS: Screen[] = ["chat", "conversations", "catalog", "providers", "capabilities", "admission", "skills"];

@customElement("ce-app")
export class CeApp extends CeElement {
  static styles = [
    CeElement.baseStyles,
    css`
      :host { display: grid; grid-template-columns: 220px 1fr; height: 100vh; }
      nav { background: var(--ce-bg-elev); border-inline-end: 1px solid var(--ce-border); padding: var(--ce-gap); display: flex; flex-direction: column; gap: 4px; }
      nav .brand { font-weight: 700; letter-spacing: .02em; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
      nav a { padding: 8px 10px; border-radius: var(--ce-radius-sm); color: var(--ce-text-dim); text-decoration: none; cursor: pointer; }
      nav a.active { background: var(--ce-bg-elev-2); color: var(--ce-text); }
      main { overflow: auto; padding: var(--ce-gap); }
      .foot { margin-top: auto; font-size: .75rem; color: var(--ce-text-dim); }
      @media (max-width: 760px) { :host { grid-template-columns: 1fr; grid-template-rows: auto 1fr; } nav { flex-direction: row; flex-wrap: wrap; } .foot { display: none; } }
    `,
  ];

  @state() private screen: Screen = (location.hash.replace("#", "") as Screen) || "chat";
  @state() private conversationId: string | null = null;
  @state() private health: Record<string, unknown> | null = null;

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("hashchange", () => {
      const s = location.hash.replace("#", "") as Screen;
      if (SCREENS.includes(s)) this.screen = s;
    });
    fetch("/healthz").then((r) => r.json()).then((h) => (this.health = h)).catch(() => (this.health = null));
  }

  private go(s: Screen) { location.hash = s; this.screen = s; }

  private openConversation(e: CustomEvent<{ id: string }>) {
    this.conversationId = e.detail.id;
    this.go("chat");
  }

  render() {
    const h = this.health as any;
    return html`
      <nav>
        <div class="brand">
          <span>${t("app_title")}</span>
          <button @click=${() => setLang(lang() === "ar" ? "en" : "ar")} title="language">${t("lang")}</button>
        </div>
        ${SCREENS.map((s) => html`<a class=${this.screen === s ? "active" : ""} @click=${() => this.go(s)} data-nav=${s}>${t(("nav_" + s) as any)}</a>`)}
        <div class="foot">
          ${h ? html`<div>${t("upstream")}: <span class="mono">${String(h.upstream_sha).slice(0, 7)}</span></div>
                  <div>${t("backend")}: ${h.admission_backend}</div>
                  <div>${(h.providers as string[]).length} providers</div>` : html`<div>${t("loading")}</div>`}
        </div>
      </nav>
      <main>
        ${this.screen === "chat" ? html`<ce-chat .conversationId=${this.conversationId} @conversation-changed=${(e: CustomEvent) => (this.conversationId = e.detail.id)}></ce-chat>` : ""}
        ${this.screen === "conversations" ? html`<ce-conversations @open-conversation=${this.openConversation}></ce-conversations>` : ""}
        ${this.screen === "catalog" ? html`<ce-catalog></ce-catalog>` : ""}
        ${this.screen === "providers" ? html`<ce-providers></ce-providers>` : ""}
        ${this.screen === "capabilities" ? html`<ce-capabilities></ce-capabilities>` : ""}
        ${this.screen === "admission" ? html`<ce-admission></ce-admission>` : ""}
        ${this.screen === "skills" ? html`<ce-skills></ce-skills>` : ""}
      </main>
    `;
  }
}
