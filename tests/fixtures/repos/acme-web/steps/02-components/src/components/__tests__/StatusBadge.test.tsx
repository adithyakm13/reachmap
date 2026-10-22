import { render, screen } from "@testing-library/react";

import { StatusBadge } from "../StatusBadge";

describe("StatusBadge", () => {
  it("renders the status text", () => {
    render(<StatusBadge status="paid" />);
    expect(screen.getByText("paid")).toBeTruthy();
  });
});
