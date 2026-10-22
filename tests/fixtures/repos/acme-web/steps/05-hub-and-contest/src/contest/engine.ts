export interface Entry {
  points: number;
  weight: number;
}

export function runContest(entries: Entry[]): number {
  return entries.reduce((total, entry) => total + entry.points * entry.weight, 0);
}
