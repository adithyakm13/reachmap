import { Request, Response } from "express";
import { findOrders } from "../services/orders";

export function search(req: Request, res: Response) {
  res.json(findOrders().filter((o) => o.ref === req.query.ref));
}
