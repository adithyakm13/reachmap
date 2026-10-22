import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createClient } from "./api/client";
import { fakeClient } from "./test/fakeClient";
import { renderAt } from "./test/render";
import { startSession } from "./session";

describe("a token typed into a page that is already open", () => {
  it("shows the reports once the address holds the token, and removes the fragment", async () => {
    const replace = vi.spyOn(window.history, "replaceState");
    const made: string[] = [];
    const session = startSession(window, (token) => {
      made.push(token);
      return fakeClient();
    });
    renderAt(session, "/");
    expect((await screen.findByRole("alert")).textContent).toContain("reachmap serve printed");
    window.location.hash = "#token=typed-token_1";
    expect(await screen.findByText(/no report yet/)).toBeTruthy();
    expect(made).toEqual(["typed-token_1"]);
    await waitFor(() => expect(window.location.hash).toBe(""));
    expect(window.location.href).not.toContain("typed-token");
    expect(replace).toHaveBeenCalledWith(null, "", expect.not.stringContaining("token"));
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0);
    session.stop();
  });

  it("replaces the client when another token is typed, and the page asks again with it", async () => {
    const seen: string[] = [];
    const fetchFor = (token: string) =>
      vi.fn(async (_url: string, init: RequestInit) => {
        seen.push(`${token}:${(init.headers as Record<string, string>)["X-Reachmap-Token"]}`);
        return new Response(JSON.stringify({ reports: [] }), { status: 200 });
      });
    const session = startSession(window, (token) => createClient(token, fetchFor(token)));
    renderAt(session, "/");
    await screen.findByRole("alert");
    window.location.hash = "#token=first";
    await screen.findByText(/no report yet/);
    window.location.hash = "#token=second";
    await waitFor(() => expect(seen).toContain("second:second"));
    expect(seen.every((s) => s.split(":")[0] === s.split(":")[1])).toBe(true);
    session.stop();
  });

  it("ignores a fragment that holds no token", async () => {
    const make = vi.fn(() => fakeClient());
    const session = startSession(window, make);
    renderAt(session, "/");
    await screen.findByRole("alert");
    window.location.hash = "#somewhere";
    await new Promise((r) => setTimeout(r, 30));
    expect(make).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("reachmap serve printed");
    session.stop();
  });
});
