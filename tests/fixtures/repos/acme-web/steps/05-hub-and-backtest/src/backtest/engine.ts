export interface Trade {
  price: number;
  quantity: number;
}

export function runBacktest(trades: Trade[]): number {
  return trades.reduce((total, trade) => total + trade.price * trade.quantity, 0);
}
