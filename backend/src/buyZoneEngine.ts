import { BuyZone, BuyZoneMethod, BuyZoneTranche } from "./types";

interface BaseBuyZoneInput {
  symbol: string;
  marginOfSafety?: number;
  targetPositionDollars?: number;
}

export interface EpsMultipleBuyZoneInput extends BaseBuyZoneInput {
  method: "eps";
  forwardEps: number | null;
  targetPe: number | null;
}

export interface FcfMultipleBuyZoneInput extends BaseBuyZoneInput {
  method: "fcf";
  forwardFcf: number | null;
  targetFcfMultiple: number | null;
  sharesOutstanding: number | null;
}

export interface RevenueMultipleBuyZoneInput extends BaseBuyZoneInput {
  method: "revenue";
  forwardRevenue: number | null;
  targetEvSales: number | null;
  netDebt: number | null;
  sharesOutstanding: number | null;
}

export type BuyZoneInput = EpsMultipleBuyZoneInput | FcfMultipleBuyZoneInput | RevenueMultipleBuyZoneInput;

const TRANCHE_WEIGHTS: Array<{ name: BuyZoneTranche["name"]; weight: number; level: keyof PriceLevels }> = [
  { name: "starter", weight: 0.25, level: "starterPrice" },
  { name: "normal", weight: 0.25, level: "normalBuyPrice" },
  { name: "heavy", weight: 0.3, level: "heavyBuyPrice" },
  { name: "panic", weight: 0.2, level: "panicBuyPrice" },
];

interface PriceLevels {
  starterPrice: number | null;
  normalBuyPrice: number | null;
  heavyBuyPrice: number | null;
  panicBuyPrice: number | null;
}

export function calculateFairValue(input: BuyZoneInput): number | null {
  switch (input.method) {
    case "eps":
      return multiplyIfAvailable(input.forwardEps, input.targetPe);
    case "fcf": {
      const equityValue = multiplyIfAvailable(input.forwardFcf, input.targetFcfMultiple);
      return perShare(equityValue, input.sharesOutstanding);
    }
    case "revenue": {
      const enterpriseValue = multiplyIfAvailable(input.forwardRevenue, input.targetEvSales);
      const equityValue = enterpriseValue !== null && input.netDebt !== null ? enterpriseValue - input.netDebt : null;
      return perShare(equityValue, input.sharesOutstanding);
    }
  }
}

export function buildBuyZone(input: BuyZoneInput): BuyZone {
  const fairValue = calculateFairValue(input);
  if (fairValue === null || fairValue <= 0) {
    return emptyBuyZone(input.symbol, input.method);
  }

  const marginOfSafety = input.marginOfSafety ?? 0;
  const adjustedFairValue = fairValue * (1 - marginOfSafety);
  const levels: PriceLevels = {
    starterPrice: adjustedFairValue * 0.95,
    normalBuyPrice: adjustedFairValue * 0.85,
    heavyBuyPrice: adjustedFairValue * 0.75,
    panicBuyPrice: adjustedFairValue * 0.65,
  };
  const tranches = buildTranches(levels, input.targetPositionDollars ?? null);
  const totalCost = sumNullable(tranches.map((tranche) => tranche.dollarAmount));
  const totalShares = sumNullable(tranches.map((tranche) => tranche.estimatedShares));

  return {
    symbol: input.symbol,
    method: input.method,
    fairValue,
    adjustedFairValue,
    ...levels,
    tranches,
    totalShares,
    weightedAverageCost: totalCost !== null && totalShares !== null && totalShares > 0 ? totalCost / totalShares : null,
    upsideToFairValue: levels.starterPrice !== null ? fairValue / levels.starterPrice - 1 : null,
    downsideToPanicPrice: levels.starterPrice !== null ? levels.panicBuyPrice! / levels.starterPrice - 1 : null,
  };
}

function buildTranches(levels: PriceLevels, targetPositionDollars: number | null): BuyZoneTranche[] {
  return TRANCHE_WEIGHTS.map((tranche) => {
    const buyPrice = levels[tranche.level];
    const dollarAmount =
      targetPositionDollars !== null && targetPositionDollars > 0 ? targetPositionDollars * tranche.weight : null;
    return {
      name: tranche.name,
      weight: tranche.weight,
      buyPrice,
      dollarAmount,
      estimatedShares: dollarAmount !== null && buyPrice !== null && buyPrice > 0 ? dollarAmount / buyPrice : null,
    };
  });
}

function emptyBuyZone(symbol: string, method: BuyZoneMethod): BuyZone {
  return {
    symbol,
    method,
    fairValue: null,
    adjustedFairValue: null,
    starterPrice: null,
    normalBuyPrice: null,
    heavyBuyPrice: null,
    panicBuyPrice: null,
    tranches: buildTranches(
      { starterPrice: null, normalBuyPrice: null, heavyBuyPrice: null, panicBuyPrice: null },
      null,
    ),
    totalShares: null,
    weightedAverageCost: null,
    upsideToFairValue: null,
    downsideToPanicPrice: null,
  };
}

function multiplyIfAvailable(left: number | null, right: number | null): number | null {
  return left !== null && right !== null ? left * right : null;
}

function perShare(value: number | null, sharesOutstanding: number | null): number | null {
  return value !== null && sharesOutstanding !== null && sharesOutstanding > 0 ? value / sharesOutstanding : null;
}

function sumNullable(values: Array<number | null>): number | null {
  if (values.some((value) => value === null)) return null;
  return values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
}
