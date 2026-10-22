import { Request, Response } from "express";
import { dailyTotals, monthlyTotals } from "../services/reports";

export function daily(req: Request, res: Response) {
  res.json(dailyTotals());
}

export function monthly(req: Request, res: Response) {
  res.json(monthlyTotals());
}
