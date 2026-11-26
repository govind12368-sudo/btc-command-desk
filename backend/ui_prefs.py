"""Menu pages: hide / show (owner request 23 Nov 2026).

Two layers, both reversible and neither deletes anything:
  - each user hides pages in their own menu (users.ui_hidden_pages);
  - an admin hides pages for everyone (site_ui "pages"): non-admin users neither see them in the menu nor open them;
    admins still see them, marked "hidden for users".
The BTC Dashboard cannot be hidden, so every account always has a home page.
"""
from bson import ObjectId

from core import db, iso, now_utc

PAGES = ["/", "/market", "/scalping", "/swing", "/pillars", "/scanner", "/intel", "/news", "/plan-a", "/flush-short", "/candle-runs", "/delta-top3",
         "/elite-4-edge", "/option-picks", "/real-account", "/straddle-bot", "/observer", "/fund-manager", "/auto-trade", "/journal", "/regime", "/validation", "/data-health", "/server", "/settings", "/audit",
         "/upgrades", "/account", "/users"]
LOCKED = {"/"}


def clean(pages):
    out = []
    for p in pages or []:
        if isinstance(p, str) and p in PAGES and p not in LOCKED and p not in out:
            out.append(p)
    return out


async def config(user):
    g = await db.site_ui.find_one({"_id": "pages"}) or {}
    u = await db.users.find_one({"_id": ObjectId(user["id"])}, {"ui_hidden_pages": 1}) or {}
    return {"hidden_global": clean(g.get("hidden")), "hidden_user": clean(u.get("ui_hidden_pages")), "locked": sorted(LOCKED), "pages": PAGES,
            "global_by": g.get("by"), "global_at": g.get("at")}


async def set_user(user, pages):
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"ui_hidden_pages": clean(pages)}})
    return await config(user)


async def global_hidden():
    """Pages hidden for all users (the server refuses their data to non-admins: see viewer.py)."""
    return clean(((await db.site_ui.find_one({"_id": "pages"})) or {}).get("hidden"))


async def set_global(user, pages):
    await db.site_ui.update_one({"_id": "pages"}, {"$set": {"hidden": clean(pages), "by": user["email"], "at": iso(now_utc())}}, upsert=True)
    import viewer
    viewer.forget_cache()
    return await config(user)
