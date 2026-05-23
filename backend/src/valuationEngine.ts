import { KeyMetricSnapshot, RatioSnapshot, ValuationMetrics } from "./types";

export function calculateValuationMetrics(
  symbol: string,
  ratios: RatioSnapshot | null,
  keyMetrics: KeyMetricSnapshot | null,
  historicalPsMedian: number | null,
): ValuationMetrics {
  const ps = ratios?.ps ?? null;
  return {
    symbol,
    pe: ratios?.pe ?? null,
    ps,
    evToFcf: keyMetrics?.evToFcf ?? null,
    evToSales: keyMetrics?.evToSales ?? null,
    fcfYield: keyMetrics?.fcfYield ?? null,
    psVsHistoricalMedian: ps !== null && historicalPsMedian && historicalPsMedian > 0 ? ps / historicalPsMedian : null,
  };
}

export function entryScoreFromValuation(metrics: ValuationMetrics): { score: number; reasons: string[] } {
  let score = 50;
  const reasons: string[] = [];

  if (metrics.evToFcf !== null) {
    if (metrics.evToFcf <= 25) {
      score += 20;
      reasons.push("EV/FCF is reasonable.");
    } else if (metrics.evToFcf > 50) {
      score -= 20;
      reasons.push("EV/FCF is above 50.");
    }
  }

  if (metrics.fcfYield !== null) {
    if (metrics.fcfYield >= 0.04) score += 15;
    if (metrics.fcfYield < 0.015) score -= 15;
  }

  if (metrics.psVsHistoricalMedian !== null && metrics.psVsHistoricalMedian > 1.5) {
    score -= 15;
    reasons.push("PS is far above historical median.");
  }

  return { score: clamp(score), reasons };
}

function clamp(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}
