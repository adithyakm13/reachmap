import { Request, Response } from "express";
import { countOrders } from "../services/orders";

export function stats(req: Request, res: Response) {
  res.json({ orders: countOrders() });
}
