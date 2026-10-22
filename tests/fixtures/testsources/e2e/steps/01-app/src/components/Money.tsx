export function Money({ cents }: { cents: number }) {
  return <span className="money">{(cents / 100).toFixed(2)}</span>;
}
