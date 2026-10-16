import { LitElement, css } from "lit";
import { onLang } from "../i18n";

/** Shared styles + re-render on language switch. */
export class CeElement extends LitElement {
  private _off?: () => void;
  connectedCallback() { super.connectedCallback(); this._off = onLang(() => this.requestUpdate()); }
  disconnectedCallback() { this._off?.(); super.disconnectedCallback(); }
  static baseStyles = css`
    :host { display: block; color: var(--ce-text); font-family: var(--ce-font); font-size: var(--ce-fs-base); }
    *, *::before, *::after { box-sizing: border-box; }
    h2 { font-size: var(--ce-fs-lg); margin: 0 0 var(--ce-gap); font-weight: 700; letter-spacing: -.01em; }
    h3 { font-size: var(--ce-fs-base); margin: 0 0 8px; font-weight: 600; }

    /* surfaces */
    .card {
      background: var(--ce-glass); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-lg);
      padding: 16px; box-shadow: var(--ce-shadow-sm); backdrop-filter: var(--ce-blur); -webkit-backdrop-filter: var(--ce-blur);
      transition: border-color var(--ce-dur) var(--ce-ease), box-shadow var(--ce-dur) var(--ce-ease);
    }
    .card:hover { border-color: var(--ce-border-strong); }
    .card.glow:hover { box-shadow: var(--ce-shadow-glow); }
    .card + .card { margin-top: var(--ce-gap); }
    .section-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; color: var(--ce-accent); }
    .section-head h2, .section-head h3 { margin: 0; color: var(--ce-text); }
    .section-head svg { width: 18px; height: 18px; flex: none; }

    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .grow { flex: 1; min-width: 0; }
    .dim { color: var(--ce-text-dim); font-size: var(--ce-fs-sm); }
    .muted { color: var(--ce-text-muted); font-size: var(--ce-fs-xs); }
    .mono { font-family: var(--ce-mono); font-size: var(--ce-fs-xs); }
    .grad-text { background: var(--ce-gradient); -webkit-background-clip: text; background-clip: text; color: transparent; }

    /* controls */
    button, .btn {
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      background: var(--ce-bg-3); color: var(--ce-text); border: 1px solid var(--ce-border);
      border-radius: var(--ce-radius-sm); padding: 7px 13px; cursor: pointer; font: inherit; font-size: var(--ce-fs-sm); font-weight: 500;
      transition: background var(--ce-dur) var(--ce-ease), border-color var(--ce-dur) var(--ce-ease), transform var(--ce-dur) var(--ce-ease), box-shadow var(--ce-dur) var(--ce-ease);
    }
    button:hover:not(:disabled), .btn:hover { background: var(--ce-bg-hover); border-color: var(--ce-border-strong); }
    button:active:not(:disabled) { transform: translateY(1px); }
    button.primary { background: var(--ce-gradient); color: var(--ce-accent-ink); border-color: transparent; font-weight: 600; box-shadow: 0 2px 10px var(--ce-accent-glow); }
    button.primary:hover:not(:disabled) { filter: brightness(1.08); box-shadow: 0 4px 18px var(--ce-accent-glow); }
    button.ghost { background: transparent; border-color: transparent; color: var(--ce-text-dim); }
    button.ghost:hover:not(:disabled) { background: var(--ce-accent-soft); color: var(--ce-text); border-color: transparent; }
    button.danger { color: var(--ce-danger); }
    button.danger:hover:not(:disabled) { background: rgba(255, 92, 106, .12); border-color: rgba(255, 92, 106, .3); }
    button.icon { width: 34px; height: 34px; padding: 0; border-radius: var(--ce-radius-sm); }
    button.icon svg { width: 18px; height: 18px; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--ce-accent-glow); border-color: var(--ce-accent); }

    input, select, textarea {
      background: var(--ce-bg-2); color: var(--ce-text); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm);
      padding: 8px 11px; font: inherit; font-size: var(--ce-fs-sm);
      transition: border-color var(--ce-dur) var(--ce-ease), box-shadow var(--ce-dur) var(--ce-ease);
    }
    input::placeholder, textarea::placeholder { color: var(--ce-text-muted); }
    select { cursor: pointer; }
    textarea { resize: vertical; min-height: 60px; line-height: 1.55; }

    /* data */
    table { width: 100%; border-collapse: collapse; font-size: var(--ce-fs-sm); }
    th, td { text-align: start; padding: 8px 10px; border-bottom: 1px solid var(--ce-border); vertical-align: top; }
    th { color: var(--ce-text-dim); font-weight: 600; font-size: var(--ce-fs-xs); text-transform: uppercase; letter-spacing: .04em; position: sticky; top: 0; background: var(--ce-bg-2); z-index: 1; }
    tbody tr { transition: background var(--ce-dur) var(--ce-ease); }
    tbody tr:hover { background: var(--ce-accent-soft); }

    .pill {
      display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px; border-radius: var(--ce-radius-full);
      font-size: var(--ce-fs-xs); font-weight: 600; border: 1px solid var(--ce-border); background: var(--ce-bg-3); color: var(--ce-text-dim); line-height: 1.5;
    }
    .pill.accent { color: var(--ce-accent); background: var(--ce-accent-soft); border-color: transparent; }
    .pill.ok { color: var(--ce-ok); background: rgba(34, 224, 122, .10); border-color: rgba(34, 224, 122, .22); }
    .pill.warn { color: var(--ce-warn); background: rgba(255, 181, 71, .10); border-color: rgba(255, 181, 71, .22); }
    .pill.bad { color: var(--ce-danger); background: rgba(255, 92, 106, .10); border-color: rgba(255, 92, 106, .22); }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ce-ok); box-shadow: 0 0 0 0 rgba(34, 224, 122, .5); animation: ce-pulse 2s infinite; }
    .dot.bad { background: var(--ce-danger); animation: none; }
    .dot.warn { background: var(--ce-warn); animation: none; }

    .err { color: var(--ce-danger); font-size: var(--ce-fs-sm); white-space: pre-wrap; background: rgba(255, 92, 106, .08); border: 1px solid rgba(255, 92, 106, .2); border-radius: var(--ce-radius-sm); padding: 8px 10px; }
    details > summary { cursor: pointer; color: var(--ce-text-dim); font-size: var(--ce-fs-sm); list-style: none; display: flex; align-items: center; gap: 6px; }
    details > summary::before { content: "›"; display: inline-block; transition: transform var(--ce-dur) var(--ce-ease); color: var(--ce-accent); }
    details[open] > summary::before { transform: rotate(90deg); }
    pre { background: var(--ce-bg-2); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm); padding: 10px; overflow: auto; font-family: var(--ce-mono); font-size: var(--ce-fs-xs); margin: 8px 0 0; }
    .empty { color: var(--ce-text-muted); text-align: center; padding: 28px 12px; font-size: var(--ce-fs-sm); }

    /* scrollbars */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-thumb { background: var(--ce-border-strong); border-radius: var(--ce-radius-full); }
    ::-webkit-scrollbar-thumb:hover { background: var(--ce-accent-glow); }
    ::-webkit-scrollbar-track { background: transparent; }

    /* motion */
    .fade-up { animation: ce-fade-up .28s var(--ce-ease) both; }
    @keyframes ce-fade-up { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    @keyframes ce-fade { from { opacity: 0; } to { opacity: 1; } }
    @keyframes ce-pulse { 0% { box-shadow: 0 0 0 0 rgba(34, 224, 122, .45); } 70% { box-shadow: 0 0 0 7px rgba(34, 224, 122, 0); } 100% { box-shadow: 0 0 0 0 rgba(34, 224, 122, 0); } }
    @keyframes ce-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
  `;
}
