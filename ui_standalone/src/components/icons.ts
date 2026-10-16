import { html, svg } from "lit";

/** Original inline icon set (simple 24px strokes) — no external icon font. */
const wrap = (body: unknown) => svg`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

export const icons = {
  chat: wrap(svg`<path d="M4 5h16v10H9l-5 4z"/>`),
  conversations: wrap(svg`<path d="M4 4h12v9H8l-4 3z"/><path d="M20 9v9l-3-2h-7"/>`),
  catalog: wrap(svg`<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>`),
  providers: wrap(svg`<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>`),
  capabilities: wrap(svg`<path d="M12 3l2.5 5.5L20 9l-4 4 1 6-5-2.7L7 19l1-6-4-4 5.5-.5z"/>`),
  admission: wrap(svg`<path d="M12 3l8 3v6c0 4.5-3.4 7.8-8 9-4.6-1.2-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>`),
  skills: wrap(svg`<path d="M9 3h6l1 4 3 1.5-1 4 1 4L16 18l-1 3H9l-1-3-3-1.5 1-4-1-4L8 7z"/>`),
  plus: wrap(svg`<path d="M12 5v14M5 12h14"/>`),
  send: wrap(svg`<path d="M4 12L20 4l-4 16-4-7z"/>`),
  stop: wrap(svg`<rect x="6" y="6" width="12" height="12" rx="2"/>`),
  copy: wrap(svg`<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5h10"/>`),
  sun: wrap(svg`<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>`),
  moon: wrap(svg`<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>`),
  globe: wrap(svg`<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>`),
  menu: wrap(svg`<path d="M4 7h16M4 12h16M4 17h16"/>`),
  user: wrap(svg`<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>`),
  spark: wrap(svg`<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2 2M16 16l2 2M6 18l2-2M16 8l2-2"/>`),
};

/** Brand mark: layered orbit rings with a gradient core (original). */
export const logo = (size = 28) => html`<svg viewBox="0 0 48 48" width=${size} height=${size} aria-hidden="true">
  <defs><linearGradient id="ceg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--ce-accent)"/><stop offset="1" stop-color="var(--ce-cyan)"/></linearGradient></defs>
  <ellipse cx="24" cy="24" rx="20" ry="9" fill="none" stroke="url(#ceg)" stroke-width="1.6" opacity=".45" transform="rotate(-25 24 24)"/>
  <ellipse cx="24" cy="24" rx="20" ry="9" fill="none" stroke="url(#ceg)" stroke-width="1.6" opacity=".8" transform="rotate(35 24 24)"/>
  <circle cx="24" cy="24" r="5.5" fill="url(#ceg)"/>
</svg>`;
