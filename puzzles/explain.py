"""Grounded move explanations ("why is that the best move?") — EXPLAIN_SPEC.

Hard separation, enforced by construction:

    move_facts(state, action, actor) -> Facts     # exact, numeric, testable
    render(facts_best, facts_alt, phase) -> str | None   # words, nothing else

No search is needed: every fact is a cheap diff of position features
computed by functions that already exist for other purposes (heuristic
scoring, static evaluation, legal-move generation) — see `agents/heuristic.py`
and `search/value.py`. `render` cites ONLY numbers/names that are leaf
values inside the facts dicts it was given, and returns None (never vague
prose) when nothing about the move clears a salience bar — the caller
falls back to its own win-prob line.
"""
from __future__ import annotations

from engine import (
    Action, ActionType, Building, GameState, PortType, TOPOLOGY, apply_action,
)
from engine.longest_road import longest_road_length
from engine.rules import _legal_settlement_vertices
from engine.types import COST_CITY, COST_SETTLEMENT, DISCARD_THRESHOLD, Resource

from agents.heuristic import PIP, _resource_pips

_RENDER_CAP = 220


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------


def move_facts(state: GameState, action: Action, actor: int) -> dict:
    """Exact, numeric diff between `state` and `state` with `action` applied,
    from `actor`'s point of view. Never touches search — every field is a
    cheap closed-form computation over the (perfect, server-side) state."""
    after = state.clone()
    apply_action(after, action)

    facts: dict = {
        "action_type": action.type.name,
        "race": _race_facts(state, after, actor),
        "economy": _economy_facts(after, actor),
    }
    t = action.type
    if t in (
        ActionType.SETUP_PLACE_SETTLEMENT, ActionType.BUILD_SETTLEMENT, ActionType.BUILD_CITY,
    ):
        facts.update(_placement_facts(state, action))
    if t is ActionType.BUILD_ROAD:
        facts["opens"] = _road_opens_facts(state, action)
    if t is ActionType.BUILD_SETTLEMENT:
        facts.update(_denies_spot_facts(state, action, actor))
    if t is ActionType.MOVE_ROBBER:
        facts["robber"] = _robber_facts(state, action, actor)
    if t in (ActionType.TRADE_BANK, ActionType.PLAY_YEAR_OF_PLENTY):
        facts["enables_now"] = _enables_now_facts(state, action)
    if t is ActionType.BUY_DEV_CARD:
        facts["dev_deck"] = _dev_deck_facts(state)
    return facts


def _robber_adjusted_pips(board, vertex: int, robber_hex: int) -> dict[Resource, int]:
    out: dict[Resource, int] = {}
    for h in TOPOLOGY.vertex_hexes[vertex]:
        if h == robber_hex:
            continue
        n = board.numbers[h]
        res = board.terrain[h].resource
        if n is None or res is None:
            continue
        out[res] = out.get(res, 0) + PIP[n]
    return out


def _placement_facts(before: GameState, action: Action) -> dict:
    """settlement/city: adjacent-hex pips added, robber-adjusted (§EXPLAIN
    Facts.prod_gain) — a NEW settlement and a CITY upgrade both add exactly
    one multiple of the vertex's pips (0x->1x, or 1x->2x)."""
    pips = _robber_adjusted_pips(before.board, action.vertex, before.robber_hex)
    gain = {r.value: p for r, p in pips.items()}
    out = {
        "prod_gain": gain,
        "prod_gain_total": sum(gain.values()),
        "prod_gain_diversity": len(gain),
    }
    port = _port_facts(before, action)
    if port is not None:
        out.update(port)
    return out


def _port_facts(before: GameState, action: Action) -> dict | None:
    port = None
    for edge, p in before.board.ports.items():
        if action.vertex in TOPOLOGY.edge_vertices[edge]:
            port = p
            break
    if port is None:
        return None
    if port in before.owned_ports(action.player):
        return None  # already had this port elsewhere: nothing new
    label = "3:1" if port is PortType.GENERIC else f"2:1 {port.resource.value}"
    out: dict = {"port_gained": label}
    if port is not PortType.GENERIC:
        old_ratio = before.trade_ratio(action.player, port.resource)
        if old_ratio > 2:
            out["ratio_improved"] = {port.resource.value: [old_ratio, 2]}
    return out


