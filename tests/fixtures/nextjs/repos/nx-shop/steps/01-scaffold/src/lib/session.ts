let warmed = false;

export function warmCache(label: string): void {
  warmed = label.length > 0;
}

export function readSession(): { user: string } | null {
  return warmed ? { user: "demo" } : null;
}
