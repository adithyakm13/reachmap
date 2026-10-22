// The checks a report must pass before the page builds a view from it: an object of schema 1 (the major version, an integer: a later
// major is refused, never read in part) with a known status and the parts the view relies on. The runner has already checked the file
// (`reachmap.report.parse`); this is the page's own guard, so a body that is not a report is a fixed message and never an exception.

import { SCHEMA, type Report } from "../api/types";
import { isDict } from "./read";

export class ReportShapeError extends Error {
  constructor() {
    super("not a report this page reads");
    this.name = "ReportShapeError";
  }
}

const STATUSES = ["completed", "failed", "timed_out"];

export function checkReport(data: unknown): Report {
  if (!isDict(data) || data.schema !== SCHEMA || typeof data.status !== "string" || !STATUSES.includes(data.status)) {
    throw new ReportShapeError();
  }
  if (!isDict(data.change) || !Array.isArray(data.incompleteness) || !Array.isArray(data.notes)) {
    throw new ReportShapeError();
  }
  if (data.status === "completed") {
    for (const key of ["findings", "areas", "branches", "propagations", "backend_calls", "suggestions"]) {
      if (!Array.isArray(data[key])) {
        throw new ReportShapeError();
      }
    }
  }
  return data as unknown as Report;
}
