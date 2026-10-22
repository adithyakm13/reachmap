describe("misc", () => {
  it("builds the address from the config", () => {
    cy.visit(Cypress.config("baseUrl") + "/cases");
  });

  it("passes an options object", () => {
    cy.visit({ url: "/cases", method: "GET" });
  });

  it("opens the settlements and then a case", () => {
    cy.visit("/billing/settlements");
    cy.visit("/cases/CASE-2");
  });
});
