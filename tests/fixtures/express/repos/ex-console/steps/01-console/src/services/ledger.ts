import { apiGet, apiPost, apiDelete } from "@/api/http";
import { ENTRIES_URL, entryUrl, BALANCES_URL } from "@/api/endpoints";

export function fetchEntries() {
  return apiGet(ENTRIES_URL);
}

export function fetchEntry(entryId: string) {
  return apiGet(entryUrl(entryId));
}

export function addEntry(entry: unknown) {
  return apiPost(ENTRIES_URL, entry);
}

export function removeEntry(entryId: string) {
  return apiDelete(entryUrl(entryId));
}

export function fetchBalances() {
  return apiGet(BALANCES_URL);
}
