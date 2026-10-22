import { readSession } from "@/lib/session";

export function middleware() {
  readSession();
}
