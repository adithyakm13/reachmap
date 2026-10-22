import { Request, Response } from "express";
import { invoicesFor, oneInvoice } from "../services/invoices";

export function listInvoices(req: Request, res: Response) {
  res.json(invoicesFor(req.query.customer));
}

export function getInvoice(req: Request, res: Response) {
  res.json({ invoice: oneInvoice(req.params.invoiceId) });
}
