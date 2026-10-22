import { createRouter } from "./eventbus";

const router = createRouter();

router.get("/ping", (msg) => msg.reply("pong"));

export default router;
