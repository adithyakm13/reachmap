import { Request, Response } from "express";
import { findOrders, findOrder, saveOrder, findLines, saveLine } from "../services/orders";
import { formatMoney } from "../lib/format";

export function listOrders(req: Request, res: Response) {
  res.json(findOrders());
}

export function getOrder(req: Request, res: Response) {
  const order = findOrder(req.params.id);
  res.json({ ...order, total: formatMoney(order.total) });
}

export function createOrder(req: Request, res: Response) {
  res.status(201).json(saveOrder(req.body));
}

export function listLines(req: Request, res: Response) {
  res.json(findLines(req.params.id));
}

export function addLine(req: Request, res: Response) {
  res.status(201).json(saveLine(req.params.id, req.body));
}
