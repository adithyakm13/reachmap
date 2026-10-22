import { describe, expect, it, vi } from "vitest";
import { ApiError, TOKEN_HEADER, createClient, isJobId, isReportName, takeToken } from "./client";

const TOKEN = "Zk3_-abcDEF0123456789abcdefABCDEF0123456789";

describe("takeToken", () => {
  it("reads the token from the fragment once and takes the fragment out of the address bar", () => {
    const replaceState = vi.fn();
    const token = takeToken({ hash: `#token=${TOKEN}`, pathname: "/", search: "" }, { replaceState });
    expect(token).toBe(TOKEN);
    expect(replaceState).toHaveBeenCalledTimes(1);
    expect(replaceState).toHaveBeenCalledWith(null, "", "/");
  });

  it("keeps the path and the query of the address", () => {
    const replaceState = vi.fn();
    takeToken({ hash: `#token=${TOKEN}`, pathname: "/reports/x", search: "?a=1" }, { replaceState });
    expect(replaceState).toHaveBeenCalledWith(null, "", "/reports/x?a=1");
  });

  it("finds the token among other fragment parts", () => {
    const replaceState = vi.fn();
    expect(takeToken({ hash: `#a=b&token=${TOKEN}`, pathname: "/", search: "" }, { replaceState })).toBe(TOKEN);
  });

  it("says there is none, and leaves the address alone, when there is no fragment", () => {
    const replaceState = vi.fn();
    expect(takeToken({ hash: "", pathname: "/", search: "" }, { replaceState })).toBeNull();
    expect(replaceState).not.toHaveBeenCalled();
  });

  it("does not take a token that is not made of the characters of a url-safe token", () => {
    const replaceState = vi.fn();
    expect(takeToken({ hash: "#token=a b", pathname: "/", search: "" }, { replaceState })).toBe("a");
    expect(takeToken({ hash: "#token=", pathname: "/", search: "" }, { replaceState })).toBeNull();
    expect(takeToken({ hash: "#xtoken=abc", pathname: "/", search: "" }, { replaceState })).toBeNull();
  });

  it("never stores the token", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem");
    takeToken({ hash: `#token=${TOKEN}`, pathname: "/", search: "" }, { replaceState: vi.fn() });
    createClient(TOKEN);
    expect(setItem).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});

function answer(status: number, body: unknown): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("the client", () => {
  it("calls only paths under /api/ of its own origin, with the token header and no credentials", async () => {
    const fetchImpl = vi.fn(async (_url: string, _init: RequestInit) => answer(200, { reports: [] }));
    const client = createClient(TOKEN, fetchImpl);
    await client.reports();
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/reports");
    expect((init.headers as Record<string, string>)[TOKEN_HEADER]).toBe(TOKEN);
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });

  it("sends JSON bodies with the JSON content type", async () => {
    const fetchImpl = vi.fn(async (_url: string, _init: RequestInit) => answer(201, { id: "x" }));
    const client = createClient(TOKEN, fetchImpl);
    await client.decide("acme-api-r1", { target: "a-0123456789abcdef", verdict: "skip", reason: "why", author: "Quinn" });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/reports/acme-api-r1/decisions");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ target: "a-0123456789abcdef", verdict: "skip", reason: "why", author: "Quinn" });
  });

  it("makes every request path from a checked name or id", async () => {
    const fetchImpl = vi.fn(async (_url: string, _init: RequestInit) => answer(200, {}));
    const client = createClient(TOKEN, fetchImpl);
    for (const bad of ["../x", "a/b", "http://evil.invalid/x", "", ".hidden", "a b", "x".repeat(300), "javascript:alert(1)"]) {
      await expect(client.report(bad)).rejects.toMatchObject({ status: 404 });
      await expect(client.decide(bad, { target: "a-0123456789abcdef", verdict: "test", reason: "", author: "Q" })).rejects.toBeInstanceOf(ApiError);
    }
    await expect(client.cancelJob("../jobs")).rejects.toBeInstanceOf(ApiError);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(isReportName("acme-api-compare-main-x-0123abcd-0123456789ab-r1")).toBe(true);
    expect(isJobId("0123456789ab")).toBe(true);
    expect(isJobId("0123456789AB")).toBe(false);
  });

  it("turns a refusal into an error that holds a status and fixed words, never the body", async () => {
    const hostile = "<img src=x onerror=alert(1)>";
    const fetchImpl = vi.fn(async () => answer(400, { error: "invalid_decision", field: "reason", message: hostile }));
    const client = createClient(TOKEN, fetchImpl);
    const error = await client.decide("r", { target: "a-0123456789abcdef", verdict: "skip", reason: "", author: "Q" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, code: "invalid_decision", field: "reason" });
    expect(JSON.stringify(error)).not.toContain("img");
    expect((error as Error).message).not.toContain("img");
  });

  it("drops a code or a field that is not one of the runner's fixed words", async () => {
    const fetchImpl = vi.fn(async () => answer(400, { error: "<script>x</script>", field: "A b" }));
    const error = await createClient(TOKEN, fetchImpl).jobs().catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 400, code: null, field: null });
  });

  it("says status 0 when the runner cannot be reached and 502 when an answer is not what was asked for", async () => {
    const down = createClient(TOKEN, vi.fn(async () => Promise.reject(new TypeError("boom"))));
    await expect(down.reports()).rejects.toMatchObject({ status: 0 });
    const odd = createClient(TOKEN, vi.fn(async () => answer(200, { nothing: true })));
    await expect(odd.reports()).rejects.toMatchObject({ status: 502 });
    await expect(odd.jobs()).rejects.toMatchObject({ status: 502 });
    await expect(odd.repos()).rejects.toMatchObject({ status: 502 });
    const notJson = createClient(TOKEN, vi.fn(async () => answer(200, "<html>")));
    await expect(notJson.reports()).rejects.toMatchObject({ status: 502 });
  });

  it("asks once: a refused job or decision is not sent again", async () => {
    const fetchImpl = vi.fn(async (_url: string, _init: RequestInit) => answer(400, { error: "invalid_job", field: "repo" }));
    const client = createClient(TOKEN, fetchImpl);
    await expect(client.submitJob({ repo: "acme/api", pr: "1" })).rejects.toMatchObject({ status: 400 });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    await expect(client.decide("r", { target: "a-0123456789abcdef", verdict: "test", reason: "", author: "Q" })).rejects.toMatchObject({ status: 400 });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const down = vi.fn(async () => Promise.reject(new TypeError("boom")));
    await expect(createClient(TOKEN, down).submitJob({ repo: "acme/api", pr: "1" })).rejects.toMatchObject({ status: 0 });
    expect(down).toHaveBeenCalledTimes(1);
  });

  it("starts and cancels jobs", async () => {
    const fetchImpl = vi.fn(async (_url: string, _init: RequestInit) => answer(202, { id: "0123456789ab", state: "queued" }));
    const client = createClient(TOKEN, fetchImpl);
    await client.submitJob({ repo: "acme/api", compare: "main...topic" });
    await client.cancelJob("0123456789ab");
    expect(fetchImpl.mock.calls.map(([u, i]) => `${i.method} ${u}`)).toEqual(["POST /api/jobs", "DELETE /api/jobs/0123456789ab"]);
  });
});
