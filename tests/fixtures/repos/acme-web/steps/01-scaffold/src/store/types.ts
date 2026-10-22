export interface Row {
  id: string;
  label: string;
}

export interface RootState {
  invoices: { rows: Row[] };
  cases: { rows: Row[] };
  reports: { rows: Row[] };
  lists: { rows: Row[] };
}
