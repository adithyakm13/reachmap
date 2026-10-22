import { findOrder } from "../orders";

test("finds an order", () => {
  expect(findOrder("1").ref).toBe("A-1");
});
