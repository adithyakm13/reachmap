export interface Entry {
  points: number;
  weight: number;
}

export function runContest(entries: Entry[]): number {
  return entries
    .filter((entry) => entry.weight > 0)
    .reduce((total, entry) => total + entry.points * entry.weight, 0);
}
