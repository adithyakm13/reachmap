import { test, expect } from "@playwright/test";

// page.goto("/billing/settlements") was the old settlement address, kept here as a note.
test.describe("billing", () => {
  test("invoice list shows totals", async ({ page }) => {
    await page.goto("/billing/invoices");
    await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
  });

  test("invoice detail by id", async ({ page }) => {
    const invoiceId = "INV-100";
    await page.goto(`/billing/invoices/${invoiceId}`);
  });

  test("a literal invoice id with a query and a fragment", async ({ page }) => {
    await page.goto('/billing/invoices/INV-7?tab=audit#top');
  });

  test("settlements by full address", async ({ page }) => {
    await page.goto("http://localhost:5173/billing/settlements", { waitUntil: "networkidle" });
  });
});
