import { Param } from "@nestjs/common";
import { Controller, Get } from "./decorators";

@Controller("custom")
export class CustomController {
  @Get("ping")
  ping(@Param("who") who: string) {
    return "pong";
  }
}
