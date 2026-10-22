import { Module } from "@nestjs/common";
import { BalancesController } from "./balances.controller";

@Module({
  controllers: [BalancesController],
})
export class BalancesModule {}
