import { connect } from "react-redux";

import type { RootState } from "@/store/types";

function InvoiceTableView({ rows }: { rows: { id: string; label: string }[] }) {
  return (
    <ul>
      {rows.map((row) => (
        <li key={row.id}>{row.label}</li>
      ))}
    </ul>
  );
}

const mapStateToProps = (state: RootState) => ({
  rows: state.invoices.rows,
});

export const InvoiceTable = connect(mapStateToProps)(InvoiceTableView);
