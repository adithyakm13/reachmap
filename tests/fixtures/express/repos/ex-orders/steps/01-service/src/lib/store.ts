const rows = new Map<string, unknown>();

export function get(id: string) {
  return rows.get(id);
}
