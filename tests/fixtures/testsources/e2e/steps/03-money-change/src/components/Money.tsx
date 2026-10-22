export function Money({ cents }: { cents: number }) {
  return <span className="money">{(Math.round(cents) / 100).toFixed(2)}</span>;
}
