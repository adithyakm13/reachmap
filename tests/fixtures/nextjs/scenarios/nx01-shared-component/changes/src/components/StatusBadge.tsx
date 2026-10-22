export type Status = "open" | "paid" | "cancelled";

export default function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge badge-${status}`} data-status={status}>{status}</span>;
}
