import { ANTI_FOMO_WARNING } from "./config";
import { GrowthMetrics, RatioSnapshot, TechnicalIndicators, ValuationMetrics } from "./types";

export interface RiskAnalysis {
  score: number;
  label: string;
  flags: string[];
  antiFomoWarnings: string[];
}

export function analyzeRisk(params: {
  technicals: TechnicalIndicators;
  valuation: ValuationMetrics;
  ratios: RatioSnapshot | null;
  growth: GrowthMetrics | null;
}): RiskAnalysis {
  let riskPenalty = 0;
  const flags: string[] = [];

  if ((params.technicals.rsi14 ?? 0) > 70) add("RSI > 70", 15);
  if ((params.technicals.distanceTo52WeekHigh ?? -100) > -5) add("Price is within 5% of 52-week high", 15);
  if ((params.technicals.return20d ?? 0) > 25) add("Past 20-day return > 25%", 15);
  if ((params.valuation.evToFcf ?? 0) > 50) add("EV/FCF > 50", 15);
  if ((params.valuation.psVsHistoricalMedian ?? 0) > 1.5) add("PS is far above historical median", 10);
  if ((params.ratios?.fcfMargin ?? 0) < 0 || params.growth?.fcfMarginDeteriorating) {
    add("FCF margin is negative or deteriorating", 15);
  }
  if (params.growth?.revenueGrowthSlowing && (params.valuation.psVsHistoricalMedian ?? 1) > 1.1) {
    add("Revenue growth is slowing while valuation is expanding", 15);
  }

  const score = Math.max(0, 100 - riskPenalty);
  return {
    score,
    label: score >= 75 ? "low risk" : score >= 50 ? "medium risk" : "high risk",
    flags,
    antiFomoWarnings: flags.length > 0 ? [ANTI_FOMO_WARNING] : [],
  };

  function add(reason: string, penalty: number): void {
    flags.push(reason);
    riskPenalty += penalty;
  }
}
