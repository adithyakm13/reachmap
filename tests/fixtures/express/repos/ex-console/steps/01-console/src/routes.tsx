import LedgerScreen from "./screens/LedgerScreen";
import EntryScreen from "./screens/EntryScreen";
import BalancesScreen from "./screens/BalancesScreen";

export const routes = (
  <>
    <Route path="/ledger" element={<LedgerScreen />} />
    <Route path="/ledger/:entryId" element={<EntryScreen />} />
    <Route path="/balances" element={<BalancesScreen />} />
  </>
);
