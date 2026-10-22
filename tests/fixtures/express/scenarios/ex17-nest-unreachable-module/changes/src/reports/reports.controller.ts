import { Controller, Get } from "@nestjs/common";

@Controller("reports")
export class ReportsController {
  @Get("trial-balance")
  trialBalance() {
    return { debit: 0, credit: 0, asOf: "today" };
  }
}
