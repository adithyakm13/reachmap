import express from "express";
import { API_PREFIX } from "./config";
import { requestLog } from "./middleware/requestLog";
import { apiRouter } from "./routes";
import searchRouter from "./routes/search";
// import draftRouter from "./routes/draft";

const app = express();
app.use(express.json());
app.use(requestLog);

app.use(API_PREFIX, apiRouter);
app.use(API_PREFIX + "/search", searchRouter);
app.use("/legacy", require("./legacy"));
// app.use("/draft", draftRouter);

app.get("/health", (req, res) => {
  res.json({ ok: true });
});

export default app;
