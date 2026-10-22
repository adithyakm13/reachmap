import { downloadStatement } from "@/api/generated/client";

export default function StatementsPage() {
  const download = () => downloadStatement("2024-02");
  return <button onClick={download}>Download</button>;
}
