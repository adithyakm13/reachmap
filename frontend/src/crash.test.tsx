import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fakeClient } from "./test/fakeClient";
import { expectOwnWordsClean, renderAt } from "./test/render";

vi.mock("./pages/Reports", () => ({
  Reports: () => {
    throw new Error("<img src=x onerror=alert(1)> it is safe");
  },
}));

describe("a page that fails to render", () => {
  it("shows fixed words inside the frame and never the error", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { container } = renderAt(fakeClient(), "/");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("This page could not be shown");
    expect(container.textContent).not.toContain("onerror");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeTruthy(); // the frame is still there
    expectOwnWordsClean(container);
  });
});
