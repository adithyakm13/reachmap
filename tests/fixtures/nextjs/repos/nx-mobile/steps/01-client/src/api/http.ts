export async function apiGet(url: string) {
  const response = await fetch(url);
  return response.json();
}

export async function apiDelete(url: string) {
  const response = await fetch(url, { method: "DELETE" });
  return response.json();
}
