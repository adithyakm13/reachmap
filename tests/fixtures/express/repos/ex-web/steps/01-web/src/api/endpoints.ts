export const ORDERS_URL = "/api/v1/orders";

export const orderUrl = (orderId: string) => `/api/v1/orders/${orderId}`;

export const customerOrdersUrl = (customerId: string) => `/api/v1/customers/${customerId}/orders`;

export const customerUrl = (customerId: string) => `/api/v1/customers/${customerId}`;

export const ADMIN_STATS_URL = "/api/v1/admin/stats";

export const INVOICES_URL = "/legacy/invoices";
