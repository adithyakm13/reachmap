export async function openCases(page: { goto: (url: string) => Promise<unknown> }) {
  await page.goto("/cases");
}
