import { Router } from "express";
import { stats } from "../controllers/admin";

export const router = Router();

router.get("/stats", stats);
