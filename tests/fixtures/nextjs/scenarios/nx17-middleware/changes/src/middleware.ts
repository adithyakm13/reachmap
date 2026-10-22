import { readSession } from "@/lib/session";

export function middleware() {
  return readSession() ?? null;
}
