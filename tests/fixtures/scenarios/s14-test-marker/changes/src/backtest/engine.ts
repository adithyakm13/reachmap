export interface Trade {
  price: number;
  quantity: number;
}

export function runBacktest(trades: Trade[]): number {
  return trades
    .filter((trade) => trade.quantity > 0)
    .reduce((total, trade) => total + trade.price * trade.quantity, 0);
}
