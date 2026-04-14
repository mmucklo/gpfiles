/**
 * Parses the /api/metrics/signals/breakdown response into a flat { [modelName]: count } map.
 *
 * Phase 14 shape:
 *   { attribution: { by_model: { "30d": { "MODEL_NAME": { count, avg_confidence, avg_selection_score } } } } }
 *
 * Pre-Phase-14 shape (no longer produced but guarded against to avoid crashes):
 *   { SENTIMENT: 123, MACRO: 45, ... }
 *
 * Returns an empty object if the shape is unrecognised.
 */
export function parseBreakdownByModel(
    breakdown: unknown,
    window = '30d',
): Record<string, number> {
    if (!breakdown || typeof breakdown !== 'object') return {};

    const b = breakdown as Record<string, unknown>;
    const attribution = b['attribution'];

    if (!attribution || typeof attribution !== 'object') return {};

    const byModel = (attribution as Record<string, unknown>)['by_model'];
    if (!byModel || typeof byModel !== 'object') {
        console.warn('[SIGNALPANEL-UNEXPECTED-SHAPE] attribution present but by_model missing; keys=', Object.keys(attribution as object));
        return {};
    }

    const windowData = (byModel as Record<string, unknown>)[window];
    if (!windowData || typeof windowData !== 'object') {
        console.warn('[SIGNALPANEL-UNEXPECTED-SHAPE] attribution present but by_model.' + window + ' missing; by_model keys=', Object.keys(byModel as object));
        return {};
    }

    const result: Record<string, number> = {};
    for (const [modelName, stats] of Object.entries(windowData as Record<string, unknown>)) {
        const count =
            stats && typeof stats === 'object' && typeof (stats as Record<string, unknown>)['count'] === 'number'
                ? (stats as Record<string, unknown>)['count'] as number
                : 0;
        result[modelName] = count;
    }
    return result;
}
