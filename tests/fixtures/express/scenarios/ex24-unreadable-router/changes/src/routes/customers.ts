import { Router } from "express";
import * as ctrl from "../controllers/customers";

export const customersRouter = Router({ mergeParams: true });
const broken = `open;

customersRouter.get("/", ctrl.list);
customersRouter.get("/:customerId", ctrl.get);
customersRouter.get("/:customerId/orders", (req, res) => {
  res.json(ctrl.ordersOf(req.params.customerId));
});
