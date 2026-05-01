def _naam_tokens(naam):
    return {t.lower() for t in naam.split() if len(t) > 2}


def matches_reviewed(candidate_naam, reviewed_names):
    candidate_tokens = _naam_tokens(candidate_naam)
    if not candidate_tokens:
        return None
    for reviewer_naam in reviewed_names:
        if candidate_tokens & _naam_tokens(reviewer_naam):
            return reviewer_naam
    return None


def deduplicate(rows):
    email_groups = {}
    for row in rows:
        key = row['email'].lower()
        email_groups.setdefault(key, []).append(row)

    candidates, dubbel = [], []
    for email, group in email_groups.items():
        if len(group) == 1:
            candidates.append(group[0])
        else:
            sorted_group = sorted(
                group,
                key=lambda r: r['datum_consult'] or '0000-00-00',
                reverse=True,
            )
            candidates.append(sorted_group[0])
            for dup in sorted_group[1:]:
                dubbel.append({
                    **dup,
                    'reden': f'Duplicaat — zelfde e-mail als {sorted_group[0]["naam"]}',
                })

    return candidates, {'dubbel': dubbel}
