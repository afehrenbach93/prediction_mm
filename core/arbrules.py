"""
Same-venue partition exhaustiveness screen (no LLM).

False "GO" on arb-scan came from quoting a *subset* of a large family
(e.g. 6 of ~149 MVP legs) and treating Σ asks < 1 as a lock. A partition
arb is only admissible when every sibling in the catalog census was booked.

This is the rules-exhaustiveness gate for Poly US slug families. It does NOT
verify resolution-source equivalence across venues — that is a later layer.
"""


def census_families(slugs: list[str]) -> dict[str, list[str]]:
    """family_key → sorted unique member slugs (from a catalog crawl)."""
    from core.arbscan import family_key
    fam: dict[str, list[str]] = {}
    for s in slugs or []:
        k = family_key(s)
        if not k:
            continue
        fam.setdefault(k, [])
        if s not in fam[k]:
            fam[k].append(s)
    return {k: sorted(v) for k, v in fam.items() if len(v) >= 2}


def family_complete(family: str, booked_legs: list[str],
                    census: dict[str, list[str]],
                    *, max_family_size: int = 12) -> tuple[bool, str]:
    """True only if census lists the family and every member was booked.

    Families larger than max_family_size are refused (award markets etc.) —
    we cannot afford a full book crawl and partial sums are lies.
    """
    expected = census.get(family) or []
    if len(expected) < 2:
        return False, "no_census"
    if len(expected) > max_family_size:
        return False, f"family_too_large({len(expected)}>{max_family_size})"
    missing = [s for s in expected if s not in set(booked_legs or [])]
    if missing:
        return False, f"incomplete({len(expected) - len(missing)}/{len(expected)})"
    return True, f"complete({len(expected)})"


def prioritize_complete_families(census: dict[str, list[str]],
                                 *, max_books: int = 100,
                                 max_family_size: int = 12,
                                 offset: int = 0) -> list[str]:
    """Order slugs so small complete families are fetched whole within budget."""
    items = [(k, v) for k, v in census.items() if 2 <= len(v) <= max_family_size]
    items.sort(key=lambda kv: (len(kv[1]), kv[0]))
    if items:
        off = int(offset) % len(items)
        items = items[off:] + items[:off]
    out: list[str] = []
    seen: set[str] = set()
    for _fam, members in items:
        for s in members:
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= max_books:
                return out
    return out


def go_kill(n_rules_ok: int, n_with_depth: int, median_edge: float | None,
            *, min_n: int = 30, min_depth_hits: int = 10,
            min_median_edge: float = 0.005) -> tuple[str, str]:
    """GO only on rules-complete partition/complement hits with depth."""
    if n_rules_ok < min_n:
        return ("WATCH",
                f"need ≥{min_n} rules-complete hits (have {n_rules_ok}) "
                f"— incomplete families do not count")
    if n_with_depth < min_depth_hits:
        return ("WATCH",
                f"need ≥{min_depth_hits} with depth>0 (have {n_with_depth})")
    if median_edge is None:
        return "INCONCLUSIVE", "missing median_edge"
    if median_edge < min_median_edge:
        return ("KILL",
                f"median_edge={median_edge:.4f} < {min_median_edge} "
                f"— fees/slip eat the lock")
    return ("GO",
            f"{n_rules_ok} rules-complete, {n_with_depth} with depth, "
            f"median_edge={median_edge:.4f}")
