// A unit test of a helper, not an end-to-end spec: no spec glob selects it.
import { openCases } from "./goto";

test("opens the cases", async () => {
  await openCases({ goto: async (_: string) => undefined });
});

async function stub(page: { goto: (u: string) => Promise<unknown> }) {
  await page.goto("/cases");
}
