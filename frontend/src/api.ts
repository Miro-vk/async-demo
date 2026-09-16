import type { Counts, EmailDetail, InboxItem, Outcome, Thresholds } from "./types";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} on ${path}`);
  return response.json() as Promise<T>;
}

export const api = {
  inbox: (params: { action?: string; pendingOnly?: boolean } = {}) => {
    const query = new URLSearchParams();
    if (params.action) query.set("action", params.action);
    if (params.pendingOnly) query.set("pending_only", "true");
    const suffix = query.toString() ? `?${query}` : "";
    return get<{ items: InboxItem[]; counts: Counts }>(`/api/inbox${suffix}`);
  },

  email: (id: string) => get<EmailDetail>(`/api/emails/${id}`),

  thresholds: () => get<Thresholds>("/api/thresholds"),

  review: async (
    id: string,
    body: { outcome: Outcome; note: string; edits: { field_path: string; original_value: string | null; corrected_value: string | null }[] },
  ): Promise<EmailDetail> => {
    const response = await fetch(`/api/emails/${id}/review`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(detail.detail ?? "review failed");
    }
    return response.json() as Promise<EmailDetail>;
  },
};
