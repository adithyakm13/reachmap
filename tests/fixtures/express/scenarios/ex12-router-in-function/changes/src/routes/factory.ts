import { Router } from "express";
import { search } from "../controllers/search";

export function buildRouter() {
  const router = Router();
  router.get("/", search);
  return router;
}
