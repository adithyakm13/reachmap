// Money formatting for the whole app. The legacy mode keeps the old
// comma-decimal output that exported statements still use.
export function formatAmount(value: number, mode: "legacy" | "modern"): string {
  if (mode === "legacy") {
    return value.toFixed(2).replace(".", ",").padStart(10, " ");
  }
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" }).format(value);
}
