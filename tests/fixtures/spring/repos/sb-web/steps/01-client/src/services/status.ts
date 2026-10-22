import { apiGet } from "@/api/http";
import { STATUS_URL } from "@/api/endpoints";

export function fetchStatus() {
  return apiGet(STATUS_URL);
}
