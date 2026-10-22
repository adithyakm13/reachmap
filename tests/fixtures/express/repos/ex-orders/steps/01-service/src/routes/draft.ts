import { Router } from "express";
import { draft } from "../controllers/draft";

export const draftRouter = Router();

draftRouter.get("/", draft);
