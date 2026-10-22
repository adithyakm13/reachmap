import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api/client";

export type Loaded<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: T; error: null }
  | { status: "error"; data: null; error: Error };

export interface Async<T> {
  state: Loaded<T>;
  reload: () => void;
}

function asError(e: unknown): Error {
  return e instanceof Error ? e : new ApiError(0);
}

/**
 * Load something from the runner when the page opens and whenever `key` changes. `reload` fetches it again and keeps showing the old answer
 * until the new one arrives (a decision recorded does not blank the page). With `intervalMs` it reloads on its own. An answer that arrives
 * after the page has moved on is dropped.
 */
export function useAsync<T>(load: () => Promise<T>, key: string, intervalMs?: number): Async<T> {
  const [state, setState] = useState<Loaded<T>>({ status: "loading", data: null, error: null });
  const loader = useRef(load);
  loader.current = load;
  const generation = useRef(0);

  const run = useCallback((silent: boolean) => {
    const mine = ++generation.current;
    if (!silent) {
      setState((old) => (old.status === "ready" ? old : { status: "loading", data: null, error: null }));
    }
    loader
      .current()
      .then((data) => {
        if (mine === generation.current) {
          setState({ status: "ready", data, error: null });
        }
      })
      .catch((e: unknown) => {
        if (mine === generation.current) {
          setState({ status: "error", data: null, error: asError(e) });
        }
      });
  }, []);

  useEffect(() => {
    setState({ status: "loading", data: null, error: null });
    run(true);
    return () => {
      generation.current += 1;
    };
  }, [key, run]);

  useEffect(() => {
    if (intervalMs === undefined) {
      return undefined;
    }
    const timer = window.setInterval(() => run(true), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs, run]);

  const reload = useCallback(() => run(true), [run]);
  return { state, reload };
}

/** The document title: the page name, then the name of the app. */
export function useTitle(title: string): void {
  useEffect(() => {
    const before = document.title;
    document.title = title;
    return () => {
      document.title = before;
    };
  }, [title]);
}
