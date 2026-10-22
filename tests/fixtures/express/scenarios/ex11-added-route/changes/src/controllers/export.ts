import { Request, Response } from "express";
import { findOrders } from "../services/orders";

export function exportOrders(req: Request, res: Response) {
  res.type("text/csv").send(findOrders().map((o) => o.ref).join("\n"));
}
