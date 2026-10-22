import { Controller, Get, Param } from "@nestjs/common";

@Controller({ path: "balances", version: "1" })
export class BalancesController {
  @Get()
  all() {
    return [];
  }

  @Get(":account")
  one(@Param("account") account: string) {
    return { account, balance: 0 };
  }
}
