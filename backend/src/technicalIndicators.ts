import { DailyPrice, TechnicalIndicators } from "./types";

export function ema(values: number[], period: number): Array<number | null> {
  const output: Array<number | null> = Array(values.length).fill(null);
  if (values.length < period) return output;
  const multiplier = 2 / (period + 1);
  let previous = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  output[period - 1] = previous;

  for (let index = period; index < values.length; index++) {
    previous = (values[index] - previous) * multiplier + previous;
    output[index] = previous;
  }
  return output;
}

export function rsi(values: number[], period = 14): Array<number | null> {
  const output: Array<number | null> = Array(values.length).fill(null);
  if (values.length <= period) return output;

  let gains = 0;
  let losses = 0;
  for (let index = 1; index <= period; index++) {
    const change = values[index] - values[index - 1];
    gains += Math.max(change, 0);
    losses += Math.max(-change, 0);
  }

  let avgGain = gains / period;
  let avgLoss = losses / period;
  output[period] = toRsi(avgGain, avgLoss);

  for (let index = period + 1; index < values.length; index++) {
    const change = values[index] - values[index - 1];
    avgGain = (avgGain * (period - 1) + Math.max(change, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-change, 0)) / period;
    output[index] = toRsi(avgGain, avgLoss);
  }

  return output;
}

export function calculateTechnicalIndicators(symbol: string, prices: DailyPrice[]): TechnicalIndicators {
  const closes = prices.map((price) => price.close).filter((value): value is number => value !== null);
  const highs = prices.map((price) => price.high).filter((value): value is number => value !== null);
  if (closes.length === 0) {
    return emptyTechnicals(symbol);
  }

  const ema20 = last(ema(closes, 20));
  const ema50 = last(ema(closes, 50));
  const ema200 = last(ema(closes, 200));
  const rsi14 = last(rsi(closes, 14));
  const current = closes[closes.length - 1];
  const high52 = Math.max(...highs.slice(-252));
  const return20d = closes.length > 20 ? (current / closes[closes.length - 21] - 1) * 100 : null;
  const distanceTo52WeekHigh = high52 > 0 ? (current / high52 - 1) * 100 : null;

  return { symbol, rsi14, ema20, ema50, ema200, return20d, distanceTo52WeekHigh };
}

function toRsi(avgGain: number, avgLoss: number): number {
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

function last(values: Array<number | null>): number | null {
  for (let index = values.length - 1; index >= 0; index--) {
    if (values[index] !== null) return values[index];
  }
  return null;
}

function emptyTechnicals(symbol: string): TechnicalIndicators {
  return {
    symbol,
    rsi14: null,
    ema20: null,
    ema50: null,
    ema200: null,
    return20d: null,
    distanceTo52WeekHigh: null,
  };
}