def _road_opens_facts(before: GameState, action: Action) -> list[dict]:
    from trainer.actions import vertex_notation  # lazy: avoids a puzzles<->trainer import cycle

    p = action.player
    prev = set(_legal_settlement_vertices(before, p))
    after = before.clone()
    apply_action(after, action)
    now = set(_legal_settlement_vertices(after, p))
    opened = sorted(now - prev)
    return [
        {
            "vertex": v,
            "notation": vertex_notation(before.board, v),
            "pips": sum(_resource_pips(before.board, v).values()),
        }
        for v in opened
    ]


def _denies_spot_facts(before: GameState, action: Action, actor: int) -> dict:
    """Only meaningful for a road-connected main-phase settlement: at setup,
    every empty vertex is equally available to both players, so "denies"
    would be a vibe, not a fact — omitted there."""
    from trainer.actions import vertex_notation

    opp = 1 - actor
    denies = action.vertex in set(_legal_settlement_vertices(before, opp))
    return {
        "denies_spot": denies,
        "denies_notation": vertex_notation(before.board, action.vertex) if denies else None,
    }


def _robber_facts(before: GameState, action: Action, actor: int) -> dict:
    opp = 1 - actor
    h = action.hex
    n = before.board.numbers[h]
    pips = PIP.get(n, 0) if n is not None else 0
    res = before.board.terrain[h].resource
    opp_blocked: dict = {}
    my_blocked: dict = {}
    if res is not None and pips:
        for v in TOPOLOGY.hex_vertices[h]:
            b = before.buildings.get(v)
            if b is None:
                continue
            owner, kind = b
            mult = 2 if kind is Building.CITY else 1
            amt = mult * pips
            if owner == opp:
                opp_blocked[res.value] = opp_blocked.get(res.value, 0) + amt
            elif owner == actor:
                my_blocked[res.value] = my_blocked.get(res.value, 0) + amt
    steal_target_cards = None
    opp_adjacent = any(
        before.buildings.get(v, (None,))[0] == opp for v in TOPOLOGY.hex_vertices[h]
    )
    if opp_adjacent and before.players[opp].hand_size() > 0:
        steal_target_cards = before.players[opp].hand_size()
    return {
        "opp_pips_blocked": opp_blocked,
        "opp_pips_blocked_total": sum(opp_blocked.values()),
        "my_pips_blocked": my_blocked,
        "my_pips_blocked_total": sum(my_blocked.values()),
        "steal_target_cards": steal_target_cards,
    }


def _affordable(hand: dict, cost: dict) -> bool:
    return all(hand[r] >= n for r, n in cost.items())


def _enables_now_facts(before: GameState, action: Action) -> dict:
    p = action.player
    hand = dict(before.players[p].resources)
    if action.type is ActionType.TRADE_BANK:
        ratio = before.trade_ratio(p, action.give)
        hand[action.give] -= ratio
        hand[action.get] += 1
    else:  # PLAY_YEAR_OF_PLENTY
        for r in action.resources:
            hand[r] += 1
    current = before.players[p].resources
    completes = None
    if _affordable(hand, COST_CITY) and not _affordable(current, COST_CITY):
        completes = "city"
    elif _affordable(hand, COST_SETTLEMENT) and not _affordable(current, COST_SETTLEMENT):
        completes = "settlement"
    return {"completes": completes}


def _dev_deck_facts(before: GameState) -> dict:
    """`deck_left` and each player's own hand size/dev-card count are public
    (visible pile sizes); TYPES of held-but-unplayed cards are not. The only
    dev type with an explicit public play-counter is the knight
    (`knights_played`), so `expected_knights` spreads the un-pinned knight
    count proportionally across every card whose type isn't publicly known
    (the deck plus both hands) — never peeks at the real deck order."""
    from engine.types import DEV_DECK_COUNTS
    from engine import DevCard

    deck_left = len(before.dev_deck)
    total_knights_played = sum(p.knights_played for p in before.players)
    held_unplayed = sum(
        n for ps in before.players for n in ps.dev_cards.values()
    )
    unknown_pool = deck_left + held_unplayed
    knights_remaining = max(0, DEV_DECK_COUNTS[DevCard.KNIGHT] - total_knights_played)
    expected_knights = (
        knights_remaining * deck_left / unknown_pool if unknown_pool > 0 else 0.0
    )
    return {"deck_left": deck_left, "expected_knights": round(expected_knights, 2)}


