"""Gemensamt exponeringstak mellan Anna och Lena.

Bottarna handlar på olika OKX-konton och ser inte varandras positioner. Den här
modulen läser den andra bottens positioner ur den read-only status-feeden
(data/status.json på gh-pages, uppdateras var 30:e min) och stoppar nya affärer som
1) öppnar samma coin som den andra botten redan har, eller
2) skulle ge fler än MAX_SAME_DIRECTION positioner åt samma håll totalt.

Endast läsning av en publik JSON-fil – inga nycklar, inga orderanrop.
Samma logik finns i tradebottenanna/trading/crossbot.py; håll dem i synk.
"""
import datetime as dt
import json
import logging
import os
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FEED_URL = os.getenv("CROSS_BOT_FEED_URL", "https://therog1.github.io/tradebottenanna/data/status.json")
MAX_FEED_AGE_MIN = float(os.getenv("CROSS_BOT_MAX_FEED_AGE_MIN", "75"))
MAX_SAME_DIRECTION = int(os.getenv("CROSS_BOT_MAX_SAME_DIRECTION", "4"))
ENABLED = os.getenv("CROSS_BOT_ENABLED", "true").strip().lower() != "false"
# Om feeden inte går att läsa: blockera nya affärer (true) eller fortsätt med egna gränser (false).
FAIL_CLOSED = os.getenv("CROSS_BOT_FAIL_CLOSED", "false").strip().lower() == "true"

Exposure = Tuple[str, bool]  # (coin, long)


def coin_of(inst_id: str) -> str:
    return str(inst_id or "").split("-")[0].upper()


def peer_exposure(feed: Dict, peer: str, now: Optional[dt.datetime] = None,
                  max_age_min: float = MAX_FEED_AGE_MIN) -> Optional[List[Exposure]]:
    """Den andra bottens positioner som (coin, long). None om feeden saknas, är fel eller för gammal."""
    acc = feed.get(peer) if isinstance(feed, dict) else None
    if not isinstance(acc, dict) or not acc.get("ok"):
        return None
    try:
        fetched = dt.datetime.fromisoformat(str(acc.get("fetched_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    if (now - fetched).total_seconds() > max_age_min * 60:
        return None
    out: List[Exposure] = []
    for p in acc.get("positions") or []:
        try:
            pos = float(p.get("pos") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if pos == 0:
            continue
        side = str(p.get("posSide") or "net").lower()
        long = pos > 0 if side == "net" else side == "long"
        out.append((coin_of(p.get("instId")), long))
    return out


def check(coin: str, long: bool, own: List[Exposure], peer: Optional[List[Exposure]],
          max_same_direction: int = MAX_SAME_DIRECTION, fail_closed: bool = FAIL_CLOSED) -> Tuple[bool, str]:
    """(tillåten, skäl). own = egna öppna positioner som (coin, long)."""
    if peer is None:
        return (False, "CROSS_BOT_FEED_UNAVAILABLE") if fail_closed else (True, "CROSS_BOT_FEED_UNAVAILABLE(ignored)")
    coin = coin.upper()
    if any(c == coin for c, _ in peer):
        return False, f"CROSS_BOT_SAME_COIN:{coin}"
    same = sum(1 for _, l in own + peer if l == long)
    if same >= max_same_direction:
        return False, f"CROSS_BOT_DIRECTION_CAP:{'LONG' if long else 'SHORT'}={same}/{max_same_direction}"
    return True, "OK"


def load_peer(peer: str, timeout: float = 5.0) -> Optional[List[Exposure]]:
    if not ENABLED:
        return []
    try:
        with urllib.request.urlopen(FEED_URL, timeout=timeout) as r:
            feed = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # nätverk/parse – aldrig krascha cykeln
        logger.warning(f"Cross-bot: kunde inte läsa feeden ({type(e).__name__})")
        return None
    exp = peer_exposure(feed, peer)
    if exp is None:
        logger.warning(f"Cross-bot: {peer} saknas eller är inaktuell i feeden")
    return exp
