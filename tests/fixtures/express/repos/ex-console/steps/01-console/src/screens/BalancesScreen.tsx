import { fetchBalances } from "@/services/ledger";

export default function BalancesScreen() {
  return <pre>{JSON.stringify(fetchBalances())}</pre>;
}
