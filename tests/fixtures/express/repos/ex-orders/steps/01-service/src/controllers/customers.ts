import { Request, Response } from "express";
import { allCustomers, oneCustomer, ordersFor } from "../services/customers";

export function list(req: Request, res: Response) {
  res.json(allCustomers());
}

export function get(req: Request, res: Response) {
  res.json(oneCustomer(req.params.customerId));
}

export function ordersOf(customerId: string) {
  return ordersFor(customerId);
}
