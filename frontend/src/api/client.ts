const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const TOKEN_STORAGE = "matrixbot.token";

export function apiUrl(path: string): string {
  if (!API_BASE_URL) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE);
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...options, headers });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Network request failed";
    throw new ApiError(0, `Could not reach the bot: ${message}`);
  }

  if (!res.ok) {
    if (res.status === 401 && !path.endsWith("/login")) {
      clearToken();
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
      throw new ApiError(401, "Authentication required");
    }
    const body = await res.text();
    try {
      const parsed = JSON.parse(body) as { error?: unknown; reason?: unknown };
      const detail = parsed.error ?? parsed.reason;
      if (typeof detail === "string") throw new ApiError(res.status, detail);
    } catch (err) {
      if (err instanceof ApiError) throw err;
    }
    throw new ApiError(res.status, body || res.statusText);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}
