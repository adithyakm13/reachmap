export function createRouter() {
  const handlers = new Map<string, (msg: any) => void>();
  return {
    get(topic: string, handler: (msg: any) => void) {
      handlers.set(topic, handler);
    },
  };
}
