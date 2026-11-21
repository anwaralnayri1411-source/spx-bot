"""H-MOREENTRIES-S: توصية ثانية على عقد رابح في بوت الشركات."""
from datetime import datetime

import stocks_bot as sb


def test_winner_map_and_second_idea(monkeypatch):
    n = datetime(2026, 11, 20, 11, 0)
    pos = [{"ticker": "NVDA", "side": "CALL", "exp": "2026-11-27", "strike": 180.0, "last_pnl": 0.25},
           {"ticker": "TSLA", "side": "PUT", "exp": "2026-11-27", "strike": 300.0, "last_pnl": -0.1},
           {"ticker": "AMD", "side": "CALL", "exp": "2026-11-27", "strike": 200.0}]
    monkeypatch.setattr(sb, "active_positions", lambda P, n: pos)
    monkeypatch.setattr(sb, "is_closed", lambda p: False)
    w = sb.winner_map({}, n)
    assert w[("NVDA", "CALL")]["ok"] and not w[("TSLA", "PUT")]["ok"] and not w[("AMD", "CALL")]["ok"]
    i = {"ticker": "NVDA", "side": "CALL", "contracts": [{"exp": "2026-11-27", "strike": 180.0}, {"exp": "2026-11-27", "strike": 185.0}]}
    assert sb.second_idea(i, w[("NVDA", "CALL")]) and i["contracts"][0]["strike"] == 185.0 and i["nth"] == 2
    j = {"ticker": "NVDA", "side": "CALL", "contracts": [{"exp": "2026-11-27", "strike": 180.0}]}
    assert not sb.second_idea(j, w[("NVDA", "CALL")])
    pos += [dict(pos[0], strike=s) for s in (185.0, 190.0)]
    assert not sb.winner_map({}, n)[("NVDA", "CALL")]["ok"]          # السقف 3
