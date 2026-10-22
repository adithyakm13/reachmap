import { ping, fetchStatus } from "@/services/health";

export default function StatusScreen() {
  return <pre>{JSON.stringify([ping(), fetchStatus()])}</pre>;
}
