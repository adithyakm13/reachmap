// The session: the runner client (which holds the token), in memory only.
//
// The token comes from the address fragment when the page starts, and again whenever the fragment changes (a person who types the address
// that `reachmap serve` printed into a page that is already open changes only the fragment, and the page is not loaded again). Each time
// the token is taken out of the address bar and the history at once and a new client replaces the old one.

import { useSyncExternalStore } from "react";
import { createClient, takeToken, type Client, type HistoryLike, type LocationLike } from "./api/client";

export interface Session {
  get(): Client | null;
  /** Changes with each new client, so that what was loaded with the old one is loaded again. */
  version(): number;
  set(client: Client | null): void;
  subscribe(listener: () => void): () => void;
}

export function createSession(initial: Client | null): Session {
  let client = initial;
  let version = 0;
  const listeners = new Set<() => void>();
  return {
    get: () => client,
    version: () => version,
    set(next) {
      client = next;
      version += 1;
      listeners.forEach((l) => l());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export interface WindowLike {
  location: LocationLike;
  history: HistoryLike;
  addEventListener(type: "hashchange", listener: () => void): void;
  removeEventListener(type: "hashchange", listener: () => void): void;
}

export type StartedSession = Session & { stop(): void };

/** Take the token of the address now, and again whenever the fragment changes. */
export function startSession(win: WindowLike, make: (token: string) => Client = (token) => createClient(token)): StartedSession {
  const first = takeToken(win.location, win.history);
  const session = createSession(first === null ? null : make(first));
  const onChange = () => {
    const token = takeToken(win.location, win.history);
    if (token !== null) {
      session.set(make(token));
    }
  };
  win.addEventListener("hashchange", onChange);
  return { ...session, stop: () => win.removeEventListener("hashchange", onChange) };
}

export function useSession(session: Session): { client: Client | null; version: number } {
  const client = useSyncExternalStore(session.subscribe, session.get);
  const version = useSyncExternalStore(session.subscribe, session.version);
  return { client, version };
}
