type Props = { status: "open" | "paid" | "overdue" };

export function StatusBadge({ status }: Props) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}
