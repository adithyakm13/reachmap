import { Injectable } from "@nestjs/common";
import { EntryDto } from "./entry.dto";

@Injectable()
export class LedgerService {
  private entries: EntryDto[] = [];

  all(): EntryDto[] {
    return this.entries;
  }

  find(id: string): EntryDto | undefined {
    return this.entries[Number(id)];
  }

  add(dto: EntryDto): EntryDto {
    this.entries.push(dto);
    return dto;
  }

  remove(id: string): boolean {
    return this.entries.splice(Number(id), 1).length > 0;
  }
}