def _race_facts(before: GameState, after: GameState, actor: int) -> dict:
    opp = 1 - actor
    lr_before, lr_after = before.longest_road_holder, after.longest_road_holder
    la_before, la_after = before.largest_army_holder, after.largest_army_holder
    return {
        "vp_after": after.total_vp(actor),
        "opp_vp_after": after.total_vp(opp),
        "lr_len_delta": longest_road_length(after, actor) - longest_road_length(before, actor),
        "lr_takes": lr_before != actor and lr_after == actor,
        "lr_holds": lr_before == actor and lr_after == actor,
        "knights_after": after.players[actor].knights_played,
        "opp_knights_after": after.players[opp].knights_played,
        "la_takes": la_before != actor and la_after == actor,
        "la_holds": la_before == actor and la_after == actor,
    }


def _economy_facts(after: GameState, actor: int) -> dict:
    ps = after.players[actor]
    return {
        "build_progress_city": round(_progress(ps.resources, COST_CITY), 3),
        "build_progress_settlement": round(_progress(ps.resources, COST_SETTLEMENT), 3),
        "hand_after": ps.hand_size(),
        "discard_exposed": ps.hand_size() > DISCARD_THRESHOLD,
    }


def _progress(hand: dict, cost: dict) -> float:
    total = sum(cost.values())
    covered = sum(min(hand[r], n) for r, n in cost.items())
    return covered / total if total else 0.0


# ---------------------------------------------------------------------------
# rendering — templates only cite ints that are leaf values in the facts
# dicts they were given (the honesty property tests/test_explain.py checks).
# ---------------------------------------------------------------------------

_KIND_FOR_ACTION = {
    "SETUP_PLACE_SETTLEMENT": "placement",
    "BUILD_SETTLEMENT": "placement",
    "BUILD_CITY": "placement",
    "BUILD_ROAD": "road",
    "MOVE_ROBBER": "robber",
    "TRADE_BANK": "trade",
    "PLAY_YEAR_OF_PLENTY": "trade",
    "BUY_DEV_CARD": "devcard",
    "PLAY_KNIGHT": "devcard",
}


def _cap(s: str, limit: int = _RENDER_CAP) -> str:
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _render_placement(best: dict, alt: dict | None) -> str | None:
    total = best.get("prod_gain_total", 0)
    port = best.get("port_gained")
    if total <= 0 and not port:
        return None
    bits = []
    if total > 0:
        gain = best.get("prod_gain") or {}
        n_res = best.get("prod_gain_diversity", len(gain))
        res_list = ", ".join(f"{n} {r}" for r, n in sorted(gain.items(), key=lambda kv: -kv[1]))
        bits.append(f"adds {total} pips ({res_list}) across {n_res} resource{'s' if n_res != 1 else ''}")
    if port:
        bits.append(f"a {port} port")
    lead = "This spot " + " and ".join(bits) + "."
    tail = ""
    if alt:
        alt_total = alt.get("prod_gain_total", 0)
        if alt_total and alt_total != total:
            cmp = "more" if alt_total > total else "fewer"
            tail = f" The alternative adds {alt_total} pips ({cmp} than this)."
        elif alt.get("port_gained") and not port:
            tail = f" The alternative reaches a {alt['port_gained']} port instead."
    return _cap(lead + tail)


def _render_road(best: dict, alt: dict | None) -> str | None:
    opens = best.get("opens") or []
    if not opens:
        return None
    top = max(opens, key=lambda o: o["pips"])
    lead = f"This road opens the {top['notation']} spot ({top['pips']} pips)"
    if len(opens) > 1:
        lead += f" — {len(opens)} new spots reachable next turn"
    lead += "."
    tail = ""
    if alt and not (alt.get("opens") or []):
        tail = " The alternative opens nothing new."
    return _cap(lead + tail)


