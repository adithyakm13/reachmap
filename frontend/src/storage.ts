// What the page keeps in `localStorage`, as a convenience only: the theme and the name of the person deciding. Every read and write is
// inside try/catch (storage can be missing, full or blocked), and the page works the same without it. The session token is never stored.

import type { MantineColorSchemeManager } from "@mantine/core";
import { useSyncExternalStore } from "react";

export const THEME_KEY = "reachmap-theme";
export const AUTHOR_KEY = "reachmap-author";

export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // the name or theme is a convenience: not remembering it is not an error
  }
}

// ------------------------------------------------------------------------------ the name of the person deciding

type Listener = () => void;
const listeners = new Set<Listener>();
let current: string | null = null;

function loadAuthor(): string {
  if (current === null) {
    current = readStored(AUTHOR_KEY) ?? "";
  }
  return current;
}

export function rememberAuthor(name: string): void {
  current = name;
  writeStored(AUTHOR_KEY, name);
  listeners.forEach((l) => l());
}

/** Forget the in-memory copy (the next read goes to storage again): for tests. */
export function resetAuthorCache(): void {
  current = null;
  listeners.forEach((l) => l());
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The remembered name of the person deciding, shared by every decision form of the page. */
export function useRememberedAuthor(): string {
  return useSyncExternalStore(subscribe, loadAuthor, () => "");
}

// ------------------------------------------------------------------------------ the theme

export type Theme = "light" | "dark";

export function storedTheme(): Theme {
  return readStored(THEME_KEY) === "dark" ? "dark" : "light"; // light unless the person chose dark
}

/** Set the theme on the page before it renders (Mantine sets the same attribute once it has mounted). */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-mantine-color-scheme", theme);
}

/** Mantine's colour scheme manager over the storage of this module: light by default, dark only when it was chosen, never "auto". */
export const colorSchemeManager: MantineColorSchemeManager = {
  get: () => storedTheme(),
  set: (value) => writeStored(THEME_KEY, value === "dark" ? "dark" : "light"),
  subscribe: () => undefined,
  unsubscribe: () => undefined,
  clear: () => writeStored(THEME_KEY, "light"),
};
