// Клиент REST API. Все запросы идут на свой origin (/api/v1/*), cookie передаются автоматически.

export const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(`API ${status}`);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_PREFIX}${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  const body = resp.headers.get("content-type")?.includes("application/json") ? await resp.json() : null;
  if (!resp.ok) throw new ApiError(resp.status, body);
  return body as T;
}
