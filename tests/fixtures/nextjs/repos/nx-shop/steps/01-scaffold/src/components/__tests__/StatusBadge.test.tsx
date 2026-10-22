import StatusBadge from "../StatusBadge";

test("renders the status", () => {
  expect(StatusBadge({ status: "open" })).toBeTruthy();
});
