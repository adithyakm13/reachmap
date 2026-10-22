import { fetchStatus } from "@/services/status";

export default function StatusScreen() {
  return <pre>{JSON.stringify(fetchStatus())}</pre>;
}
