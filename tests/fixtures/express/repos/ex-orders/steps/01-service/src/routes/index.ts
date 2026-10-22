import { Router } from "express";
import { API_VERSION } from "../config";
import ordersRouter from "./orders";
import { customersRouter } from "./customers";
import * as admin from "./admin";
import reportsRouter from "./reports";

export const apiRouter = Router();

apiRouter.use(API_VERSION + "/orders", ordersRouter);
apiRouter.use(API_VERSION + "/customers", customersRouter);
apiRouter.use(`${API_VERSION}/admin`, admin.router);
apiRouter.use(API_VERSION + "/reports", reportsRouter);
