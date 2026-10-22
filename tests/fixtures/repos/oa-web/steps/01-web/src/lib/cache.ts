const entries = new Map<string, unknown>();

export const cache = {
  get(key: string) {
    return entries.get(key);
  },
  set(key: string, value: unknown) {
    entries.set(key, value);
  },
};
