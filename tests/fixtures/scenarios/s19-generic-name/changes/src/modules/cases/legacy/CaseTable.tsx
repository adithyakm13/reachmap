import { connect } from "react-redux";

import type { RootState } from "@/store/types";

function CaseTableView({ rows }: { rows: { id: string; label: string }[] }) {
  return (
    <ul>
      {rows.map((row) => (
        <li key={row.id}>{row.label}</li>
      ))}
    </ul>
  );
}

const mapStateToProps = (state: RootState) => ({
  rows: state.cases.rows.filter((row) => row.label !== ""),
});

export const CaseTable = connect(mapStateToProps)(CaseTableView);
