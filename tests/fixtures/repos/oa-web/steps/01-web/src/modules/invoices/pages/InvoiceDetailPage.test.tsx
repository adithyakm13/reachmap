import { getInvoice } from "@/api/generated/client";

test("the client has the operation", () => {
  expect(typeof getInvoice).toBe("function");
});
