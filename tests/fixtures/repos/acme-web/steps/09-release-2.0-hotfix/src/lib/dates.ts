export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { timeZone: "UTC" });
}

export function parseLegacyDate(value: string): Date {
  const [day, month, year] = value.split("/").map(Number);
  return new Date(year, month - 1, day);
}
