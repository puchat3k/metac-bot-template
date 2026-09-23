# Prediction Market Portfolio Canon v1

Status: ACTIVE PAPER-RESEARCH CONTRACT
Effective: 2026-09-23
Scope: Kalshi / Metaculus forecasting and prediction-market paper strategies
Live-money authority: NONE

## 1. Purpose

Treat prediction-market trading as an alternative-alpha / event-driven sleeve, not as a passive asset class and not as a guaranteed income stream.

The system objective is to determine which strategies produce durable net returns, with acceptable drawdown and enough executable capacity to matter at portfolio scale.

The lab must optimize for:

1. net return after realistic execution;
2. benchmark-relative return;
3. drawdown and recovery time;
4. calibration / Brier skill where a probability forecast exists;
5. liquidity and executable capacity;
6. diversification across events and categories;
7. low reliance on one regime or one source of edge;
8. prospective out-of-sample evidence.

No monthly return target is assumed or canonised.

## 2. Market-as-prior rule

Kalshi market probability is the default prior.

Independent model forecasts are overlays, not replacements. Large model-vs-market disagreement is not automatically treated as larger edge.

Reason:
- Kalshi's own 2026 calibration study reports strong aggregate calibration across more than 2.2 million resolved markets and sharply improving Brier scores near resolution.
- Forecast-combination and recalibration are established forecasting methods.
- Our first 80 resolved paper positions showed the generic model became unreliable at large disagreements and very short horizons.

Research:
- https://kalshi.com/research/publications/calibration
- https://www.sciencedirect.com/science/article/pii/S0169207010000075
- https://www.sciencedirect.com/science/article/pii/S016920701930158X

## 3. Current empirical hypothesis

PROSPECTIVE HYPOTHESIS, NOT ESTABLISHED EDGE:

A market-anchored forecast overlay may add value when:
- absolute raw model-market disagreement is modest;
- enough time remains before resolution;
- the model contributes information independent of the market;
- execution costs do not consume the edge.

The first resolved cohort suggested:
- <10% raw disagreement performed better than extreme disagreement;
- >20% raw disagreement was associated with poor results;
- positions opened inside two hours of close performed poorly.

These findings are in-sample discovery evidence only. They must be tested prospectively.

## 4. Challenger tournament

All arms remain paper-only.

### A. market-control-v1

Purpose:
- same-market benchmark;
- no independent forecast;
- one small paper observation on every covered market.

### B. kalshi-portfolio-v4

Purpose:
- current main market-anchored model;
- independent forecast shrunk toward the market prior;
- fractional-Kelly paper sizing;
- event-level gross/net caps;
- empirical downweighting of historically weak regimes.

### C. moderate-disagreement-shadow-v1

Prospective rule:
- raw absolute model-market disagreement between 5% and 10%;
- at least 12 hours to close;
- fixed-size shadow position;
- uses the current calibrated/shrunk direction;
- no training eligibility.

Purpose:
- directly test whether the first cohort's apparent green zone survives out of sample.

### D. anti-longshot-shadow-v1

Prospective rule:
- market probability <=10%: shadow-buy NO;
- market probability >=90%: shadow-buy YES;
- fixed-size paper position;
- no independent probability claim;
- no training eligibility.

Purpose:
- test whether a known favourite-longshot anomaly explains returns that might otherwise be credited to the model.

Research basis:
- CEPR analysis of more than 300,000 Kalshi contracts reports a clear favourite-longshot bias, with cheap contracts performing poorly and expensive contracts earning small positive returns.
- Makers performed materially better than takers in the same dataset.

Research:
- https://cepr.org/voxeu/columns/economics-kalshi-prediction-market
- https://cepr.org/publications/dp20631

### E. contrarian-shadow-v1

Purpose:
- test whether extreme model-market disagreement is an anti-signal;
- opposite direction to the main model for extreme raw disagreement;
- shadow only until enough prospective evidence accumulates.

### F. maker / liquidity-provider challenger

Status: REQUIRED NEXT IMPLEMENTATION, not yet equivalent to executable evidence.

