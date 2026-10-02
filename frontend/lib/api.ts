// Клиент REST API. Все запросы идут на свой origin (/api/v1/*), cookie передаются автоматически.
// При 401 один раз пробуем обновить сессию (refresh-cookie) и повторяем запрос.

export const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `API ${status}`);
  }
}

/** Человекочитаемое сообщение из ответа FastAPI (detail — строка или список ошибок валидации). */
export function errorMessage(err: unknown, fallback = "Произошла ошибка"): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (Array.isArray(err.detail)) {
      return err.detail
        .map((e: { msg?: string; loc?: string[] }) => (e.msg ?? "").replace(/^Value error, /, ""))
        .filter(Boolean)
        .join("; ");
    }
  }
  return fallback;
}

let refreshing: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  refreshing ??= fetch(`${API_PREFIX}/auth/refresh`, { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      setTimeout(() => (refreshing = null), 0);
    });
  return refreshing;
}

const NO_REFRESH = ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"];

async function request(path: string, init: RequestInit, retry: boolean): Promise<Response> {
  const resp = await fetch(`${API_PREFIX}${path}`, { credentials: "same-origin", ...init });
  if (resp.status === 401 && retry && !NO_REFRESH.some((p) => path.startsWith(p))) {
    if (await refreshSession()) return request(path, init, false);
  }
  return resp;
}

async function parse<T>(resp: Response): Promise<T> {
  // 204 No Content приходит без тела, иногда с JSON-заголовком — resp.json() на нём падает.
  const text = resp.status === 204 ? "" : await resp.text();
  const isJson = resp.headers.get("content-type")?.includes("application/json");
  const body = text && isJson ? JSON.parse(text) : null;
  if (!resp.ok) throw new ApiError(resp.status, body?.detail ?? body);
  return body as T;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = init.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init.headers };
  return parse<T>(await request(path, { ...init, headers }, true));
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<T>(path, { method: "POST", body: form });
  },
};

export function queryString(params: Record<string, string | number | boolean | undefined | null>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}
