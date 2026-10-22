import { Router } from "express";
import { listOrders, getOrder, createOrder, addLine, listLines } from "../controllers/orders";
import { requireAuth } from "../middleware/auth";

const router = Router();

router.get("/", listOrders);
router.get("/:id", requireAuth, getOrder);
router.post("/", requireAuth, createOrder);
router
  .route("/:id/lines")
  .get(listLines)
  .post(addLine);

export default router;
