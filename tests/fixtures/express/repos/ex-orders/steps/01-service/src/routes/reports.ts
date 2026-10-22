import { Router } from "express";
import { daily, monthly } from "../controllers/reports";
import { dailyPath } from "../lib/paths";

const router = Router();

router.get(dailyPath(), daily);
router.get("/monthly", monthly);

export default router;
