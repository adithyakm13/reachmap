export function withLogging(label: string): string {
  return `[shop] ${label.trim()}`;
}
