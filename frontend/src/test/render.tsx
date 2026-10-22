import { expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import type { Client } from "../api/client";
import type { ReportListItem, Report } from "../api/types";
import { AppProviders } from "../providers";
import { makeRoutes } from "../routes";
import type { Session } from "../session";
import { check } from "../report/wording";
import { fakeClient, type FakeClient } from "./fakeClient";
import { fixture, type FixtureName } from "./fixtures";

export function renderAt(client: Client | null | Session, path: string) {
  const router = createMemoryRouter(makeRoutes(client), { initialEntries: [path] });
  const view = render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return { ...view, router };
}

/** A runner that has one report, called `name`, and serves `report` for it. */
export function runnerWith(name: string, report: Report | ((n: string) => Report)): FakeClient {
  const item: ReportListItem = { name, key: { workspace: "acme", repo: "acme/api", change: "compare:main...x" }, revision: 1, status: "completed", created: "2026-01-02T03:04:05Z" };
  return fakeClient({ reports: [item], report: typeof report === "function" ? report : () => report });
}

export const NAME = "acme-api-compare-main-x-0123abcd-0123456789ab-r1";

/** Render the page of a fixture report (`tail` is what follows `/reports/<name>`: a tab, or a tab and an id) and wait for it. */
export async function renderReport(name: FixtureName | Report, tail = "") {
  const report = typeof name === "string" ? fixture(name) : name;
  const client = runnerWith(NAME, report);
  const view = renderAt(client, `/reports/${NAME}${tail}`);
  // the pages are chunks of their own, loaded when the route is first visited: the first level one heading is the loaded page
  await screen.findByRole("heading", { level: 1 }, { timeout: 5000 });
  await waitFor(() => expect(screen.queryByRole("status", { name: "Loading." })).toBeNull());
  return { ...view, client, report };
}

/**
 * What the page says itself: the text nodes of the page that are not content (`data-c`), each one apart. They are joined with a space, not
 * glued (`textContent` would run the end of one element into the start of the next and hide a banned word at the seam, or make one up).
 */
export function ownWordsList(root: HTMLElement): string[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement?.closest("[data-c]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const out: string[] = [];
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    out.push(node.textContent ?? "");
  }
  return out;
}

export function ownWords(root: HTMLElement): string {
  return ownWordsList(root).join(" ");
}

export function expectOwnWordsClean(root: HTMLElement): void {
  expect(ownWordsList(root).flatMap((t) => check(t))).toEqual([]);
  expect(check(ownWords(root))).toEqual([]);
}

export function headings(root: HTMLElement, level: number): string[] {
  return within(root).queryAllByRole("heading", { level }).map((h) => h.textContent ?? "");
}

/** The words of everything on the page, drawers and dialogs included (they are drawn into portals outside the container). */
export function pageText(): string {
  // the style elements of the UI kit are in the page too, and their text is not the page's
  const clone = document.body.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("style").forEach((s) => s.remove());
  return clone.textContent ?? "";
}

/** What the page says itself over the whole document, portals included. */
export function expectPageWordsClean(): void {
  expectOwnWordsClean(document.body);
}

export function tagNames(root: HTMLElement): Set<string> {
  return new Set([...root.querySelectorAll("*")].map((el) => el.tagName.toLowerCase()));
}
