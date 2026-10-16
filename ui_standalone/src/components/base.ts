import { LitElement, css } from "lit";
import { onLang } from "../i18n";

/** Shared styles + re-render on language switch. */
export class CeElement extends LitElement {
  private _off?: () => void;
  connectedCallback() { super.connectedCallback(); this._off = onLang(() => this.requestUpdate()); }
  disconnectedCallback() { this._off?.(); super.disconnectedCallback(); }
  static baseStyles = css`
    :host { display: block; color: var(--ce-text); font-family: var(--ce-font); }
    h2 { font-size: 1.05rem; margin: 0 0 var(--ce-gap); font-weight: 600; }
    .card { background: var(--ce-bg-elev); border: 1px solid var(--ce-border); border-radius: var(--ce-radius); padding: var(--ce-gap); box-shadow: var(--ce-shadow); }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .grow { flex: 1; }
    .dim { color: var(--ce-text-dim); font-size: .85rem; }
    .mono { font-family: var(--ce-mono); font-size: .8rem; }
    button, .btn { background: var(--ce-bg-elev-2); color: var(--ce-text); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm); padding: 6px 12px; cursor: pointer; font: inherit; }
    button.primary { background: var(--ce-accent); color: var(--ce-accent-ink); border-color: transparent; font-weight: 600; }
    button.danger { color: var(--ce-danger); }
    button:disabled { opacity: .5; cursor: not-allowed; }
    input, select, textarea { background: var(--ce-bg); color: var(--ce-text); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm); padding: 7px 10px; font: inherit; }
    textarea { resize: vertical; min-height: 60px; }
    table { width: 100%; border-collapse: collapse; font-size: .9rem; }
    th, td { text-align: start; padding: 6px 8px; border-bottom: 1px solid var(--ce-border); vertical-align: top; }
    th { color: var(--ce-text-dim); font-weight: 500; position: sticky; top: 0; background: var(--ce-bg-elev); }
    .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: .75rem; border: 1px solid var(--ce-border); background: var(--ce-bg-elev-2); }
    .pill.ok { color: var(--ce-ok); } .pill.warn { color: var(--ce-warn); } .pill.bad { color: var(--ce-danger); }
    .err { color: var(--ce-danger); font-size: .85rem; white-space: pre-wrap; }
    details > summary { cursor: pointer; color: var(--ce-text-dim); }
    pre { background: var(--ce-bg); border: 1px solid var(--ce-border); border-radius: var(--ce-radius-sm); padding: 8px; overflow: auto; font-family: var(--ce-mono); font-size: .75rem; }
  `;
}
