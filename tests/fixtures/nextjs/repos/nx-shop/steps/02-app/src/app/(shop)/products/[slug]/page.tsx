import StatusBadge from "../../../../components/StatusBadge";
import { formatPrice } from "@/lib/format";

export default function ProductPage({ params }: { params: { slug: string } }) {
  return (
    <div>
      <h1>{params.slug}</h1>
      <p>{formatPrice(1200)}</p>
      <StatusBadge status="paid" />
    </div>
  );
}
