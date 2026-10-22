import { apiGet } from "@/api/http";
import { HEALTH_URL, STATUS_URL } from "@/api/endpoints";

export function ping() {
  return apiGet(HEALTH_URL);
}

export function fetchStatus() {
  return apiGet(STATUS_URL);
}
