from .models import Stance

MIN_TRANSITION_PARTICIPANTS = 20


def summarize_stances(groups):
    final_counts = dict.fromkeys(Stance.values, 0)
    kept = 0
    transitions = []
    for group in groups:
        initial = group["initial_stance"]
        current = group["current_stance"]
        count = group["count"]
        final_counts[current] += count
        if initial == current:
            kept += count
        transitions.append({"from": initial, "to": current, "count": count})

    total = sum(final_counts.values())
    return {
        "total_participants": total,
        "final_pro": final_counts[Stance.PRO],
        "final_con": final_counts[Stance.CON],
        "final_neutral": final_counts[Stance.NEUTRAL],
        "stance_changed_count": total - kept,
        "stance_kept_count": kept,
        "sankey_data": transitions,
    }
