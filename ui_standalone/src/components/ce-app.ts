import { css, html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { CeElement } from "./base";
import { getTheme, lang, setLang, setTheme, t } from "../i18n";
import { icons, logo } from "./icons";
import "./ce-chat";
import "./ce-conversations";
import "./ce-catalog";
import "./ce-providers";
import "./ce-capabilities";
import "./ce-admission";
import "./ce-skills";

type Screen = "chat" | "conversations" | "catalog" | "providers" | "capabilities" | "admission" | "skills";
const SCREENS: Screen[] = ["chat", "conversations", "catalog", "providers", "capabilities", "admission", "skills"];
const WORKSPACE: Screen[] = ["chat", "conversations"];
const ADMIN: Screen[] = ["catalog", "providers", "capabilities", "admission", "skills"];

@customElement("ce-app")
export class CeApp extends CeElement {
  static styles = [
    CeElement.baseStyles,
    css`
      :host { display: grid; grid-template-columns: var(--ce-sidebar) 1fr; height: 100vh; transition: grid-template-columns var(--ce-dur) var(--ce-ease); }
      :host([collapsed]) { grid-template-columns: var(--ce-sidebar-min) 1fr; }

      nav {
        display: flex; flex-direction: column; gap: 6px; padding: 14px 12px; overflow: hidden;
        background: var(--ce-glass); border-inline-end: 1px solid var(--ce-border);
        backdrop-filter: var(--ce-blur); -webkit-backdrop-filter: var(--ce-blur);
      }
      .brand { display: flex; align-items: center; gap: 10px; padding: 4px 6px 12px; min-height: 48px; }
      .brand .name { font-weight: 800; font-size: var(--ce-fs-lg); letter-spacing: .01em; white-space: nowrap; }
      .brand .spacer { flex: 1; }
      .tools { display: flex; gap: 4px; }
      :host([collapsed]) .brand { flex-direction: column; padding-bottom: 8px; }
      :host([collapsed]) .brand .name, :host([collapsed]) .label, :host([collapsed]) .foot .txt, :host([collapsed]) .group-title { display: none; }
      :host([collapsed]) .tools { flex-direction: column; }

      .new-chat {
        width: 100%; justify-content: center; padding: 10px 12px; border-radius: var(--ce-radius); margin-bottom: 6px;
        background: var(--ce-gradient); color: var(--ce-accent-ink); border: none; font-weight: 600; box-shadow: 0 4px 14px var(--ce-accent-glow);
      }
      .new-chat:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
      .new-chat svg { width: 18px; height: 18px; }
      :host([collapsed]) .new-chat { padding: 10px 0; }

      .group-title { font-size: var(--ce-fs-xs); color: var(--ce-text-muted); text-transform: uppercase; letter-spacing: .08em; padding: 10px 10px 4px; }
      a.item {
        display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: var(--ce-radius-sm);
        color: var(--ce-text-dim); text-decoration: none; cursor: pointer; white-space: nowrap; font-size: var(--ce-fs-sm); font-weight: 500;
        transition: background var(--ce-dur) var(--ce-ease), color var(--ce-dur) var(--ce-ease);
        position: relative;
      }
      a.item svg { width: 19px; height: 19px; flex: none; }
      a.item:hover { background: var(--ce-bg-hover); color: var(--ce-text); }
      a.item.active { background: var(--ce-accent-soft); color: var(--ce-accent); }
      a.item.active::before { content: ""; position: absolute; inset-inline-start: -12px; top: 8px; bottom: 8px; width: 3px; border-radius: 3px; background: var(--ce-gradient); }
      :host([collapsed]) a.item { justify-content: center; padding: 10px 0; }

      .foot { margin-top: auto; display: flex; flex-direction: column; gap: 8px; padding-top: 10px; border-top: 1px solid var(--ce-border); }
      .health { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: var(--ce-radius-sm); background: var(--ce-bg-2); border: 1px solid var(--ce-border); }
      .health .avatar { width: 30px; height: 30px; border-radius: var(--ce-radius-sm); display: grid; place-items: center; background: var(--ce-gradient-soft); color: var(--ce-accent); flex: none; }
      .health .avatar svg { width: 16px; height: 16px; }
      .health .txt { display: flex; flex-direction: column; min-width: 0; font-size: var(--ce-fs-xs); color: var(--ce-text-dim); line-height: 1.35; }
      .health .txt b { color: var(--ce-text); font-weight: 600; display: flex; align-items: center; gap: 6px; }
      .health .txt span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      :host([collapsed]) .health { justify-content: center; padding: 8px 0; }

      main { overflow: auto; padding: var(--ce-gap) 18px; min-width: 0; animation: ce-fade .25s var(--ce-ease); }
      main.chat { padding: 0; overflow: hidden; }

      @media (max-width: 760px) {
        :host, :host([collapsed]) { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
        nav { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 4px; padding: 8px; border-inline-end: none; border-bottom: 1px solid var(--ce-border); }
        .brand { padding: 0 6px; min-height: 0; } .brand .name { display: none; }
        .new-chat { width: auto; margin: 0; padding: 8px 10px; } .new-chat .label { display: none; }
        .group-title, .foot { display: none; }
        a.item { padding: 8px; } a.item .label { display: none; } a.item.active::before { display: none; }
        main { padding: 10px; }
      }
    `,
  ];

  @state() private screen: Screen = (location.hash.replace("#", "") as Screen) || "chat";
  @state() private conversationId: string | null = null;
  @state() private health: Record<string, unknown> | null = null;
  @state() private collapsed = localStorage.getItem("ce.sidebar") === "min";

  connectedCallback() {
    super.connectedCallback();
    if (this.collapsed) this.setAttribute("collapsed", "");
    window.addEventListener("hashchange", () => {
      const s = location.hash.replace("#", "") as Screen;
      if (SCREENS.includes(s)) this.screen = s;
    });
    fetch("/healthz").then((r) => r.json()).then((h) => (this.health = h)).catch(() => (this.health = null));
  }

  private go(s: Screen) { location.hash = s; this.screen = s; }

  private toggleCollapse() {
    this.collapsed = !this.collapsed;
    this.toggleAttribute("collapsed", this.collapsed);
    localStorage.setItem("ce.sidebar", this.collapsed ? "min" : "full");
  }

  private newChat() {
    this.conversationId = null;
    this.go("chat");
    this.renderRoot.querySelector<any>("ce-chat")?.reset?.();
  }

  private openConversation(e: CustomEvent<{ id: string }>) {
    this.conversationId = e.detail.id;
    this.go("chat");
  }

  private item(s: Screen) {
    return html`<a class="item ${this.screen === s ? "active" : ""}" @click=${() => this.go(s)} data-nav=${s} title=${t(("nav_" + s) as any)}>
      ${icons[s]}<span class="label">${t(("nav_" + s) as any)}</span></a>`;
  }

  render() {
    const h = this.health as any;
    const dark = getTheme() === "dark";
    return html`
      <nav>
        <div class="brand">
          ${logo(30)}
          <span class="name grad-text">${t("app_title")}</span>
          <span class="spacer"></span>
          <div class="tools">
            <button class="ghost icon" @click=${() => setLang(lang() === "ar" ? "en" : "ar")} title=${t("lang")} aria-label="language">${icons.globe}</button>
            <button class="ghost icon" @click=${() => setTheme(dark ? "light" : "dark")} title=${t("theme")} aria-label="theme">${dark ? icons.sun : icons.moon}</button>
            <button class="ghost icon" @click=${this.toggleCollapse} title=${t("collapse")} aria-label="collapse">${icons.menu}</button>
          </div>
        </div>
        <button class="new-chat" @click=${this.newChat} title=${t("new_chat")}>${icons.plus}<span class="label">${t("new_chat")}</span></button>
        <div class="group-title">${t("workspace")}</div>
        ${WORKSPACE.map((s) => this.item(s))}
        <div class="group-title">${t("admin_section")}</div>
        ${ADMIN.map((s) => this.item(s))}
        <div class="foot">
          <div class="health" title=${h ? `${t("upstream")} ${String(h.upstream_sha).slice(0, 7)} · ${h.admission_backend}` : t("loading")}>
            <div class="avatar">${icons.user}</div>
            ${h ? html`<div class="txt"><b><span class="dot"></span>${t("online")} · ${(h.providers as string[]).length} providers</b><span class="mono">${String(h.upstream_sha).slice(0, 7)} · ${h.admission_backend}</span></div>`
                : html`<div class="txt"><b><span class="dot warn"></span>${t("loading")}</b></div>`}
          </div>
        </div>
      </nav>
      <main class=${this.screen === "chat" ? "chat" : ""}>
        ${this.screen === "chat" ? html`<ce-chat .conversationId=${this.conversationId} @conversation-changed=${(e: CustomEvent) => (this.conversationId = e.detail.id)}></ce-chat>` : nothing}
        ${this.screen === "conversations" ? html`<ce-conversations @open-conversation=${this.openConversation}></ce-conversations>` : nothing}
        ${this.screen === "catalog" ? html`<ce-catalog></ce-catalog>` : nothing}
        ${this.screen === "providers" ? html`<ce-providers></ce-providers>` : nothing}
        ${this.screen === "capabilities" ? html`<ce-capabilities></ce-capabilities>` : nothing}
        ${this.screen === "admission" ? html`<ce-admission></ce-admission>` : nothing}
        ${this.screen === "skills" ? html`<ce-skills></ce-skills>` : nothing}
      </main>
    `;
  }
}
