import { Module } from "@nestjs/common";
import { LedgerController } from "./ledger.controller";
import { LedgerService } from "./ledger.service";
import { AuditLog } from "../audit/audit.log";

@Module({
  controllers: [LedgerController],
  providers: [LedgerService, AuditLog],
})
export class LedgerModule {}
const broken = `open;
