export async function apiGet<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`GET ${url} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, { method: "POST", body: JSON.stringify(body) });
  if (!response.ok) {
    throw new Error(`POST ${url} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}
