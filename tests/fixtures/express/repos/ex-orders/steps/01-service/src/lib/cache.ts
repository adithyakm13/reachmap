const entries = new Map<string, unknown>();

export function get(key: string) {
  return entries.get(key);
}

export function set(key: string, value: unknown) {
  entries.set(key, value);
}
