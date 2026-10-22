export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB");
}
