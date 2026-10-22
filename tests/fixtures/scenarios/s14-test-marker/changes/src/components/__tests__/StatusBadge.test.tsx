import { render, screen } from "@testing-library/react";

import { StatusBadge } from "../StatusBadge";

describe("StatusBadge", () => {
  it("renders the status text", () => {
    render(<StatusBadge status="paid" />);
    expect(screen.getByText("paid")).toBeTruthy();
  });

  it("renders an overdue status", () => {
    render(<StatusBadge status="overdue" />);
    expect(screen.getByText("overdue")).toBeTruthy();
  });
});
