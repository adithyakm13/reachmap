export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB");
}

export function parseLegacyDate(value: string): Date {
  if (!value.includes("/")) {
    return new Date(value);
  }
  const [day, month, year] = value.split("/").map(Number);
  return new Date(year, month - 1, day);
}
