import { regionProcessingBasis, regionTypeLabel } from "./regionTypes.js";

export default function RegionTypeBadge({ region }) {
  return <span className={`region-type-badge ${regionProcessingBasis(region) || "unknown"}`} aria-label={`처리 기준: ${regionTypeLabel(region)}`}>{regionTypeLabel(region)}</span>;
}
