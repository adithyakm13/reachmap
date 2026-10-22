import { Controller, Get } from "@nestjs/common";
import { AuditLog } from "./audit.log";

@Controller("audit")
export class AuditController {
  constructor(private readonly log: AuditLog) {}

  @Get()
  trail() {
    return this.log;
  }
}
