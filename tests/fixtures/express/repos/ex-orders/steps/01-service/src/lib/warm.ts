import * as cache from "./cache";

function load() {
  return { warm: true };
}

cache.get("/orders/:id", load);
