import type { NextApiRequest, NextApiResponse } from "next";
import { getOrder } from "@/lib/orders";

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === "GET") {
    res.status(200).json({ order: getOrder(String(req.query.id)) });
    return;
  }
  res.status(405).end();
}
