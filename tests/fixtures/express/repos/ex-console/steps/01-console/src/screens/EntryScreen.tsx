import { fetchEntry, removeEntry } from "@/services/ledger";

export default function EntryScreen({ entryId }: { entryId: string }) {
  return (
    <div>
      <pre>{JSON.stringify(fetchEntry(entryId))}</pre>
      <button onClick={() => removeEntry(entryId)}>Remove</button>
    </div>
  );
}
