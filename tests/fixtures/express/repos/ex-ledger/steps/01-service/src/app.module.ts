import { Module } from "@nestjs/common";
import { LedgerModule } from "./ledger/ledger.module";
import { BalancesModule } from "./balances/balances.module";

@Module({
  imports: [LedgerModule, BalancesModule],
})
export class AppModule {}