Purpose:
- test the same forecast signals with maker-style entry rather than taker-style assumptions;
- measure fill probability, adverse selection, spread capture and inventory risk;
- do not fabricate fills without order-book evidence.

Research:
- Kalshi has formal market-maker / liquidity-provider programs.
- https://help.kalshi.com/en/articles/13823819-how-to-become-a-market-maker-on-kalshi

### G. combinatorial / cross-market arbitrage challenger

Status: REQUIRED NEXT IMPLEMENTATION, scanner first.

Purpose:
- identify logically inconsistent related contracts;
- measure executable depth and duration of opportunities;
- keep arbitrage P&L separate from directional forecasting P&L.

Research:
- published Polymarket research estimates substantial realized arbitrage profit historically, while capacity and execution constraints remain central.
- https://arxiv.org/abs/2508.03474

## 5. Benchmark contract

Every strategy must eventually be compared over the same calendar periods against:

1. Nasdaq-100 proxy;
2. S&P 500 proxy;
3. global-equity proxy;
4. balanced 80/20 equity-bond proxy;
5. short-duration cash / T-bill proxy;
6. prediction-market market-control baseline.

Report both:
- return on deployed capital;
- return contribution on total allocated sleeve capital.

A high-return strategy with tiny executable capacity must not be presented as a high-return portfolio strategy.

## 6. Capacity contract

For each paper strategy measure:

- gross paper notional;
- average simultaneous notional;
- executable bid/ask price where available;
- observed spread;
- available depth where available;
- turnover;
- capital utilization;
- event concentration;
- category concentration;
- estimated maximum deployable capital before edge degradation.

Midpoint-only P&L is research evidence, not live-equivalent P&L.

## 7. Risk contract

Track at minimum:

- cumulative net P&L;
- monthly return;
- maximum drawdown;
- recovery time;
- volatility;
- Sharpe;
- Sortino;
- worst month;
- percentage of profitable months;
- benchmark-relative excess return;
- correlation / beta to equity benchmarks;
- event and category concentration.

No strategy is considered portfolio-ready on win rate alone.

## 8. Promotion gates

Paper arms may be compared continuously, but no arm may be called established or live-ready merely because it is green.

Minimum evidence for a serious promotion review:

- prospective data only after the rule was frozen;
- at least 200 resolved positions;
- at least 50 distinct event groups;
- at least 3 materially different categories;
- realistic spread / fee / slippage assumptions;
- positive benchmark-relative result;
- drawdown within the declared risk budget;
- no single category responsible for the majority of P&L;
- evidence that plausible executable capacity is large enough to matter.

A live-money deployment remains a separate decision requiring explicit human approval and a separate capital-allocation contract.

## 9. Portfolio role

Prediction-market strategies may eventually qualify as a portfolio sleeve if they demonstrate:

- persistent net alpha after costs;
- acceptable drawdown;
- useful capacity;
- sufficiently low correlation with the core passive portfolio.

The strategy does not need to produce a fixed monthly paycheck. Accumulation, modest withdrawals and occasional drawdown are compatible with the portfolio objective.

## 10. Anti-overclaim rules

Never claim:
- guaranteed income;
- predictable monthly returns;
- an established 5-10% monthly return;
- live-equivalent profitability from midpoint paper P&L;
- diversification benefits before correlation is measured;
- scalability before executable capacity is measured.

Use:
- OBSERVED;
- PROSPECTIVE HYPOTHESIS;
- PAPER-VALIDATED;
- OUT-OF-SAMPLE VALIDATED;
- LIVE-ELIGIBLE;
as separate states.

## 11. Current implementation state

Deployed / existing:
- full-market paper baseline coverage;
- market-anchored calibrated primary model;
- event-level risk caps;
- independent settlement resolver;
- human-readable dashboard;
- contrarian extreme-disagreement shadow arm.

Added by v1 tournament:
- moderate-disagreement shadow arm;
- anti-longshot shadow benchmark.

Next:
- maker/execution simulator with real order-book depth;
- combinatorial-arbitrage scanner;
- same-period passive-market benchmark ingestion;
- strategy scoreboard including return, drawdown, capacity and benchmark-relative performance.
