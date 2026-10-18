/**
 * chat-engine-client (TypeScript) — zero-dependency client for chat_engine (CE-11).
 * Works in browsers and Node ≥ 18 (fetch + ReadableStream). SSE parsed by hand
 * (EventSource cannot POST).
 */

export type Json = Record<string, unknown>;

export interface Turn {
  id: string;
  seq: number;
  role: string;
  kind: "message" | "summary" | "system" | "tool";
  status: "complete" | "partial" | "failed";
  parts: Array<{ type: string; text?: string } & Json>;
  provider_key?: string | null;
  model_key?: string | null;
  usage?: Json;
  skills_used?: Array<{ skill_id: string; version?: string; source?: string }>;
  stream_id?: string | null;
  metadata?: Json;
}

export interface ChatResponse {
  conversation_id: string;
  turn: Turn;
  user_turn_id: string;
  execution_id?: string | null;
  provider_key?: string | null;
  model_key?: string | null;
  usage?: Json;
  handoff?: { packet_id: string; estimated_tokens: number; dropped_messages: number; summarized: boolean };
}

export type StreamEventType =
  | "started" | "message_delta" | "tool_call_start" | "tool_call_delta" | "tool_call_end"
  | "usage" | "heartbeat" | "completed" | "failed" | "cancelled";

export interface StreamEvent {
  seq: number;
  event_id?: string;
  type: StreamEventType;
  stream_id: string;
  data: Json;
}

export interface ChatEngineErrorBody {
  error: { code: string; message: string; retryable?: boolean; details?: Json; type?: string };
}

export class ChatEngineAPIError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Json;
  readonly retryable: boolean;
  constructor(status: number, body: Partial<ChatEngineErrorBody> | undefined, fallback: string) {
    const e = body?.error;
    super(`${status} ${e?.code ?? "http_error"}: ${e?.message ?? fallback}`);
    this.status = status;
    this.code = e?.code ?? "http_error";
    this.details = e?.details ?? {};
    this.retryable = e?.retryable ?? (status === 429 || status === 503);
  }
  get retryAfterMs(): number | undefined {
    const v = this.details["retry_after_ms"];
    return typeof v === "number" ? v : undefined;
  }
}

export interface ClientOptions {
  baseUrl?: string;
  apiKey?: string;
  fetch?: typeof fetch;
  maxRetries?: number;
  headers?: Record<string, string>;
}

const RETRYABLE = new Set([429, 502, 503, 504]);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class ChatEngine {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxRetries: number;
  private readonly headers: Record<string, string>;

  constructor(opts: ClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "http://localhost:8080").replace(/\/$/, "");
    this.fetchImpl = opts.fetch ?? ((input, init) => fetch(input, init));  // keep window as receiver
    this.maxRetries = opts.maxRetries ?? 3;
    this.headers = { "content-type": "application/json", ...(opts.headers ?? {}) };
    if (opts.apiKey) this.headers["authorization"] = `Bearer ${opts.apiKey}`;
  }

  // ---- core ---------------------------------------------------------------------------------

  private async request<T>(method: string, path: string, body?: unknown, query?: Json): Promise<T> {
    const url = new URL(this.baseUrl + path, typeof location !== "undefined" ? location.origin : "http://localhost");
    if (query) for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    let last: ChatEngineAPIError | undefined;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const res = await this.fetchImpl(url, { method, headers: this.headers, body: body === undefined ? undefined : JSON.stringify(body) });
      if (RETRYABLE.has(res.status) && attempt < this.maxRetries) {
        last = await this.toError(res);
        const ra = last.retryAfterMs ?? Number(res.headers.get("retry-after") ?? 0) * 1000;
        await sleep(ra > 0 ? ra : Math.min(8000, 250 * 2 ** attempt) * (0.5 + Math.random()));
        continue;
      }
      if (!res.ok) throw await this.toError(res);
      if (res.status === 204) return {} as T;
      return (await res.json()) as T;
    }
    throw last ?? new ChatEngineAPIError(503, undefined, "retry exhausted");
  }

  private async toError(res: Response): Promise<ChatEngineAPIError> {
    let body: Partial<ChatEngineErrorBody> | undefined;
    const text = await res.text();
    try { body = JSON.parse(text); } catch { /* non-JSON */ }
    return new ChatEngineAPIError(res.status, body, text);
  }

  private async *stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
    const res = await this.fetchImpl(this.baseUrl + path, {
      method: "POST", headers: { ...this.headers, accept: "text/event-stream" }, body: JSON.stringify(body), signal,
    });
    if (!res.ok || !res.body) throw await this.toError(res);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let id: string | undefined;
    let data: string[] = [];
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).replace(/\r$/, "");
        buf = buf.slice(nl + 1);
        if (line === "") {
          if (data.length) {
            const payload = data.join("\n");
            data = [];
            if (payload === "[DONE]") return;
            const d = JSON.parse(payload);
            yield { seq: d.seq ?? 0, event_id: id ?? d.event_id, type: d.type, stream_id: d.stream_id, data: d.data ?? {} };
          }
          id = undefined;
          continue;
        }
        if (line.startsWith(":")) continue;
        const i = line.indexOf(":");
        const key = i < 0 ? line : line.slice(0, i);
        const val = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
        if (key === "id") id = val; else if (key === "data") data.push(val);
      }
    }
  }

  // ---- surface ------------------------------------------------------------------------------

  health() { return this.request<Json>("GET", "/healthz"); }
  models() { return this.request<{ object: "list"; data: Array<{ id: string; owned_by: string }> }>("GET", "/v1/openai/models"); }
  catalog(q: { offset?: number; limit?: number; q?: string } = {}) { return this.request<Json>("GET", "/v1/catalog/models", undefined, q); }

  createProject(body: { name: string } & Json) { return this.request<Json>("POST", "/v1/projects", body); }
  listProjects() { return this.request<{ projects: Json[]; total: number }>("GET", "/v1/projects"); }
  createConversation(body: Json = {}) { return this.request<Json>("POST", "/v1/conversations", body); }
  getConversation(id: string) { return this.request<Json>("GET", `/v1/conversations/${id}`); }
  listConversations(q: Json = {}) { return this.request<{ conversations: Json[]; total: number }>("GET", "/v1/conversations", undefined, q); }
  turns(id: string, q: { after_seq?: number; limit?: number } = {}) { return this.request<{ turns: Turn[]; total: number }>("GET", `/v1/conversations/${id}/turns`, undefined, q); }

  chat(body: { message: string; conversation_id?: string; project_id?: string; model?: string } & Json) {
    return this.request<ChatResponse>("POST", "/v1/chat", body);
  }
  chatStream(body: ({ message: string; conversation_id?: string } | { messages: Array<{ role: string; content: string }> }) & Json, signal?: AbortSignal) {
    return this.stream("/v1/chat/stream", body, signal);
  }
  cancelStream(streamId: string) { return this.request<Json>("POST", `/v1/chat/stream/${streamId}:cancel`); }
  resumeStream(streamId: string, deliveredUntilSeq: number, signal?: AbortSignal) { return this.stream(`/v1/chat/stream/${streamId}:resume`, { delivered_until_seq: deliveredUntilSeq }, signal); }

  completions(body: { model: string; messages: Array<{ role: string; content: string }> } & Json) {
    return this.request<Json>("POST", "/v1/chat/completions", body);
  }
  execute(body: { ask: string } & Json) { return this.request<Json>("POST", "/v1/execute", body); }
}

export default ChatEngine;
