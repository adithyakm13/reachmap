import { test, expect } from "@playwright/test";

const caseId = "CASE-1";
const note = { id: "N-9" };

test("case list", async ({ page }) => {
  await page.goto("/cases");
});

test("a new case is its own screen, not a case called new", async ({ page }) => {
  await page.goto("/cases/new");
});

test("case detail", async ({ page }) => {
  await page.goto(`/cases/${caseId}`);
});

test("a note of a case, with member expressions in the address", async ({ page }) => {
  await page.goto(`/cases/${caseId}/notes/${note.id}`);
});

test("an address held in a variable", async ({ page }) => {
  const url = process.env.START_URL ?? "/cases";
  await page.goto(url);
});

test("an address built by concatenation", async ({ page }) => {
  await page.goto("/cases/" + caseId);
});

test("an address that starts with a variable", async ({ page }) => {
  await page.goto(`${process.env.BASE_URL}/cases`);
});

/* The reports spec moved to Cypress:
   await page.goto("/reports");
*/
const help = 'await page.goto("/reports")';
test("not a visit: the call is inside a string", async () => {
  console.log(help);
});
