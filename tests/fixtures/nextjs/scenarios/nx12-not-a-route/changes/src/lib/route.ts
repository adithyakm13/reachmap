export function buildRoute(path: string, params: Record<string, string>): string {
  return Object.entries(params ?? {}).reduce((url, [key, value]) => url.replace(`:${key}`, value), path);
}
