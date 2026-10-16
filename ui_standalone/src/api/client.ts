/** UI-side API facade over the zero-dep SDK (same-origin by default). */
import { ChatEngine, type Json } from "./sdk";

export const api = new ChatEngine({ baseUrl: (import.meta as any).env?.VITE_CE_BASE_URL ?? "" });

export async function getJson<T = Json>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}
export async function sendJson<T = Json>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  if (!r.ok) {
    let msg = text;
    try { msg = JSON.parse(text).error?.message ?? text; } catch { /* raw */ }
    throw new Error(`${r.status}: ${msg}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}