def _render_robber(best: dict, alt: dict | None) -> str | None:
    r = best.get("robber") or {}
    opp_total = r.get("opp_pips_blocked_total", 0)
    my_total = r.get("my_pips_blocked_total", 0)
    if opp_total == 0 and my_total == 0:
        return None
    opp = r.get("opp_pips_blocked") or {}
    res_list = ", ".join(f"{n} {res}" for res, n in sorted(opp.items(), key=lambda kv: -kv[1]))
    lead = f"Denies the opponent {opp_total} pips ({res_list})" if opp_total else "Blocks no opponent production"
    if opp_total and my_total == 0:
        lead += " while your own production stays untouched"
    elif my_total:
        lead += f", but also blocks {my_total} of your own pips"
    lead += "."
    tail = ""
    if alt:
        alt_opp = (alt.get("robber") or {}).get("opp_pips_blocked_total", 0)
        if alt_opp != opp_total:
            # Search may rank hexes on factors beyond raw pips (steal
            # value, friendly-robber eligibility) — phrase the comparison
            # directionally instead of assuming the alternative is worse.
            cmp = "more" if alt_opp > opp_total else "fewer"
            tail = f" The alternative hex denies {alt_opp} pips ({cmp} than this)."
    return _cap(lead + tail)


def _render_trade(best: dict, alt: dict | None) -> str | None:
    completes = (best.get("enables_now") or {}).get("completes")
    if not completes:
        return None
    lead = f"This completes your {completes} cost this turn."
    tail = ""
    if alt and not (alt.get("enables_now") or {}).get("completes"):
        tail = " The alternative doesn't unlock a build yet."
    return _cap(lead + tail)


def _render_devcard(best: dict, alt: dict | None) -> str | None:
    race = best.get("race") or {}
    if race.get("la_takes"):
        return _cap(
            f"Playing this retakes Largest Army "
            f"({race['knights_after']} knights vs {race.get('opp_knights_after', 0)})."
        )
    dd = best.get("dev_deck")
    if dd and dd.get("deck_left") is not None:
        return _cap(f"{dd['deck_left']} dev cards remain in the deck.")
    return None


def _render_endgame(best: dict, alt: dict | None) -> str | None:
    race = best.get("race") or {}
    vp = race.get("vp_after")
    if vp is None:
        return None
    lead = f"After this move you're at {vp} VP"
    if race.get("la_takes"):
        lead += " and take Largest Army"
    elif race.get("lr_takes"):
        lead += " and take Longest Road"
    lead += "."
    return _cap(lead)


_RENDERERS = {
    "placement": _render_placement,
    "road": _render_road,
    "robber": _render_robber,
    "trade": _render_trade,
    "devcard": _render_devcard,
}


def render(facts_best: dict, facts_alt: dict | None, phase: str | None = None) -> str | None:
    """One clause from `facts_best`, optionally contrasted with `facts_alt`
    — cites ONLY numbers/names that are facts leaf values. None when
    nothing clears salience; the caller keeps its own win-prob fallback."""
    if phase == "endgame":
        s = _render_endgame(facts_best, facts_alt)
        if s:
            return s
    kind = _KIND_FOR_ACTION.get(facts_best.get("action_type"))
    fn = _RENDERERS.get(kind)
    return fn(facts_best, facts_alt) if fn else None


def render_miss(facts_chosen: dict, facts_best: dict) -> str | None:
    """One sentence on what the user's move missed relative to the best
    move — the mirror of `render`'s contrast clause, standalone (EXPLAIN_SPEC
    §1: "one sentence on what their move missed")."""
    kind = _KIND_FOR_ACTION.get(facts_best.get("action_type"))
    if kind == "placement":
        bt, ct = facts_best.get("prod_gain_total", 0), facts_chosen.get("prod_gain_total", 0)
        if bt > ct:
            return f"Your move only added {ct} pips versus {bt} for the best spot."
        if facts_best.get("port_gained") and not facts_chosen.get("port_gained"):
            return f"Your move missed the {facts_best['port_gained']} port."
    elif kind == "robber":
        bt = (facts_best.get("robber") or {}).get("opp_pips_blocked_total", 0)
        ct = (facts_chosen.get("robber") or {}).get("opp_pips_blocked_total", 0)
        if bt > ct:
            return f"Your target only blocks {ct} pips versus {bt} for the best hex."
    elif kind == "road":
        if (facts_best.get("opens") or []) and not (facts_chosen.get("opens") or []):
            return "Your road doesn't open a new settlement spot."
    elif kind == "trade":
        bc = (facts_best.get("enables_now") or {}).get("completes")
        cc = (facts_chosen.get("enables_now") or {}).get("completes")
        if bc and not cc:
            return f"Your move doesn't complete the {bc} cost this turn."
    elif kind == "devcard":
        if (facts_best.get("race") or {}).get("la_takes") and not (
            facts_chosen.get("race") or {}
        ).get("la_takes"):
            return "Your move doesn't retake Largest Army."
    return None
