import { formatDate, parseLegacyDate } from "./dates";

describe("dates", () => {
  it("formats an ISO date", () => {
    expect(formatDate("2024-03-05")).toBe("05/03/2024");
  });

  it("parses a legacy day/month/year date", () => {
    expect(parseLegacyDate("05/03/2024").getMonth()).toBe(2);
  });
});
