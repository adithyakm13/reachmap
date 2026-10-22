import { Router } from "express";
import * as ctrl from "../controllers/customers";

export const customersRouter = Router({ mergeParams: true });

customersRouter.get("/", ctrl.list);
customersRouter.get("/:customerId", ctrl.get);
