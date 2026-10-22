import { Router } from "express";
import { listOrders, getOrder, createOrder, addLine, listLines } from "../controllers/orders";
import { requireAuth } from "../middleware/auth";
import { exportOrders } from "../controllers/export";

const router = Router();

router.get("/", listOrders);
router.get("/export", exportOrders);
router.get("/:id", requireAuth, getOrder);
router.post("/", requireAuth, createOrder);
router
  .route("/:id/lines")
  .get(listLines)
  .post(addLine);

export default router;
