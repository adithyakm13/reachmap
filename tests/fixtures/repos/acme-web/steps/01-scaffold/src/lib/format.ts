export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

export function padNumber(value: number, width = 2): string {
  return String(value).padStart(width, "0");
}
