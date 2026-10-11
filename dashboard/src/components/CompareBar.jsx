import { formatMoney, formatPercent, improvement } from "../lib/format";

/** Production vs challenger error as two proportional bars (shorter = more accurate). */
export function CompareBar({ productionMae, challengerMae, productionVersion, challengerVersion }) {
  if (productionMae == null || challengerMae == null) return null;
  const max = Math.max(productionMae, challengerMae) || 1;
  const gain = improvement(productionMae, challengerMae);
  const better = challengerMae < productionMae;
  const summary =
    gain == null
      ? ""
      : better
        ? `Challenger error is ${formatPercent(gain)} lower than production`
        : `Challenger error is ${formatPercent(-gain)} higher than production`;

  return (
    <div className="compare" role="group" aria-label={`Mean absolute error: production ${formatMoney(productionMae)}, challenger ${formatMoney(challengerMae)}. ${summary}.`}>
      <div className="compare-row">
        <span>Production {productionVersion}</span>
        <div className="compare-track"><div className="compare-fill" style={{ width: `${(productionMae / max) * 100}%` }} /></div>
        <span className="num mono">{formatMoney(productionMae)}</span>
      </div>
      <div className="compare-row">
        <span>Challenger {challengerVersion}</span>
        <div className="compare-track"><div className={`compare-fill ${better ? "better" : ""}`} style={{ width: `${(challengerMae / max) * 100}%` }} /></div>
        <span className="num mono">{formatMoney(challengerMae)}</span>
      </div>
      <div className="compare-caption">{summary} (mean absolute error on paired live requests).</div>
    </div>
  );
}
