import { Router } from "express";
import { search } from "../controllers/search";

const searchRouter = Router();

searchRouter.get("/", search);

export default searchRouter;
