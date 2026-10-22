describe("billing", () => {
  it("opens the invoice list", () => {
    cy.visit("/billing/invoices");
    cy.contains("Invoices");
  });

  it("opens an invoice by id from the environment", () => {
    cy.visit(`/billing/invoices/${Cypress.env("INVOICE_ID")}`);
  });

  it("goes through a custom command first", () => {
    cy.login();
    cy.visit('/reports');
  });

  // cy.visit("/cases") was dropped from this suite
});
