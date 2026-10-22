import { fetchEntries, addEntry } from "@/services/ledger";

export default function LedgerScreen() {
  return (
    <div>
      <pre>{JSON.stringify(fetchEntries())}</pre>
      <button onClick={() => addEntry({ account: "cash", amount: 1 })}>Add</button>
    </div>
  );
}
