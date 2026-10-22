// Generated from api/openapi.json. Do not edit by hand.

const BASE_PATH = "/v1";

async function request(method: string, path: string, body?: unknown): Promise<unknown> {
  const response = await fetch(BASE_PATH + path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
  return response.json();
}

export function listInvoices() {
  return request("GET", "/invoices");
}

export function createInvoice(body: unknown) {
  return request("POST", "/invoices", body);
}

export function getInvoice(invoiceId: string) {
  return request("GET", `/invoices/${invoiceId}`);
}

export function voidInvoice(invoiceId: string) {
  return request("DELETE", `/invoices/${invoiceId}`);
}

export function getCustomer(customerId: string) {
  return request("GET", `/customers/${customerId}`);
}

export function downloadStatement(statementId: string) {
  return request("GET", `/statements/${statementId}/download`);
}

export function get() {
  return request("GET", "/ping");
}

export function list() {
  return request("GET", "/reports");
}
