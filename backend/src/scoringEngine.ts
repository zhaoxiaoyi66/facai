import { entryScoreFromValuation } from "./valuationEngine";
import {
  DashboardSections,
  GrowthMetrics,
  KeyMetricSnapshot,
  RatioSnapshot,
  RefreshJob,
  ResearchScores,
  StockResearchRow,
  TechnicalIndicators,
  ValuationMetrics,
} from "./types";
import { analyzeRisk } from "./riskEngine";

export function scoreStock(params: {
  symbol: string;
  ratios: RatioSnapshot | null;
  keyMetrics: KeyMetricSnapshot | null;
  valuation: ValuationMetrics;
  technicals: TechnicalIndicators;
  growth: GrowthMetrics | null;
}): ResearchScores {
  const quality = companyQualityScore(params.ratios, params.keyMetrics, params.growth);
  const entry = entryScoreFromValuation(params.valuation);
  const risk = analyzeRisk({
    technicals: params.technicals,
    valuation: params.valuation,
    ratios: params.ratios,
    growth: params.growth,
  });

  return {
    symbol: params.symbol,
    companyQualityScore: labelScore(quality.score, "company", quality.reasons),
    entryScore: labelScore(entry.score, "entry", entry.reasons),
    riskScore: { score: risk.score, label: risk.label, reasons: risk.flags },
    rating: ratingLabel(quality.score, entry.score, risk.score),
    antiFomoWarnings: risk.antiFomoWarnings,
  };
}

export function buildDashboardSections(rows: StockResearchRow[], refreshJobs: RefreshJob[]): DashboardSections {
  return {
    bestCurrentOpportunities: rows.filter((row) => row.qualityScore >= 70 && row.entryScore >= 70 && row.riskScore >= 60),
    highQualityNearBuyZones: rows.filter((row) => row.qualityScore >= 75 && row.entryScore >= 55),
    overheatedAvoidChasing: rows.filter((row) => row.antiFomoWarnings.length > 0),
    highGrowthHighRisk: rows.filter((row) => (row.revenueGrowth ?? 0) > 0.2 && row.riskScore < 50),
    cheapDeteriorating: rows.filter((row) => row.entryScore >= 65 && row.qualityScore < 50),
    watchlistByCategory: groupByCategory(rows),
    recentEarningsMovers: [],
    apiRefreshStatus: refreshJobs,
  };
}

function companyQualityScore(
  ratios: RatioSnapshot | null,
  keyMetrics: KeyMetricSnapshot | null,
  growth: GrowthMetrics | null,
): { score: number; reasons: string[] } {
  let score = 50;
  const reasons: string[] = [];

  if ((ratios?.operatingMargin ?? 0) >= 0.2) {
    score += 15;
    reasons.push("Strong operating margin.");
  }
  if ((ratios?.fcfMargin ?? 0) >= 0.15) {
    score += 15;
    reasons.push("Strong FCF margin.");
  }
  if ((keyMetrics?.roic ?? 0) >= 0.15) {
    score += 15;
    reasons.push("High ROIC.");
  }
  if ((growth?.revenueGrowth ?? 0) >= 0.15) {
    score += 10;
    reasons.push("Revenue growth is healthy.");
  }
  if (ratios?.fcfMargin !== null && ratios?.fcfMargin !== undefined && ratios.fcfMargin < 0) {
    score -= 25;
    reasons.push("FCF margin is negative.");
  }

  return { score: clamp(score), reasons };
}

function labelScore(score: number, axis: "company" | "entry", reasons: string[]) {
  const label =
    axis === "company"
      ? score >= 70
        ? "good company"
        : "bad company"
      : score >= 70
        ? "good price"
        : "bad price";
  return { score, label, reasons };
}

function ratingLabel(quality: number, entry: number, risk: number): string {
  if (quality >= 75 && entry >= 70 && risk >= 70) return "A - high-quality buy zone";
  if (quality >= 70 && entry >= 55 && risk >= 55) return "B - staged starter";
  if (quality >= 60 && risk >= 45) return "C - watchlist";
  if (risk < 40) return "D - high risk";
  return "C - watchlist";
}

function groupByCategory(rows: StockResearchRow[]): Record<string, StockResearchRow[]> {
  return rows.reduce<Record<string, StockResearchRow[]>>((groups, row) => {
    const category = row.category ?? "uncategorized";
    groups[category] = groups[category] ?? [];
    groups[category].push(row);
    return groups;
  }, {});
}

function clamp(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}
