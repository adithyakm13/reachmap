import { downloadStatement } from "@/api/generated/client";

export default function StatementsPage() {
  const download = () => downloadStatement("2024-01");
  return <button onClick={download}>Download</button>;
}
