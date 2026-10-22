import { Request, Response } from "express";

export function draft(req: Request, res: Response) {
  res.json({ draft: true, version: 2 });
}
