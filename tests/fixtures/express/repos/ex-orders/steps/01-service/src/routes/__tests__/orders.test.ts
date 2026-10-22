import request from "supertest";
import app from "../../app";

test("lists orders", async () => {
  const res = await request(app).get("/api/v1/orders");
  expect(res.status).toBe(200);
});
