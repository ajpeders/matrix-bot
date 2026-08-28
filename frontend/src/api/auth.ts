import { apiFetch, clearToken, getToken, setToken } from "./client";

export async function login(password: string): Promise<void> {
  const res = await apiFetch<{ token: string }>("/api/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  setToken(res.token);
}

export function isAuthenticated(): boolean {
  return Boolean(getToken());
}

export function logout(): void {
  clearToken();
}
