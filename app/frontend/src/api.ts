import type { Evidence, Health, Posture, StreamEvent, Suggestions } from "./types";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

export const getSuggestions = () => getJSON<Suggestions>("/api/suggestions");
export const getPosture = () => getJSON<Posture>("/api/posture");
export const getEvidence = (obligationId: string) =>
  getJSON<Evidence>(`/api/evidence/${encodeURIComponent(obligationId)}`);
export const getHealth = () => getJSON<Health>("/api/health");

/**
 * Ask Genie and invoke `onEvent` as each Server-Sent Event arrives.
 *
 * Uses fetch + a manual SSE parse rather than EventSource because EventSource cannot
 * issue a POST, and the question needs to go in a request body rather than a query
 * string. Buffering on `\n\n` is required: a chunk boundary can land mid-event, and
 * parsing per-chunk drops events intermittently.
 */
export async function askGenie(
  question: string,
  conversationId: string | null,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`Request failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split: number;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as StreamEvent);
      } catch {
        // A malformed frame should not abort a conversation that is otherwise working.
      }
    }
  }
}

export async function exportCsv(
  question: string,
  sql: string | null,
  columns: string[],
  rows: Record<string, unknown>[],
): Promise<void> {
  const res = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, sql, columns, rows }),
  });
  if (!res.ok) throw new Error("Export failed");

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `complylens_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
