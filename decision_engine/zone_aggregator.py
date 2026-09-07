import logging
from typing import Dict, List, Optional
from config import config

logger = logging.getLogger(__name__)

class ZoneAggregator:
    """
    Zone Aggregator Engine.
    Executes:
    1. Strict Intersection Math on candidate entry zones.
    2. Source Weighting & Confidence Score Calculation (Threshold >= 66%).
    3. Multi-Directional / Hedging handling (Allows independent BUY & SELL scalp/trend setups).
    """

    @staticmethod
    def calculate_strict_intersection(zones: List[Dict]) -> Optional[Dict]:
        """
        Computes strict mathematical intersection of N entry zones.
        Each dict in zones must have: 'zone_min', 'zone_max'.
        Returns {'zone_min': float, 'zone_max': float} or None if empty intersection.
        """
        if not zones:
            return None

        max_of_mins = max(z['zone_min'] for z in zones)
        min_of_maxs = min(z['zone_max'] for z in zones)

        if max_of_mins <= min_of_maxs:
            return {
                "zone_min": round(max_of_mins, 2),
                "zone_max": round(min_of_maxs, 2)
            }
        else:
            # Empty intersection (zones do not overlap)
            return None

    def aggregate_signals(self, active_signals: List[Dict], channel_weights: Dict[str, float] = None) -> List[Dict]:
        """
        Groups active signals by direction (BUY vs SELL) and computes consensus setups.
        """
        if channel_weights is None:
            channel_weights = {}

        validated_setups = []

        for direction in ["BUY", "SELL"]:
            matching_signals = [s for s in active_signals if s.get("direction") == direction]
            if not matching_signals:
                continue

            # Check strict intersection of zones
            zones = [{"zone_min": s["zone_min"], "zone_max": s["zone_max"]} for s in matching_signals]
            intersection_zone = self.calculate_strict_intersection(zones)

            if not intersection_zone:
                logger.info(f"Skipping {direction} signals: zones do not overlap strictly.")
                continue

            # Calculate source confidence score
            total_weight = 0.0
            agreeing_weight = 0.0
            sources_used = []

            for s in matching_signals:
                source_name = s.get("source_name", "UNKNOWN")
                source_type = s.get("source_type", "CHANNEL")

                if source_type == "USER":
                    weight = config.WEIGHT_USER
                else:
                    weight = channel_weights.get(source_name, config.WEIGHT_CHANNEL_DEFAULT)

                agreeing_weight += weight
                sources_used.append({"source": source_name, "weight": weight, "type": source_type})

            # Base maximum theoretical weight reference (User + top 2 channels)
            max_reference_weight = config.WEIGHT_USER + (config.WEIGHT_CHANNEL_DEFAULT * 2)
            confidence_score = round(min(100.0, (agreeing_weight / max_reference_weight) * 100), 1)

            if confidence_score >= config.CONFIDENCE_THRESHOLD:
                # Compute safest SL
                valid_sls = [s["sl"] for s in matching_signals if s.get("sl") is not None]
                if valid_sls:
                    final_sl = min(valid_sls) if direction == "BUY" else max(valid_sls)
                else:
                    # Default 50 pips SL if unspecified
                    entry_mid = (intersection_zone["zone_min"] + intersection_zone["zone_max"]) / 2
                    final_sl = entry_mid - 5.0 if direction == "BUY" else entry_mid + 5.0

                # Compute 1:3 RR TP
                entry_mid = (intersection_zone["zone_min"] + intersection_zone["zone_max"]) / 2
                sl_distance = abs(entry_mid - final_sl)
                tp_distance = sl_distance * config.TARGET_RR
                
                final_tp = entry_mid + tp_distance if direction == "BUY" else entry_mid - tp_distance

                validated_setups.append({
                    "direction": direction,
                    "zone_min": intersection_zone["zone_min"],
                    "zone_max": intersection_zone["zone_max"],
                    "entry_target": round(entry_mid, 2),
                    "sl": round(final_sl, 2),
                    "tp": round(final_tp, 2),
                    "rr": config.TARGET_RR,
                    "confidence_score": confidence_score,
                    "sources": sources_used
                })

        return validated_setups

zone_aggregator = ZoneAggregator()
