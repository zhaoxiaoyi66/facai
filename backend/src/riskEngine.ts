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

  if ((params.technicals.rsi14 ?? 0) > 70) add("RSI 高于 70", 15);
  if ((params.technicals.distanceTo52WeekHigh ?? -100) > -5) add("价格距离 52 周高点不足 5%", 15);
  if ((params.technicals.return20d ?? 0) > 25) add("近 20 日涨幅超过 25%", 15);
  if ((params.valuation.evToFcf ?? 0) > 50) add("EV/FCF 高于 50", 15);
  if ((params.valuation.psVsHistoricalMedian ?? 0) > 1.5) add("PS 明显高于历史中位数", 10);
  if ((params.ratios?.fcfMargin ?? 0) < 0 || params.growth?.fcfMarginDeteriorating) {
    add("FCF 利润率为负或正在恶化", 15);
  }
  if (params.growth?.revenueGrowthSlowing && (params.valuation.psVsHistoricalMedian ?? 1) > 1.1) {
    add("收入增速放缓，但估值仍在扩张", 15);
  }

  const score = Math.max(0, 100 - riskPenalty);
  return {
    score,
    label: score >= 75 ? "低风险" : score >= 50 ? "中等风险" : "高风险",
    flags,
    antiFomoWarnings: flags.length > 0 ? [ANTI_FOMO_WARNING] : [],
  };

  function add(reason: string, penalty: number): void {
    flags.push(reason);
    riskPenalty += penalty;
  }
}
