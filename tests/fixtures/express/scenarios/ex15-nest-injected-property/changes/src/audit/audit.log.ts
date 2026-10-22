import { Injectable } from "@nestjs/common";

@Injectable()
export class AuditLog {
  record(action: string): void {
    console.log("audit", action, Date.now());
  }
}
