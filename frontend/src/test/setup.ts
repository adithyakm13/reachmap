import { notifications } from "@mantine/notifications";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { resetAuthorCache } from "../storage";

configure({ asyncUtilTimeout: 8000 });

// An in-memory `Storage`, installed for every test: newer versions of Node have a `localStorage` of their own that shadows the one of
// jsdom and does not work without a file, and the tests should not depend on which Node runs them.
class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length(): number {
    return this.items.size;
  }
  clear(): void {
    this.items.clear();
  }
  getItem(key: string): string | null {
    return this.items.has(key) ? (this.items.get(key) as string) : null;
  }
  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
  setItem(key: string, value: string): void {
    this.items.set(key, String(value));
  }
  [name: string]: unknown;
}

// What the libraries of the page ask of a browser and jsdom does not have: media queries, size observers, scrolling an element into view, the
// matrix React Flow reads the zoom from, and the pointer functions of the focus handling of Mantine.
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

class NoObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
for (const name of ["ResizeObserver", "IntersectionObserver"] as const) {
  if (!(name in globalThis)) {
    Object.defineProperty(globalThis, name, { configurable: true, value: NoObserver });
  }
}
if (!("DOMMatrixReadOnly" in globalThis)) {
  class Matrix {
    m22 = 1;
    constructor(_transform?: string) {}
  }
  Object.defineProperty(globalThis, "DOMMatrixReadOnly", { configurable: true, value: Matrix });
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => undefined);
Element.prototype.hasPointerCapture = Element.prototype.hasPointerCapture ?? (() => false);
Element.prototype.releasePointerCapture = Element.prototype.releasePointerCapture ?? (() => undefined);

function install(name: "localStorage" | "sessionStorage"): void {
  const storage = new MemoryStorage();
  Object.defineProperty(window, name, { value: storage, configurable: true });
  Object.defineProperty(globalThis, name, { value: storage, configurable: true });
}

beforeEach(() => {
  install("localStorage");
  install("sessionStorage");
  resetAuthorCache();
});

afterEach(() => {
  cleanup();
  notifications.clean(); // the toasts live in a store of their own, which outlasts a rendered page
  document.documentElement.removeAttribute("data-mantine-color-scheme");
});
