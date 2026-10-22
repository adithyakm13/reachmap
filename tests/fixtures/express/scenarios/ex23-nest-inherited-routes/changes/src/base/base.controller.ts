import { Get } from "@nestjs/common";

export abstract class BaseController {
  @Get("health")
  health() {
    return "ok";
  }
}
