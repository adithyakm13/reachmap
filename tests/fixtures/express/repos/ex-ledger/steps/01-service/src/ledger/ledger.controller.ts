import { Body, Controller, Delete, Get, Param, Post } from "@nestjs/common";
import { LedgerService } from "./ledger.service";
import { ENTRIES_PATH } from "./entries.constants";
import { EntryDto } from "./entry.dto";
import { AuditLog } from "../audit/audit.log";

@Controller(ENTRIES_PATH)
export class LedgerController {
  constructor(
    private readonly ledger: LedgerService,
    private readonly audit: AuditLog,
  ) {}

  @Get()
  list() {
    return this.ledger.all();
  }

  @Get(":id")
  findOne(@Param("id") id: string) {
    return this.ledger.find(id);
  }

  @Post()
  create(@Body() dto: EntryDto) {
    this.audit.record("create");
    return this.ledger.add(dto);
  }

  @Delete(":id")
  remove(@Param("id") id: string) {
    this.audit.record("remove");
    return this.ledger.remove(id);
  }
}
