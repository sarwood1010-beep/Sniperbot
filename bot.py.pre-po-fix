import os, discord, json, asyncio, hashlib, traceback, math, time, base64
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta, date
from polymarket_us import PolymarketUS
from pathlib import Path
import websockets
from cryptography.hazmat.primitives.asymmetric import ed25519

load_dotenv()
def find_env(names):
    for n in names:
        v=os.environ.get(n)
        if v:return v
    return None
key_id=find_env(["POLYMARKET_KEY_ID","POLYMARKET_US_KEY_ID"])
secret=find_env(["POLYMARKET_SECRET_KEY","POLYMARKET_US_SECRET"])
pm=PolymarketUS(key_id=key_id,secret_key=secret) if key_id and secret else PolymarketUS()
auth_works=False
try:pm.account.balances();auth_works=True
except:pass

TOKEN=os.environ.get("DISCORD_TOKEN")
ALERTS=int(os.environ.get("ALERTS_CHANNEL_ID",0))
TRADES=int(os.environ.get("TRADES_CHANNEL_ID",0))
OWNER=int(os.environ.get("AUTHORIZED_USER_ID",0))

scanning=True
live_mode=False
paused=False

# ─── v16.13: WebSocket constants ─────────────────────────────
WS_MARKETS="wss://api.polymarket.us/v1/ws/markets"
MAX_INSTRUMENTS=10

# ─── v16.13: tick history & near-miss tracking ───────────────
TICK_HISTORY={}                  # {slug: [(ts, side, price), ...]} rolling 1h
TICK_HISTORY_SECONDS=3600
NEAR_MISS_RATIO=0.5              # alert on drops >= 50% of threshold
NEAR_MISS_COOLDOWN=300           # don't re-alert same slug within 5 min
LAST_NEAR_MISS={}                # {slug: ts} for cooldown

# v16.13: raw WS message capture (in-memory rolling buffer of last 20)
RAW_WS_LOG=[]                    # [(ts, raw_str), ...] last 20 messages
RAW_WS_LOG_MAX=20

def record_raw_ws(raw):
    RAW_WS_LOG.append((time.time(),raw[:2000]))
    while len(RAW_WS_LOG)>RAW_WS_LOG_MAX:RAW_WS_LOG.pop(0)

# v16.9: decision log. Every threshold-crossing evaluation logs its outcome here
# (FIRE/recorded, cooldown, dead market, drop too large, band, edge, daily limit).
# /reconcile cross-checks this against local trades and Discord alerts.
# v16.14: RESTORED. This block and log_decision() were accidentally deleted in
# v16.13.1 when comments were trimmed to fit GitHub's 65,536-char issue-body
# limit. That made /reconcile crash (NameError) and silently broke every FIRE
# decision (log_decision was called but undefined, caught by the safety wrapper).
# With git-based deploy there is no size limit, so it's back, with full comments.
DECISION_LOG=[]                  # [{ts, slug, side, price, drop, outcome}, ...]
DECISION_LOG_MAX=500             # rolling cap on in-memory decision history

def log_decision(slug,side,price,drop,outcome):
    DECISION_LOG.append({"ts":time.time(),"slug":slug,"side":side,
        "price":price,"drop":drop,"outcome":outcome})
    while len(DECISION_LOG)>DECISION_LOG_MAX:DECISION_LOG.pop(0)

# v16.13: per-slug cooldown breaks open->close->refire churn loop.
LAST_TRADE_AT={}                 # {slug: ts of last trade}

def slug_in_cooldown(slug):
    last=LAST_TRADE_AT.get(slug)
    if last is None:return False
    cooldown_s=float(config.get("slug_cooldown_min",30))*60
    return (time.time()-last)<cooldown_s

def mark_slug_traded(slug):
    LAST_TRADE_AT[slug]=time.time()

def record_tick(slug,side,price):
    """Append to in-memory rolling buffer, trim old entries."""
    now=time.time()
    buf=TICK_HISTORY.setdefault(slug,[])
    buf.append((now,side,price))
    cutoff=now-TICK_HISTORY_SECONDS
    while buf and buf[0][0]<cutoff:buf.pop(0)

def max_drop_in_window(slug,side):
    """Return largest peak-to-current drop in last hour for this slug+side."""
    buf=[t for t in TICK_HISTORY.get(slug,[]) if t[1]==side]
    if len(buf)<2:return 0.0
    peak=max(t[2] for t in buf)
    current=buf[-1][2]
    return peak-current

# ─── THE SHARP'S RULES (v15 unchanged) ──────────────────────
# 1. aec- slugs only  2. Today's date  3. 8% drop (was 10% — WS sees real drops)
# 4. Entry 20-80%  5. Max one trade per game  6. $2 flat bet
# 7. TP 8% SL 8%  8. WebSocket real-time (was 45s poll)
# 9. MLB WTA ATP NBA  10. Don't touch for a week

config={
    "live_interval":45,
    "resolve_interval":600,
    "discovery_interval":300,          # v16.13: rediscover top-10 every 5 min
    "drop_threshold":0.08,             # v16.13: lowered 10% -> 8% (real-time data)
    "revert_pct":0.50,
    "min_edge":0.04,
    "take_profit":0.08,
    "stop_loss":0.08,
    "trail_stop":0.04,
    "bet_size":2.0,
    "max_bet_usd":3.0,
    "bankroll":42.0,
    "max_positions":5,
    "daily_loss_limit":10.0,
    "min_entry_price":0.20,
    "max_entry_price":0.80,
    "min_spread_edge":0.03,
    "activity_min_oi":100.0,           # v16.13: OI threshold for activity bonus
    "date_window_back_days":1,         # v16.13: accept slugs from N days back
    "date_window_forward_days":0,      # v16.13: accept slugs from N days forward (0=today only)
    "max_price_sum":1.10,              # v16.11: reject markets where long+short prices sum > this
    "max_drop_sanity":0.20,            # v16.13: refuse to fire if drop magnitude exceeds this (phantom-drop guard)
    "slug_cooldown_min":30,            # v16.13: after trading a slug, lock it from new trades for N minutes
    "reconcile_min":10,                # v16.15: minutes between periodic exchange-vs-local drift checks
}

LEAGUES=["mlb","nba","wta","atp"]
DATA_FILE=Path("/home/deploy/polymarket-discord-bot/signal_history.json")
CACHE_FILE=Path("/home/deploy/polymarket-discord-bot/price_cache.json")
SNAP_FILE=Path("/home/deploy/polymarket-discord-bot/pregame_snap.json")
CONFIG_FILE=Path("/home/deploy/polymarket-discord-bot/config.json")
intents=discord.Intents.default();intents.message_content=True
bot=discord.Client(intents=intents);tree=app_commands.CommandTree(bot)

def load_json(p,d):
    try:
        if p.exists():return json.loads(p.read_text())
    except:pass
    return d
def save_json(p,d):
    try:p.write_text(json.dumps(d,indent=2,default=str))
    except:pass
def load_data():return load_json(DATA_FILE,{"trades":[],"paper_pnl":0,"total_wagered":0,"wins":0,"losses":0,"exits_taken":0,"exit_pnl":0,"live_pnl":0,"live_trades":0,"daily_loss":0,"daily_reset":"","exit_calibration":{}})
def save_data(d):save_json(DATA_FILE,d)
def load_cache():return load_json(CACHE_FILE,{})
def save_cache(c):save_json(CACHE_FILE,c)
def load_snaps():return load_json(SNAP_FILE,{})
def save_snaps(s):save_json(SNAP_FILE,s)
def load_config_overrides():return load_json(CONFIG_FILE,{})
def save_config_overrides(c):save_json(CONFIG_FILE,c)

# v16.13: merge persisted overrides into in-memory config at startup
try:
    _overrides=load_config_overrides()
    for _k,_v in _overrides.items():
        if _k in config:config[_k]=_v
    if _overrides:print(f"[config] loaded overrides: {_overrides}")
except Exception as _e:print(f"[config] load err: {_e}")

def search_events(q):
    try:
        r=pm.search.query({"query":q})
        if isinstance(r,dict):
            for v in r.values():
                if isinstance(v,list):return v
        elif isinstance(r,list):return r
    except:pass
    return []

def is_future_market(slug,title=""):
    kw=["champion","mvp","trophy","winner","award","champ","pennant","rookie","cy-young","allstar","series-price"]
    return any(k in (slug+" "+title).lower() for k in kw)

def slug_date_str(slug):
    """Extract YYYY-MM-DD substring from a slug, or empty string if none found."""
    import re as _re
    m=_re.search(r"(\d{4}-\d{2}-\d{2})",slug or "")
    return m.group(1) if m else ""

def is_today_slug(slug):
    """v16.13: configurable today-only check.
    Default accepts today + yesterday (UTC) to cover late evening games slugged
    in yesterday's UTC date. Excludes anything dated forward of today unless
    date_window_forward_days is increased.
    Trusts the slug date — fail-closed if no date can be parsed."""
    sd=slug_date_str(slug)
    if not sd:return False
    try:
        slug_date=datetime.strptime(sd,"%Y-%m-%d").date()
    except:return False
    today=datetime.now(timezone.utc).date()
    back=int(config.get("date_window_back_days",1))
    fwd=int(config.get("date_window_forward_days",0))
    delta=(slug_date-today).days
    return -back<=delta<=fwd

def extract_aec_markets(events):
    markets={}
    for event in events:
        if not isinstance(event,dict):continue
        title=event.get("title",event.get("question","?"))
        eslug=event.get("slug","?")
        if is_future_market(eslug,title):continue
        nested=event.get("markets",[])
        if not isinstance(nested,list):nested=[]
        for mkt in nested:
            if not isinstance(mkt,dict):continue
            mslug=mkt.get("slug","")
            if not mslug or not mslug.startswith("aec-"):continue
            prices={};sides_info=[]
            for s in mkt.get("marketSides",[]):
                try:prices[s.get("description","?")]=float(s.get("price",0))
                except:pass
                sides_info.append({"description":s.get("description","?"),"price":s.get("price","?"),"long":s.get("long",None),"id":s.get("id","?")})
            # v16.13: pull activity signals — names vary, try several
            def _flt(v):
                try:return float(v) if v not in (None,"","null") else 0.0
                except:return 0.0
            oi=_flt(mkt.get("openInterest") or mkt.get("open_interest"))
            vol=_flt(mkt.get("volume") or mkt.get("totalVolume") or mkt.get("sharesTraded"))
            last_trade=mkt.get("lastTradePx") or mkt.get("last_trade_px")
            has_recent_trade=last_trade not in (None,"","null",{})
            if prices:
                markets[mslug]={"title":title,"event_slug":eslug,"market_slug":mslug,
                    "prices":prices,"closed":mkt.get("closed",False),"sides_info":sides_info,
                    "open_interest":oi,"volume":vol,"has_recent_trade":has_recent_trade}
    return markets

# ─── Order execution (unchanged from v15) ───────────────────
def get_balance():
    try:
        bal=pm.account.balances()
        for b in bal.get("balances",[]):
            if b.get("currency")=="USD":return float(b.get("buyingPower",0))
    except:pass
    return 0

# ─── v16.15: exchange reconciliation (Polymarket = source of truth) ──────────
# Tonight's incident: a /reset wiped local trade records while real positions
# stayed open on the exchange, leaving the bot blind to money it was supposed to
# manage. Root cause: the bot trusted its local file instead of the exchange.
# These helpers make the exchange authoritative. We only verified the positions
# call returns an empty {"positions":{}, "nextCursor":"", "eof":true,
# "availablePositions":[]} shape, so we handle the populated shape DEFENSIVELY:
# log the raw structure the first time we see a real position rather than
# guessing field names.
def fetch_exchange_positions():
    """Return (ok, list_of_raw_position_objects). ok=False means we could NOT
    reach/verify the exchange — callers must treat that as 'unknown', never as
    'flat'. Paginates via nextCursor/eof."""
    if not auth_works:
        return False, []
    out=[]
    cursor=""
    try:
        for _ in range(20):  # hard page cap; 20 pages is far more than we'd hold
            resp=pm.portfolio.positions()
            if not isinstance(resp,dict):
                return False, []
            pos=resp.get("positions",{})
            # positions may be a dict keyed by market/token, or a list — handle both
            if isinstance(pos,dict):
                for k,v in pos.items():
                    if isinstance(v,dict):
                        v=dict(v); v.setdefault("_key",k)
                        out.append(v)
                    else:
                        out.append({"_key":k,"_value":v})
            elif isinstance(pos,list):
                out.extend(pos)
            cursor=resp.get("nextCursor","")
            if resp.get("eof",True) or not cursor:
                break
        return True, out
    except Exception as e:
        print(f"[recon] positions fetch failed: {e}")
        return False, []

def _pos_size(p):
    """Best-effort extract a position size from an unknown-shape object.
    Returns float; 0 if we can't find one (treated as 'no real exposure')."""
    for k in ("netPosition","netPositionDecimal","qtyAvailable","size","quantity","shares","netQuantity","position","amount"):
        if k in p:
            try:
                f=float(p[k])
                if f!=0:return f
            except (TypeError,ValueError):pass
    return 0.0

def real_open_positions():
    """(ok, [raw positions with nonzero size]). ok=False => could not verify."""
    ok,raw=fetch_exchange_positions()
    if not ok:
        return False, []
    live=[p for p in raw if _pos_size(p)!=0]
    return True, live

def reconcile_report():
    """Compare exchange truth vs local open trades. Returns a dict the callers
    format for Discord. Does NOT mutate anything."""
    ok,exch=real_open_positions()
    d=load_data()
    local_open=[t for t in d.get("trades",[]) if t.get("status")=="open"]
    return {"ok":ok,"exchange":exch,"local_open":local_open,
            "n_exchange":len(exch),"n_local":len(local_open)}
# ─── end v16.15 reconciliation helpers ───────────────────────────────────────

def get_side_intent(side_name,sides_info):
    for s in sides_info:
        if s["description"].lower()==side_name.lower():return s.get("long",True)
    return True

def safe_quantity(price,max_dollars):
    if price<=0 or price>1:return 0
    q=math.floor(max_dollars/price)
    while q>0 and q*price>max_dollars:q-=1
    return max(0,q)

def place_order(slug,side_name,price,dollar_amount,sides_info):
    dollar_amount=min(dollar_amount,config["max_bet_usd"])
    qty=safe_quantity(price,dollar_amount)
    if qty<=0:return None,0
    cost=qty*price
    if cost>config["max_bet_usd"]:return None,0
    bal=get_balance()
    if bal<cost:return None,0
    is_long=get_side_intent(side_name,sides_info)
    intent="ORDER_INTENT_BUY_LONG" if is_long else "ORDER_INTENT_BUY_SHORT"
    try:
        order=pm.orders.create({"marketSlug":slug,"intent":intent,"type":"ORDER_TYPE_LIMIT",
            "price":{"value":str(round(price,3)),"currency":"USD"},
            "quantity":qty,"tif":"TIME_IN_FORCE_FILL_OR_KILL"})
        if not order:return None,0
        execs=order.get("executions",[])
        if not execs:
            print(f"KILLED: {slug} {side_name} @ {price}");return None,0
        actual_cost=0;actual_qty=0
        for ex in execs:
            try:eq=float(ex.get("quantity",ex.get("size",0)));ep=float(ex.get("price",price));actual_cost+=eq*ep;actual_qty+=eq
            except:pass
        if actual_qty<=0:return None,0
        print(f"FILLED: {actual_qty} @ ${actual_cost/actual_qty:.3f} = ${actual_cost:.2f}")
        return order,actual_cost
    except Exception as e:
        print(f"Order error: {e}");return None,0

def place_sell(slug,side_name,price,qty,sides_info):
    is_long=get_side_intent(side_name,sides_info)
    intent="ORDER_INTENT_SELL_LONG" if is_long else "ORDER_INTENT_SELL_SHORT"
    try:
        order=pm.orders.create({"marketSlug":slug,"intent":intent,"type":"ORDER_TYPE_LIMIT",
            "price":{"value":str(round(price,3)),"currency":"USD"},
            "quantity":int(max(1,qty)),"tif":"TIME_IN_FORCE_FILL_OR_KILL"})
        if not order:return None
        if not order.get("executions",[]):
            print(f"Sell KILLED: {slug}");return None
        return order
    except:return None

def cancel_all_orders():
    try:pm.orders.cancel_all();return True
    except:return False

# ─── Daily loss / trade mgmt (unchanged) ────────────────────
def check_daily_loss():
    data=load_data();today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("daily_reset")!=today:data["daily_loss"]=0;data["daily_reset"]=today;save_data(data)
    return data.get("daily_loss",0)

def add_daily_loss(amt):
    data=load_data();today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("daily_reset")!=today:data["daily_loss"]=0;data["daily_reset"]=today
    data["daily_loss"]=data.get("daily_loss",0)+amt;save_data(data)
    return data["daily_loss"]

def count_all_open():return sum(1 for t in load_data()["trades"] if t.get("status")=="open")
def has_open_on_slug(slug):
    return any(t["slug"]==slug and t.get("status")=="open" for t in load_data()["trades"])
def edge_ok(price,edge):
    if price<config["min_entry_price"] or price>config["max_entry_price"]:return False
    return edge>=config["min_edge"]

def open_trade(slug,side,mp,mkt_price,lg,q,reason="",sides_info=None):
    if has_open_on_slug(slug):return None
    if count_all_open()>=config["max_positions"]:return None
    edge=mp-mkt_price
    if not edge_ok(mkt_price,edge):return None
    if check_daily_loss()>=config["daily_loss_limit"]:return None
    if mkt_price<config["min_entry_price"] or mkt_price>config["max_entry_price"]:return None
    bet=config["bet_size"];bet=min(bet,config["max_bet_usd"])
    live_order=None;actual_cost=bet;trade_price=mkt_price;book_status="paper"
    if live_mode and auth_works and sides_info:
        live_order,actual_cost=place_order(slug,side,mkt_price,bet,sides_info)
        if not live_order:return None
        book_status="filled"
    data=load_data()
    shares=int(actual_cost/trade_price) if trade_price>0 else 0
    t={"id":hashlib.md5(f"{slug}-{side}-{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:8],
        "slug":slug,"side":side,"model_prob":mp,"market_prob":trade_price,"edge":mp-trade_price,
        "league":lg,"question":q,"bet_size":actual_cost,"entry_price":trade_price,
        "reason":reason,"entry_shares":shares,"book_status":book_status,
        "timestamp":datetime.now(timezone.utc).isoformat(),"status":"open","high_water":trade_price,
        "exit_price":None,"exit_reason":None,"resolved":False,"won":None,"pnl":0.0,
        "live":live_mode,"order_id":live_order.get("id") if live_order else None,"sides_info":sides_info}
    data["trades"].append(t)
    if live_mode:data["live_trades"]=data.get("live_trades",0)+1
    save_data(data);return t

def exit_trade(tid,ep,reason):
    data=load_data()
    for t in data["trades"]:
        if t["id"]==tid and t.get("status")=="open":
            if t.get("live") and auth_works and t.get("sides_info"):
                shares=t.get("entry_shares",int(t["bet_size"]/t["entry_price"]) if t["entry_price"]>0 else 1)
                sell_result=place_sell(t["slug"],t["side"],ep,shares,t["sides_info"])
                if not sell_result:return None
            t["status"]="exited";t["exit_price"]=ep;t["exit_reason"]=reason;t["resolved"]=True
            shares=t["bet_size"]/t["entry_price"] if t["entry_price"]>0 else 0
            t["pnl"]=(ep-t["entry_price"])*shares;t["won"]=t["pnl"]>0
            data["paper_pnl"]+=t["pnl"];data["total_wagered"]+=t["bet_size"]
            data["exits_taken"]=data.get("exits_taken",0)+1;data["exit_pnl"]=data.get("exit_pnl",0)+t["pnl"]
            if t.get("live"):data["live_pnl"]=data.get("live_pnl",0)+t["pnl"]
            if t["won"]:data["wins"]+=1
            else:data["losses"]+=1;add_daily_loss(abs(t["pnl"]))
            ec=data.get("exit_calibration",{})
            if reason not in ec:ec[reason]={"count":0,"total_pnl":0,"avg_pnl":0,"wins":0,"win_rate":0}
            ec[reason]["count"]+=1;ec[reason]["total_pnl"]+=t["pnl"]
            ec[reason]["avg_pnl"]=ec[reason]["total_pnl"]/ec[reason]["count"]
            if t["won"]:ec[reason]["wins"]+=1
            ec[reason]["win_rate"]=ec[reason]["wins"]/ec[reason]["count"]
            data["exit_calibration"]=ec;save_data(data);return t
    return None

def resolve_at_expiry(tid,won):
    data=load_data()
    for t in data["trades"]:
        if t["id"]==tid and t.get("status")=="open":
            t["status"]="resolved";t["resolved"]=True;t["won"]=won;t["exit_reason"]="expiry"
            if won:t["pnl"]=t["bet_size"]*(1/t["entry_price"]-1);data["wins"]+=1
            else:t["pnl"]=-t["bet_size"];data["losses"]+=1;add_daily_loss(t["bet_size"])
            data["paper_pnl"]+=t["pnl"];data["total_wagered"]+=t["bet_size"]
            if t.get("live"):data["live_pnl"]=data.get("live_pnl",0)+t["pnl"]
            save_data(data);return t
    return None

def check_exits(slug,cur):
    data=load_data();exits=[]
    for t in data["trades"]:
        if t.get("status")!="open" or t["slug"]!=slug:continue
        c=cur.get(t["side"])
        if c is None:continue
        e=t["entry_price"];hw=t.get("high_water",e)
        if c>hw:t["high_water"]=c;hw=c
        g=c-e
        if g>=config["take_profit"]:exits.append((t["id"],c,"take-profit"))
        elif g<=-config["stop_loss"]:exits.append((t["id"],c,"stop-loss"))
        elif hw>e+0.03 and c<=hw-config["trail_stop"]:exits.append((t["id"],c,"trail-stop"))
    if exits:save_data(data)
    return exits

def get_summary():
    d=load_data();total=len(d["trades"])
    pend=sum(1 for t in d["trades"] if t.get("status")=="open")
    res=sum(1 for t in d["trades"] if t.get("resolved"))
    w=d.get("wins",0);l=d.get("losses",0);pnl=d.get("paper_pnl",0)
    wag=d.get("total_wagered",0);roi=(pnl/wag*100) if wag>0 else 0
    acc=(w/res*100) if res>0 else 0
    lp=d.get("live_pnl",0);lt=d.get("live_trades",0);dl=d.get("daily_loss",0)
    return total,pend,res,w,l,pnl,wag,roi,acc,lp,lt,dl

def get_league_stats():
    d=load_data();s={}
    for t in d["trades"]:
        lg=t.get("league","?")
        if lg not in s:s[lg]={"trades":0,"wins":0,"losses":0,"pnl":0,"pending":0}
        s[lg]["trades"]+=1
        if t.get("status")=="open":s[lg]["pending"]+=1
        elif t.get("resolved"):
            if t.get("won"):s[lg]["wins"]+=1
            else:s[lg]["losses"]+=1
        s[lg]["pnl"]+=t.get("pnl",0)
    return s

# ─── v16.13: discovery — pick top-10 for WebSocket ───────────
top_games=[]            # [{slug,title,league,sides_info,price,score}, ...]
top_lock=asyncio.Lock()
# v16.13: serializes the evaluate->order->record path per process so concurrent
# WS ticks can't race past has_open_on_slug() and fire duplicate orders.
trade_lock=asyncio.Lock()

def discover_top_games():
    """v16.13: Rank today's aec- games. Lower score = better.
    No more is_today_slug hard filter — trust closed flag + activity ranking.
    Score = closeness-to-50% MINUS movement-bonus MINUS activity-bonus.
    Activity bonus heavily favors markets with open interest / recent trades,
    which naturally pushes future-date pregame markets (OI=0) to the bottom."""
    snaps=load_snaps()
    candidates=[]
    min_oi_pref=float(config.get("activity_min_oi",100.0))
    max_sum=float(config.get("max_price_sum",1.10))
    for lg in LEAGUES:
        for ms,mk in extract_aec_markets(search_events(lg)).items():
            # v16.13: HARD FILTERS — closed, not-today, or dead-market gets dropped.
            if mk["closed"]:continue
            if not is_today_slug(ms):continue
            prices=mk["prices"]
            if not prices:continue
            # v16.13: dead-market check. Real two-sided markets sum to ~100%.
            # Markets where sides sum to 110%+ are dead/stale and produce
            # garbage drop signals (the Yibing Wu -25% phantom-drop bug).
            price_sum_check=sum(prices.values())
            if price_sum_check>max_sum:continue
            best_price=None
            for n,p in prices.items():
                if config["min_entry_price"]<=p<=config["max_entry_price"]:
                    if best_price is None or abs(p-0.5)<abs(best_price-0.5):
                        best_price=p
            if best_price is None:continue
            closeness=abs(best_price-0.5)
            movement=0
            snap=snaps.get(ms,{}).get("prices",{})
            for n,p in prices.items():
                pre=snap.get(n)
                if pre is not None:
                    try:movement=max(movement,abs(p-float(pre)))
                    except:pass
            # v16.13: activity bonus weighted heavier — pregame future markets
            # have OI=0 and no recent trades, so they naturally rank last.
            # Live in-progress games dominate because they have real liquidity.
            oi=mk.get("open_interest",0.0)
            vol=mk.get("volume",0.0)
            recent=mk.get("has_recent_trade",False)
            activity_bonus=0.0
            if oi>=min_oi_pref:activity_bonus+=0.25
            elif oi>0:activity_bonus+=0.10           # any OI beats none
            if vol>0:activity_bonus+=0.20
            if recent:activity_bonus+=0.30           # recent trade = live trading
            # also: price sum should be near 1.0 for a real two-sided market
            price_sum=sum(prices.values())
            if 0.90<=price_sum<=1.10:activity_bonus+=0.10
            score=closeness-(movement*2)-activity_bonus
            candidates.append({"slug":ms,"title":mk["title"],"league":lg,
                "sides_info":mk.get("sides_info",[]),"prices":prices,
                "price":best_price,"score":score,
                "oi":oi,"vol":vol,"recent":recent,"price_sum":price_sum})
    candidates.sort(key=lambda x:x["score"])
    return candidates[:MAX_INSTRUMENTS]

# ─── v16.13: WebSocket Ed25519 auth ──────────────────────────
def ws_auth_headers():
    """Ed25519 signed headers for the WS handshake."""
    if not key_id or not secret:return []
    try:
        raw=base64.b64decode(secret)
        priv=ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
        ts=str(int(time.time()*1000))
        msg=f"{ts}GET/v1/ws/markets".encode()
        sig=base64.b64encode(priv.sign(msg)).decode()
        return [("X-PM-Access-Key",key_id),("X-PM-Timestamp",ts),("X-PM-Signature",sig)]
    except Exception as e:
        print(f"ws auth header err: {e}");return []

# ─── v16.13: WebSocket stream ────────────────────────────────
class MarketStream:
    """Persistent WS to /v1/ws/markets with auto-reconnect."""
    def __init__(self,on_tick):
        self.on_tick=on_tick
        self.subscribed=set()
        self.ws=None
        self.req=0
        self.last_msg_at=time.time()
        self.running=True

    def _rid(self):
        self.req+=1
        return f"mdl-{self.req}-{int(time.time())}"

    async def _send_subscribe(self,slugs):
        if not self.ws or not slugs:return
        # v16.13: snake_case + int subscription_type per docs overview.
        # MARKET_DATA_LITE = 2.
        msg={"subscribe":{"request_id":self._rid(),
            "subscription_type":2,
            "market_slugs":list(slugs)}}
        try:await self.ws.send(json.dumps(msg))
        except Exception as e:print(f"[ws] subscribe send err: {e}")

    async def set_subscriptions(self,slugs):
        new_set=set(list(slugs)[:MAX_INSTRUMENTS])
        if new_set==self.subscribed:return
        self.subscribed=new_set
        if self.ws:await self._send_subscribe(new_set)

    async def run(self):
        backoff=1
        while self.running:
            try:
                hdrs=ws_auth_headers()
                async with websockets.connect(WS_MARKETS,additional_headers=hdrs,
                                              ping_interval=20,ping_timeout=15,close_timeout=5) as ws:
                    self.ws=ws
                    self.last_msg_at=time.time()
                    print(f"[ws] connected to {WS_MARKETS}")
                    if self.subscribed:await self._send_subscribe(self.subscribed)
                    backoff=1
                    async for raw in ws:
                        self.last_msg_at=time.time()
                        try:await self._handle(raw)
                        except Exception as e:print(f"[ws] handler err: {e}")
            except Exception as e:
                print(f"[ws] disconnect: {e} -> reconnect in {backoff}s")
                self.ws=None
                await asyncio.sleep(backoff)
                backoff=min(backoff*2,60)

    async def _handle(self,raw):
        # v16.13: capture every raw message for /raw inspection
        record_raw_ws(raw if isinstance(raw,str) else str(raw))
        try:data=json.loads(raw)
        except:return
        if "heartbeat" in data:
            try:await self.ws.send(json.dumps({"heartbeat":{}}))
            except:pass
            return
        if "error" in data:
            print(f"[ws] server error: {data.get('error')}");return
        mdl=data.get("marketDataLite") or data.get("market_data_lite")
        if not mdl:return
        slug=mdl.get("marketSlug") or mdl.get("market_slug")
        if not slug:return
        # v16.13: extract long/short prices from lastPriceSample
        def fv(o):
            if not isinstance(o,dict):return None
            try:return float(o.get("value",0) or 0)
            except:return None
        lps=mdl.get("lastPriceSample") or mdl.get("last_price_sample") or {}
        long_px=fv(lps.get("longPx") or lps.get("long_px"))
        short_px=fv(lps.get("shortPx") or lps.get("short_px"))
        current=fv(mdl.get("currentPx") or mdl.get("current_px"))
        last_trade=fv(mdl.get("lastTradePx") or mdl.get("last_trade_px"))
        best_bid=fv(mdl.get("bestBid") or mdl.get("best_bid"))
        best_ask=fv(mdl.get("bestAsk") or mdl.get("best_ask"))
        # if lastPriceSample missing, derive long from current/bid; short = 1 - long
        if long_px is None:long_px=current or best_bid
        if long_px is not None and short_px is None:short_px=round(1.0-long_px,4)
        if long_px is None and short_px is None:return
        payload={
            "long_px":long_px,"short_px":short_px,
            "current":current,"last_trade":last_trade,
            "best_bid":best_bid,"best_ask":best_ask,
            "open_interest":mdl.get("openInterest") or mdl.get("open_interest"),
        }
        await self.on_tick(slug,payload)

stream=None

async def on_market_tick(slug,payload):
    """v16.13: payload is dict with long_px, short_px, current, last_trade, best_bid, best_ask.
    Each WS tick contains BOTH sides — we evaluate both."""
    global top_games,scanning
    if paused or not scanning:return
    side_info=None
    async with top_lock:
        for g in top_games:
            if g["slug"]==slug:side_info=g;break
    if not side_info:return
    if side_info.get("closed"):return
    # v16.13: belt-and-suspenders date guard. Even if discovery somehow
    # subscribed a future-date market, refuse to fire on it.
    if not is_today_slug(slug):
        return

    # v16.13: figure out real side names from sides_info
    # sides_info is a list of {description, price, long, id}; map long->name
    sides_info=side_info.get("sides_info") or []
    long_name=None;short_name=None
    for s in sides_info:
        if s.get("long") is True and not long_name:long_name=s.get("description")
        elif s.get("long") is False and not short_name:short_name=s.get("description")
    # fallback if sides_info incomplete
    if not long_name:long_name="long"
    if not short_name:short_name="short"

    long_px=payload.get("long_px")
    short_px=payload.get("short_px")
    sides_now={}
    if long_px is not None:sides_now[long_name]=long_px
    if short_px is not None:sides_now[short_name]=short_px
    if not sides_now:return

    # v16.13: market-quality guard. side_info["prices"] are the REST snapshot
    # prices from discovery. If they don't sum near 100%, this is a dead
    # market — refuse to fire. (Prevents the Yibing Wu phantom-drop bug.)
    rest_prices=side_info.get("prices") or {}
    if rest_prices:
        rest_sum=sum(float(p) for p in rest_prices.values() if isinstance(p,(int,float)))
        max_sum=float(config.get("max_price_sum",1.10))
        if rest_sum>max_sum:
            # dead market — let snapshots/ticks continue updating but
            # don't fire trades. This is also caught at discovery time
            # but markets can deteriorate after subscription.
            quality_ok=False
        else:
            quality_ok=True
    else:
        quality_ok=True  # no REST data yet, allow (discovery just started)

    # update snapshot with both sides
    snaps=load_snaps()
    if slug not in snaps:
        snaps[slug]={"prices":{},"league":side_info["league"],"title":side_info["title"],
            "time":datetime.now(timezone.utc).isoformat(),"sides_info":sides_info}
    # merge — keep both sides current
    cur_prices=dict(snaps[slug].get("prices",{}) or {})
    # purge legacy _ws placeholder
    cur_prices.pop("_ws",None)
    for sname,px in sides_now.items():
        cur_prices[sname]=px
    snaps[slug]["prices"]=cur_prices
    snaps[slug]["time"]=datetime.now(timezone.utc).isoformat()
    save_snaps(snaps)

    # record both sides into tick history
    for sname,px in sides_now.items():
        record_tick(slug,sname,px)

    # check exits on any open trades for this slug (uses both sides)
    for tid,ep,reason in check_exits(slug,cur_prices):
        t=exit_trade(tid,ep,reason)
        if t:
            ch=bot.get_channel(ALERTS)
            if ch:
                tag="LIVE" if t.get("live") else "PAPER"
                embed=discord.Embed(title=f"EXIT [{tag}]: {reason.upper()}",
                    color=0x10B981 if t["won"] else 0xEF4444)
                embed.add_field(name="Market",value=t["question"][:60],inline=False)
                embed.add_field(name="Entry/Exit",value=f"{t['entry_price']:.0%}->{ep:.0%}",inline=True)
                embed.add_field(name="P&L",value=f"${t['pnl']:+.2f}",inline=True)
                await ch.send(embed=embed)

    # evaluate drop rule on EACH side independently
    threshold=float(config["drop_threshold"])
    min_e=float(config["min_entry_price"])
    max_e=float(config["max_entry_price"])
    min_edge=float(config["min_edge"])
    max_drop=float(config.get("max_drop_sanity",0.20))
    for sname,px in sides_now.items():
        window_drop=max_drop_in_window(slug,sname)
        effective_drop=window_drop
        reason=None
        if effective_drop>=threshold:
            if effective_drop>max_drop:
                # phantom-drop guard: drops > cap are book noise
                reason=f"drop too large ({effective_drop:.0%} > {max_drop:.0%})"
            elif slug_in_cooldown(slug):
                # per-slug cooldown: breaks churn loop
                reason="cooldown"
            elif not quality_ok:
                # dead market (sum > max_price_sum). Phantom drops happen here.
                reason=f"dead market (sum {rest_sum:.0%})"
            elif px<min_e or px>max_e:
                reason=f"band ({px:.0%})"
            elif has_open_on_slug(slug):
                reason="open trade"
            elif check_daily_loss()>=config["daily_loss_limit"]:
                reason=f"daily limit"
            else:
                revert=px+effective_drop*float(config["revert_pct"])
                edge=revert-px
                if edge<min_edge:reason=f"edge {edge:.1%}"
                else:reason="FIRE"
        # near-miss alert
        if effective_drop>=threshold*NEAR_MISS_RATIO:
            cooldown_key=f"{slug}|{sname}"
            last_alert=LAST_NEAR_MISS.get(cooldown_key,0)
            if time.time()-last_alert>=NEAR_MISS_COOLDOWN or reason=="FIRE":
                LAST_NEAR_MISS[cooldown_key]=time.time()
                ch=bot.get_channel(ALERTS)
                if ch:
                    pct=effective_drop/threshold*100
                    emoji="🎯" if reason=="FIRE" else ("⚠️" if effective_drop>=threshold else "👀")
                    tag=reason or f"below threshold ({effective_drop:.1%})"
                    await ch.send(f"{emoji} `{slug[:50]}` **{sname}** drop **{effective_drop:.1%}** ({pct:.0f}% of thr) @ {px:.0%} — {tag}")
        # fire if all rules met
        if reason!="FIRE":
            # v16.14: log blocked threshold-crossing decisions (cooldown, dead
            # market, drop too large, band, edge, daily limit) so /reconcile can
            # show WHY trades did or didn't fire. This is the diagnostic data we
            # need to measure the edge cleanly over the coming week.
            if reason and effective_drop>=threshold:
                log_decision(slug,sname,px,effective_drop,f"blocked:{reason}")
            continue
        # v16.13.1: mark cooldown at decision, before lock (fixes re-fire spam)
        mark_slug_traded(slug)
        revert=px+effective_drop*float(config["revert_pct"])
        edge=revert-px
        # serialize fire path; re-check open inside lock
        async with trade_lock:
            if has_open_on_slug(slug):
                log_decision(slug,sname,px,effective_drop,"skipped_dup_in_lock")
                print(f"[trade_lock] {slug} {sname}: already open by concurrent tick, skipping")
                continue
            pre_trade_count=len(load_data().get("trades",[]))
            trade=None;record_err=None
            try:
                trade=open_trade(slug,sname,revert,px,side_info["league"],side_info["title"],
                    f"peak drop {effective_drop:.1%} (WS)",sides_info)
            except Exception as e:
                record_err=e
                print(f"[open_trade] exception: {e}")
                traceback.print_exc()
            post_trade_count=len(load_data().get("trades",[]))
            wrote_ok=(post_trade_count>pre_trade_count) and trade is not None
            # if order placed but record failed: pause + alert
            if trade is None and record_err is not None:
                log_decision(slug,sname,px,effective_drop,"crashed")
                scanning=False
                ch=bot.get_channel(ALERTS)
                if ch:
                    await ch.send(f"🚨 **TRADE PATH CRASHED** — bot PAUSED.\n"
                        f"`{slug}` {sname} @ {px:.0%}\n"
                        f"Error: `{type(record_err).__name__}: {str(record_err)[:200]}`\n"
                        f"Check Polymarket history for unrecorded orders. Use `/resume` only after verifying.")
                continue
            if trade is not None and not wrote_ok:
                log_decision(slug,sname,px,effective_drop,"placed_not_recorded")
                scanning=False
                ch=bot.get_channel(ALERTS)
                if ch:
                    await ch.send(f"🚨 **ORDER PLACED BUT NOT RECORDED** — bot PAUSED.\n"
                        f"`{slug}` {sname} @ {px:.0%} mode={'LIVE' if live_mode else 'PAPER'}\n"
                        f"Check Polymarket history immediately. Use `/resume` only after verifying.")
                continue
            if trade is None and record_err is None:
                log_decision(slug,sname,px,effective_drop,"open_trade_returned_none")
                continue
            # success path
            log_decision(slug,sname,px,effective_drop,f"recorded:{trade.get('id','?')}")
            if trade:
                ch=bot.get_channel(ALERTS)
                if ch:
                    tag="LIVE" if trade.get("live") else "PAPER"
                    embed=discord.Embed(title=f"EDGE [{tag}] [{side_info['league'].upper()}] WS",
                        color=0xEF4444 if trade.get("live") else 0xF59E0B)
                    embed.add_field(name="Game",value=side_info["title"][:60],inline=False)
                    embed.add_field(name="Side",value=sname,inline=True)
                    embed.add_field(name="Drop",value=f"-{effective_drop:.1%} @ {px:.0%}",inline=True)
                    embed.add_field(name="Edge",value=f"+{edge:.1%}",inline=True)
                    embed.add_field(name="Bet",value=f"${trade['bet_size']:.2f}",inline=True)
                    if trade.get("book_status"):embed.add_field(name="Fill",value=trade["book_status"],inline=True)
                    if trade.get("order_id"):embed.add_field(name="Order",value=trade["order_id"],inline=True)
                    await ch.send(embed=embed)

# ─── Commands (v15 set + /top) ──────────────────────────────
@tree.command(name="golive",description="Go live")
@app_commands.describe(confirm="Type CONFIRM LIVE")
async def cmd_golive(i:discord.Interaction,confirm:str):
    global live_mode
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    if not auth_works:return await i.response.send_message("Auth fail.")
    if confirm!="CONFIRM LIVE":return await i.response.send_message("Type: CONFIRM LIVE")
    live_mode=True;bal=get_balance()
    await i.response.send_message(f"**THE SHARP v16.15 IS LIVE (WebSocket)**\n${bal:.2f} | Flat ${config['bet_size']} bets\naec- only | today only | 8% drop\nTP/SL 8% | FOK + fill verified\nDon't touch for a week.")

@tree.command(name="gopaper",description="Paper")
async def cmd_gopaper(i:discord.Interaction):
    global live_mode
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    live_mode=False;await i.response.send_message("**PAPER.**")

@tree.command(name="closeall",description="Emergency stop")
async def cmd_closeall(i:discord.Interaction):
    global scanning,live_mode
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    scanning=False;live_mode=False;r=cancel_all_orders()
    await i.response.send_message(f"**STOPPED.** Cancel: {'ok' if r else 'fail'}")

@tree.command(name="balance",description="Balance")
async def cmd_bal(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.send_message(f"**${get_balance():.2f}**")

@tree.command(name="status",description="Status")
async def cmd_status(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    total,pend,res,w,l,pnl,wag,roi,acc,lp,lt,dl=get_summary()
    mode="**LIVE**" if live_mode else "PAPER";bal=get_balance() if auth_works else 0
    snaps=load_snaps()
    today_snaps=sum(1 for k in snaps if is_today_slug(k))
    ws_state="connected" if (stream and stream.ws) else "DOWN"
    ws_age=int(time.time()-stream.last_msg_at) if stream else -1
    msg=(f"**v16.15 THE SHARP {mode} (WebSocket)**\n"
        f"${bal:.2f} | ${config['bet_size']} flat | drop {config['drop_threshold']:.0%}\n"
        f"TP {config['take_profit']:.0%} SL {config['stop_loss']:.0%}\n"
        f"WS: {ws_state} | last msg {ws_age}s ago | subs {len(stream.subscribed) if stream else 0}/{MAX_INSTRUMENTS}\n"
        f"Leagues: {', '.join(LEAGUES)}\n"
        f"Snaps: {len(snaps)} | {today_snaps} today\n"
        f"Daily: ${dl:.2f}/${config['daily_loss_limit']}\n\n"
        f"{total} trades ({pend} open) | {w}W-{l}L")
    if res>0:msg+=f" ({acc:.0f}%) | ${pnl:+.2f}"
    if lt>0:msg+=f"\nLive: ${lp:+.2f} ({lt})"
    await i.response.send_message(msg)

@tree.command(name="top",description="Top 10 watched (WS subs)")
async def cmd_top(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    if not top_games:return await i.response.send_message("No games yet.")
    today=datetime.now(timezone.utc).date()
    def _date_tag(s):
        sd=slug_date_str(s)
        if not sd:return "?"
        try:
            d=datetime.strptime(sd,"%Y-%m-%d").date()
            delta=(d-today).days
            if delta==0:return "TODAY"
            if delta==-1:return "y'day"
            if delta==1:return "tmrw"
            if delta>0:return f"+{delta}d"
            return f"{delta}d"
        except:return sd[-5:]
    lines=[f"`{g['slug'][:38]}` {_date_tag(g['slug'])} {g['price']:.0%} sum{g.get('price_sum',0):.0%} OI{int(g.get('oi',0))} {'🔴' if g.get('recent') else '⚪'} [{g['league']}]" for g in top_games]
    await i.response.send_message("**Top "+str(len(top_games))+" watched:**\n"+"\n".join(lines))

@tree.command(name="debug",description="Per-market snapshot vs current, max drop, would-fire")
async def cmd_debug(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()  # gives us 15 min instead of 3s
    try:
        if not top_games:
            return await i.followup.send("No games subscribed yet.")
        snaps=load_snaps()
        threshold=float(config["drop_threshold"])
        min_e=float(config["min_entry_price"])
        max_e=float(config["max_entry_price"])
        min_edge=float(config["min_edge"])
        lines=[f"**DEBUG** thr={threshold:.0%} band={min_e:.0%}-{max_e:.0%} min_edge={min_edge:.0%} ticks_total={sum(len(v) for v in TICK_HISTORY.values())}"]
        for g in top_games:
            try:
                slug=g.get("slug","?")
                snap_prices=snaps.get(slug,{}).get("prices",{}) or {}
                ticks=TICK_HISTORY.get(slug,[])
                if not snap_prices and not ticks:
                    lines.append(f"`{slug[:42]}` no snap, no ticks");continue
                # coerce all snap prices to float, skip _ws placeholder
                clean_snap={}
                for n,p in snap_prices.items():
                    if n=="_ws":continue
                    try:clean_snap[n]=float(p)
                    except:pass
                if not clean_snap and ticks:
                    # derive sides from tick history if snap is empty
                    side_set=set(t[1] for t in ticks)
                    clean_snap={s:ticks[-1][2] for s in side_set}
                if not clean_snap:
                    lines.append(f"`{slug[:42]}` snap unparseable, {len(ticks)} ticks");continue
                sides_summary=[];would_fire=False;block=""
                for side_name,snap_px in clean_snap.items():
                    side_ticks=[t for t in ticks if t[1]==side_name]
                    if not side_ticks:
                        sides_summary.append(f"{side_name[:10]}:{snap_px:.0%}(0t)");continue
                    current=float(side_ticks[-1][2])
                    peak=max(float(t[2]) for t in side_ticks)
                    drop=peak-current
                    mark="🔥" if drop>=threshold else ("⚠️" if drop>=threshold*0.5 else "")
                    sides_summary.append(f"{side_name[:10]}:{current:.0%}(pk{peak:.0%},-{drop:.1%}{mark},{len(side_ticks)}t)")
                    if drop>=threshold and not would_fire:
                        if current<min_e or current>max_e:
                            block=f"band({current:.0%})"
                        elif has_open_on_slug(slug):
                            block="open trade"
                        else:
                            revert=current+drop*float(config["revert_pct"])
                            edge=revert-current
                            if edge<min_edge:block=f"edge {edge:.1%}"
                            else:would_fire=True
                status=" → 🎯FIRE" if would_fire else (f" → ❌{block}" if block else "")
                lines.append(f"`{slug[:42]}` "+" ".join(sides_summary)+status)
            except Exception as inner:
                lines.append(f"`{g.get('slug','?')[:42]}` ERR: {type(inner).__name__}: {str(inner)[:80]}")
                print(f"[debug] per-market err for {g.get('slug')}: {inner}")
                traceback.print_exc()
        msg="\n".join(lines)
        # paginate if too long
        if len(msg)<=1950:
            await i.followup.send(msg)
        else:
            chunks=[];cur=""
            for ln in lines:
                if len(cur)+len(ln)+1>1900:chunks.append(cur);cur=ln
                else:cur=cur+"\n"+ln if cur else ln
            if cur:chunks.append(cur)
            for c in chunks:await i.followup.send(c)
    except Exception as e:
        print(f"[debug] top-level err: {e}")
        traceback.print_exc()
        try:await i.followup.send(f"❌ /debug crashed: `{type(e).__name__}: {str(e)[:200]}`")
        except:pass

@tree.command(name="raw",description="Dump last N raw WS messages verbatim")
@app_commands.describe(n="How many messages (1-5, default 3)")
async def cmd_raw(i:discord.Interaction,n:int=3):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    try:
        n=max(1,min(5,n))
        if not RAW_WS_LOG:
            return await i.followup.send("No WS messages captured yet.")
        recent=RAW_WS_LOG[-n:]
        header=f"**RAW WS** (last {len(recent)} of {len(RAW_WS_LOG)} captured)"
        await i.followup.send(header)
        for ts,raw in recent:
            age=int(time.time()-ts)
            # try to pretty-print JSON; fall back to raw string
            try:
                parsed=json.loads(raw)
                pretty=json.dumps(parsed,indent=2)
            except:
                pretty=raw
            # discord 2000 char limit, leave room for code fence and age line
            body=pretty[:1850]
            if len(pretty)>1850:body+="\n...(truncated)"
            await i.followup.send(f"`{age}s ago`\n```json\n{body}\n```")
    except Exception as e:
        print(f"[raw] err: {e}")
        traceback.print_exc()
        try:await i.followup.send(f"❌ /raw crashed: `{type(e).__name__}: {str(e)[:200]}`")
        except:pass

@tree.command(name="reconcile",description="Cross-check decisions, trades, and Discord alerts")
@app_commands.describe(hours="Look back this many hours (default 24, max 168)")
async def cmd_reconcile(i:discord.Interaction,hours:int=24):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    try:
        hours=max(1,min(168,hours))
        cutoff=time.time()-(hours*3600)
        # source 1: decision log (in-memory)
        recent_decisions=[d for d in DECISION_LOG if d["ts"]>=cutoff]
        recorded_decisions=[d for d in recent_decisions if d["outcome"].startswith("recorded:")]
        skipped=[d for d in recent_decisions if d["outcome"]=="skipped_dup_in_lock"]
        crashed=[d for d in recent_decisions if d["outcome"]=="crashed"]
        unrecorded=[d for d in recent_decisions if d["outcome"]=="placed_not_recorded"]
        soft_blocked=[d for d in recent_decisions if d["outcome"]=="open_trade_returned_none"]
        # extract recorded trade IDs from decision log for cross-check
        decision_trade_ids=set()
        for d in recorded_decisions:
            tid=d["outcome"].split(":",1)[1] if ":" in d["outcome"] else None
            if tid:decision_trade_ids.add(tid)
        # source 2: local trade records (disk)
        cutoff_iso=datetime.fromtimestamp(cutoff,tz=timezone.utc).isoformat()
        data=load_data()
        local_trades=[t for t in data.get("trades",[]) if t.get("timestamp","")>=cutoff_iso]
        local_trade_ids=set(t["id"] for t in local_trades)
        # source 3: Discord EDGE alerts in this channel
        edge_alerts=0
        emergency_alerts=0
        alert_titles=[]
        ch=bot.get_channel(ALERTS)
        if ch:
            try:
                from datetime import datetime as _dt
                after_dt=_dt.fromtimestamp(cutoff,tz=timezone.utc)
                async for msg in ch.history(limit=500,after=after_dt):
                    if msg.author.id!=bot.user.id:continue
                    if msg.embeds:
                        for e in msg.embeds:
                            title=e.title or ""
                            if "EDGE" in title:
                                edge_alerts+=1
                                alert_titles.append(title[:50])
                    if msg.content and "🚨" in msg.content:
                        emergency_alerts+=1
            except Exception as e:
                print(f"[reconcile] channel.history err: {e}")
        # build report
        n_decisions=len(recorded_decisions)
        n_local=len(local_trades)
        n_discord=edge_alerts
        match_local_vs_decision=decision_trade_ids==local_trade_ids if decision_trade_ids else (n_local==n_decisions)
        all_three_agree=(n_decisions==n_local==n_discord) and match_local_vs_decision
        status="✅ ALL THREE AGREE" if all_three_agree else "⚠️ MISMATCH DETECTED"
        lines=[f"**RECONCILE** (last {hours}h) — {status}"]
        lines.append(f"```")
        lines.append(f"FIRE decisions recorded: {n_decisions}")
        lines.append(f"Local trades on disk:    {n_local}")
        lines.append(f"Discord EDGE alerts:     {n_discord}")
        lines.append(f"")
        lines.append(f"Decisions skipped (dup-lock guard):    {len(skipped)}")
        lines.append(f"Decisions soft-blocked (edge/limit):   {len(soft_blocked)}")
        lines.append(f"Decisions crashed:                     {len(crashed)}")
        lines.append(f"Orders placed but not recorded:        {len(unrecorded)}")
        lines.append(f"Emergency 🚨 alerts in channel:         {emergency_alerts}")
        lines.append(f"```")
        if not all_three_agree:
            lines.append("**Diagnosis:**")
            if n_decisions>n_local:
                lines.append(f"• {n_decisions-n_local} decisions logged but no matching local trade → records lost")
            if n_local>n_decisions:
                lines.append(f"• {n_local-n_decisions} local trades but no matching decision → trade came from elsewhere or decision log was cleared (restart?)")
            if n_decisions>n_discord:
                lines.append(f"• {n_decisions-n_discord} decisions but fewer Discord alerts → alert posting failed")
            if n_discord>n_decisions:
                lines.append(f"• {n_discord-n_decisions} extra Discord alerts beyond decisions → unexpected, investigate")
            if decision_trade_ids and decision_trade_ids!=local_trade_ids:
                missing_from_local=decision_trade_ids-local_trade_ids
                if missing_from_local:
                    lines.append(f"• Trade IDs in decision log but not on disk: {', '.join(list(missing_from_local)[:5])}")
        if recent_decisions[-5:]:
            lines.append("\n**Last 5 decisions:**")
            for d in recent_decisions[-5:]:
                age=int((time.time()-d["ts"])/60)
                lines.append(f"`{age}m ago` {d['slug'][:30]} {d['side'][:15]} -{d['drop']:.1%} → {d['outcome'][:40]}")
        msg="\n".join(lines)
        if len(msg)<=1950:
            await i.followup.send(msg)
        else:
            await i.followup.send(msg[:1900]+"\n...(truncated)")
    except Exception as e:
        print(f"[reconcile] err: {e}")
        traceback.print_exc()
        try:await i.followup.send(f"❌ /reconcile crashed: `{type(e).__name__}: {str(e)[:200]}`")
        except:pass

@tree.command(name="today",description="Today's games")
async def cmd_today(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    all_m={}
    for lg in LEAGUES:
        for k,v in extract_aec_markets(search_events(lg)).items():
            v["league"]=lg;all_m[k]=v
        await asyncio.sleep(0.3)
    today_games={k:v for k,v in all_m.items() if is_today_slug(k) and not v["closed"]}
    if not today_games:return await i.followup.send("No today's games.")
    snaps=load_snaps()
    embed=discord.Embed(title=f"Today ({len(today_games)})",color=0x10B981)
    for k,g in list(today_games.items())[:12]:
        ps=" | ".join([f"{n}: {p:.0%}" for n,p in g["prices"].items()])
        snap=snaps.get(k)
        if snap:
            changes=[]
            for n,p in g["prices"].items():
                pre=snap["prices"].get(n)
                if pre:
                    diff=p-float(pre)
                    if abs(diff)>=0.02:changes.append(f"{n}: {float(pre):.0%}->{p:.0%}")
            if changes:ps+=f"\n{' '.join(changes)}"
        embed.add_field(name=f"[{g.get('league','?').upper()}] {g['title'][:40]}",value=f"{ps}\n`{k[:35]}`",inline=False)
    if len(today_games)>12:embed.set_footer(text=f"+{len(today_games)-12}")
    await i.followup.send(embed=embed)

@tree.command(name="games",description="Sport")
@app_commands.describe(sport="mlb, nba, wta, atp")
async def cmd_games(i:discord.Interaction,sport:str):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    mkts=extract_aec_markets(search_events(sport))
    mkts={k:v for k,v in mkts.items() if not v["closed"]}
    if not mkts:return await i.followup.send(f"No: {sport}")
    embed=discord.Embed(title=f"{sport.upper()} ({len(mkts)})",color=0x3B82F6)
    for k,g in list(mkts.items())[:10]:
        ps=" | ".join([f"{n}: {p:.0%}" for n,p in g["prices"].items()])
        embed.add_field(name=g["title"][:45],value=f"{ps}\n`{k[:35]}`",inline=False)
    await i.followup.send(embed=embed)

@tree.command(name="find",description="Search")
@app_commands.describe(query="Term")
async def cmd_find(i:discord.Interaction,query:str):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    mkts=extract_aec_markets(search_events(query))
    if not mkts:return await i.followup.send(f"No: {query}")
    embed=discord.Embed(title=f"{query} ({len(mkts)})",color=0x3B82F6)
    for k,g in list(mkts.items())[:10]:
        ps=" | ".join([f"{n}: {p:.0%}" for n,p in g["prices"].items()])
        embed.add_field(name=g["title"][:45],value=f"{ps}\n`{k[:35]}`",inline=False)
    await i.followup.send(embed=embed)

@tree.command(name="scan",description="Force discovery now")
async def cmd_scan(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    global top_games
    games=await asyncio.get_event_loop().run_in_executor(None,discover_top_games)
    async with top_lock:top_games=games
    if stream:await stream.set_subscriptions([g["slug"] for g in games])
    await i.followup.send(f"Rediscovered: {len(games)} games subscribed to WS.")

@tree.command(name="positions",description="Open")
async def cmd_pos(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    d=load_data();o=[t for t in d["trades"] if t.get("status")=="open"]
    if not o:return await i.response.send_message("No positions.")
    embed=discord.Embed(title=f"Positions ({len(o)})",color=0x3B82F6)
    for t in o:
        tag="LIVE" if t.get("live") else "PAPER"
        embed.add_field(name=f"`{t['id']}` [{tag}] {t['side'][:15]}",value=f"{t['question'][:30]}\nEntry {t['entry_price']:.0%} | ${t['bet_size']:.2f} | {t.get('book_status','?')[:8]}",inline=False)
    await i.response.send_message(embed=embed)

@tree.command(name="history",description="History")
async def cmd_hist(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    total,pend,res,w,l,pnl,wag,roi,acc,lp,lt,dl=get_summary();d=load_data()
    embed=discord.Embed(title="History",color=0x6B7280)
    embed.add_field(name="Trades",value=f"{total} ({pend} open)",inline=True)
    if res>0:
        embed.add_field(name="Record",value=f"{w}W-{l}L ({acc:.0f}%)",inline=True)
        embed.add_field(name="P&L",value=f"${pnl:+.2f}",inline=True)
    if lt>0:embed.add_field(name="Live",value=f"${lp:+.2f} ({lt})",inline=True)
    ec=d.get("exit_calibration",{})
    if ec:embed.add_field(name="Exits",value="\n".join([f"**{r}:** {x['count']}x ${x['avg_pnl']:+.2f}" for r,x in ec.items()]),inline=False)
    recent=[t for t in d["trades"] if t["resolved"]][-5:]
    if recent:embed.add_field(name="Recent",value="\n".join([f"`{t['id']}` {'W' if t['won'] else 'L'} ${t['pnl']:+.2f} [{t.get('league','?')}]" for t in reversed(recent)]),inline=False)
    await i.response.send_message(embed=embed)

@tree.command(name="leagues",description="Leagues")
async def cmd_lg(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    s=get_league_stats()
    if not s:return await i.response.send_message("No data.")
    embed=discord.Embed(title="Leagues",color=0x3B82F6)
    for lg,d in sorted(s.items(),key=lambda x:x[1]["pnl"],reverse=True):
        r=d["wins"]+d["losses"];acc=(d["wins"]/r*100) if r>0 else 0
        embed.add_field(name=f"{lg.upper()} ({d['trades']})",value=f"{d['wins']}W-{d['losses']}L ({acc:.0f}%) | ${d['pnl']:+.2f}",inline=False)
    await i.response.send_message(embed=embed)

@tree.command(name="resolve",description="Resolve")
@app_commands.describe(trade_id="ID",result="won/lost")
async def cmd_res(i:discord.Interaction,trade_id:str,result:str):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    resolve_at_expiry(trade_id,result.lower() in ("won","win","w","yes","1"));await i.response.send_message(f"Resolved: {trade_id}")

@tree.command(name="sync",description="Reconcile: compare exchange positions vs my records")
async def cmd_sync(i:discord.Interaction):
    # v16.15: on-demand reconciliation. Asks the exchange "what do I actually
    # hold?" and compares to local open trades. Flags any drift either way.
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    rep=reconcile_report()
    if not rep["ok"]:
        return await i.followup.send("⚠️ **Sync failed** — couldn't reach/verify the exchange "
            "(API down or auth off). Cannot confirm whether records match reality.")
    exch=rep["exchange"];local=rep["local_open"]
    lines=[f"**Sync** — exchange: {rep['n_exchange']} open | local records: {rep['n_local']} open"]
    if rep["n_exchange"]==0 and rep["n_local"]==0:
        lines.append("✅ Both flat. Records match reality.")
        return await i.followup.send("\n".join(lines))
    # show exchange positions (raw, since we handle unknown shape defensively)
    if exch:
        lines.append("\n__On exchange:__")
        for p in exch[:10]:
            lines.append(f"• `{p.get('_key','?')}` size={_pos_size(p)}")
        # first time we see a real position, dump the full structure to the log
        print(f"[sync] raw exchange position sample: {exch[0]}")
    # local-only (bot thinks open, exchange doesn't show)
    local_only=local if not exch else local  # without a key map we can't pair; report counts
    if local and not exch:
        lines.append("\n⚠️ __Local records show open trades the exchange does NOT report.__ "
            "These may have resolved/closed. Consider /resolve or investigate.")
    if exch and not local:
        lines.append("\n🚨 __Exchange shows positions I have NO record of.__ "
            "This is the orphaned-position case. The raw structure is logged. "
            "I can't manage these (no entry data) — close them in the app or review before resuming.")
    await i.followup.send("\n".join(lines)[:1900])

@tree.command(name="reset",description="Reset bot records (blocked if live positions open)")
@app_commands.describe(scope="'stats' = clear history only | 'all' = wipe everything")
async def cmd_reset(i:discord.Interaction,scope:str="all"):
    # v16.15: hard-block reset while real positions are open on the exchange.
    # Tonight a /reset wiped local records while real money sat open, orphaning
    # positions the bot then couldn't see or manage. Now reset asks the exchange
    # first and REFUSES if anything is open — or if it can't verify (fail-safe:
    # "can't confirm flat" is treated as "don't wipe"). To actually reset, close
    # positions first (or use /closeall), then reset.
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    scope=(scope or "all").lower().strip()
    if scope not in ("stats","all"):
        return await i.followup.send("Usage: `/reset scope:stats` (history only) or `/reset scope:all` (everything).")
    # Exchange safety gate — applies to both scopes
    ok,exch=real_open_positions()
    if not ok:
        return await i.followup.send("🛑 **Reset blocked.** Could not verify exchange positions "
            "(API unreachable or auth down). Refusing to wipe records when I can't confirm you're "
            "flat. Try again when the API responds, or use `/closeall` first.")
    if exch:
        lines=["🛑 **Reset blocked — real positions are open on the exchange:**"]
        for p in exch[:10]:
            lines.append(f"• `{p.get('_key','?')}` size={_pos_size(p)}")
        lines.append("\nClose them first (`/closeall` cancels orders; close positions in the app "
            "or with /sync guidance), then reset. I will not wipe records while live money is open.")
        return await i.followup.send("\n".join(lines))
    # Safe to reset — exchange confirms flat
    if scope=="stats":
        d=load_data()
        d["paper_pnl"]=0;d["total_wagered"]=0;d["wins"]=0;d["losses"]=0
        d["exits_taken"]=0;d["exit_pnl"]=0;d["live_pnl"]=0;d["live_trades"]=0
        d["daily_loss"]=0;d["daily_reset"]="";d["exit_calibration"]={}
        # keep any open trades (there are none on exchange, but keep local list intact)
        d["trades"]=[t for t in d.get("trades",[]) if t.get("status")=="open"]
        save_data(d)
        return await i.followup.send("✅ **Stats reset** (history/counters cleared). Exchange confirmed flat. Cache/snapshots untouched.")
    # scope == all
    save_data({"trades":[],"paper_pnl":0,"total_wagered":0,"wins":0,"losses":0,"exits_taken":0,"exit_pnl":0,"live_pnl":0,"live_trades":0,"daily_loss":0,"daily_reset":"","exit_calibration":{}})
    save_cache({});save_snaps({})
    await i.followup.send("✅ **Full reset.** Exchange confirmed flat before wiping. Records, cache, and snapshots cleared.")

@tree.command(name="pause",description="Pause")
async def cmd_pause(i:discord.Interaction):
    global scanning
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    scanning=False;await i.response.send_message("Paused.")

@tree.command(name="resume",description="Resume")
async def cmd_resume(i:discord.Interaction):
    global scanning
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    scanning=True;await i.response.send_message("Resumed.")

@tree.command(name="config",description="Config")
async def cmd_config(i:discord.Interaction):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    mode="LIVE" if live_mode else "PAPER"
    await i.response.send_message(f"**{mode} | THE SHARP v16.15**\nLeagues: {', '.join(LEAGUES)}\n"+"\n".join([f"**{k}:** {v}" for k,v in config.items()]))

@tree.command(name="set",description="Set (persists to config.json)")
@app_commands.describe(key="Key",value="Value")
async def cmd_set(i:discord.Interaction,key:str,value:str):
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    if key not in config:return await i.response.send_message(f"Keys: {', '.join(config.keys())}")
    old=config[key]
    if isinstance(old,bool):config[key]=value.lower() in ("true","1","yes")
    elif isinstance(old,float):config[key]=float(value)
    elif isinstance(old,int):config[key]=int(value)
    else:config[key]=value
    # v16.13: persist so it survives restart
    try:
        overrides=load_config_overrides()
        overrides[key]=config[key]
        save_config_overrides(overrides)
        await i.response.send_message(f"**{key}:** {old} -> {config[key]} (saved)")
    except Exception as e:
        await i.response.send_message(f"**{key}:** {old} -> {config[key]} (NOT saved: {e})")

@tree.command(name="update",description="Update")
async def cmd_update(i:discord.Interaction):
    # v16.14: git-based deploy. Previously this fetched the newest GitHub issue
    # body, wrote it to build_update.py, exec()'d it (which wrote bot.py), then
    # restarted. That worked but capped code at GitHub's 65,536-char issue-body
    # limit, and the comment-trimming needed to fit silently deleted the
    # DECISION_LOG block in v16.13.1. Now we pull the committed bot.py from the
    # repo and restart. No size limit, full history, no exec of arbitrary text.
    # Deliberately NO pip install step: the venv already has every dependency.
    # Adding requirements handling here would risk breaking the venv on restart
    # to solve a problem we don't have. Add it later, on purpose, when a genuinely
    # new dependency is introduced.
    if i.user.id!=OWNER:return await i.response.send_message("x",ephemeral=True)
    await i.response.defer()
    try:
        import subprocess
        REPO_DIR="/home/deploy/polymarket-discord-bot"
        r=subprocess.run(["git","-C",REPO_DIR,"pull","--no-edit"],
            capture_output=True,text=True,timeout=60)
        out=(r.stdout+r.stderr).strip()
        if r.returncode!=0:
            return await i.followup.send(f"git pull failed (NOT restarting):\n```\n{out[:1500]}\n```")
        if "Already up to date" in out:
            return await i.followup.send(f"Already up to date - nothing to deploy.\n```\n{out[:500]}\n```")
        await i.followup.send(f"Pulled latest. Restarting...\n```\n{out[:1200]}\n```")
        await asyncio.sleep(3)
        subprocess.Popen(["sudo","systemctl","restart","sniper-bot"])
    except Exception as e:await i.followup.send(f"Failed: {e}")

# ─── v16.13: background loops ────────────────────────────────
async def discovery_loop():
    """Every N seconds, rediscover top 10 and update WS subscriptions."""
    global top_games
    await bot.wait_until_ready()
    await asyncio.sleep(2)
    while not bot.is_closed():
        try:
            games=await asyncio.get_event_loop().run_in_executor(None,discover_top_games)
            async with top_lock:top_games=games
            if stream:await stream.set_subscriptions([g["slug"] for g in games])
            # also seed REST prices into snapshots so WS ticks have a baseline
            snaps=load_snaps()
            for g in games:
                if g["slug"] not in snaps:
                    snaps[g["slug"]]={"prices":dict(g["prices"]),"league":g["league"],
                        "title":g["title"],"time":datetime.now(timezone.utc).isoformat(),
                        "sides_info":g["sides_info"]}
            save_snaps(snaps)
            print(f"[disc] top {len(games)} subscribed")
        except Exception as e:print(f"[disc] err: {e}")
        await asyncio.sleep(config["discovery_interval"])

async def resolve_loop():
    await bot.wait_until_ready()
    ch=bot.get_channel(TRADES)
    while not bot.is_closed():
        try:
            d=load_data()
            for t in [x for x in d["trades"] if x.get("status")=="open"]:
                parts=t["slug"].split("-")
                teams="-".join(parts[1:4]) if len(parts)>=4 and parts[0]=="aec" else t["slug"][:20]
                for event in search_events(teams):
                    for mkt in event.get("markets",[]):
                        if mkt.get("slug")==t["slug"] and mkt.get("closed"):
                            for s in mkt.get("marketSides",[]):
                                try:
                                    if float(s.get("price",0))>=0.95:
                                        won=t["side"].lower()==s.get("description","").lower()
                                        result=resolve_at_expiry(t["id"],won)
                                        if result and ch:
                                            tag="LIVE" if t.get("live") else "PAPER"
                                            embed=discord.Embed(title=f"{'WON' if won else 'LOST'} [{tag}]",color=0x10B981 if won else 0xEF4444)
                                            embed.add_field(name="Market",value=t["question"][:60],inline=False)
                                            embed.add_field(name="P&L",value=f"${result['pnl']:+.2f}",inline=True)
                                            tot2,pe2,re2,w,l,pnl,wa2,ro2,ac2,lp,lt,dl2=get_summary()
                                            embed.set_footer(text=f"${pnl:+.2f} | {w}W-{l}L")
                                            await ch.send(embed=embed)
                                except:pass
                await asyncio.sleep(2)
        except Exception as e:print(f"Resolve err: {e}")
        await asyncio.sleep(config["resolve_interval"])

async def watchdog_loop():
    """If WS goes silent for 60s, force reconnect."""
    while not bot.is_closed():
        await asyncio.sleep(30)
        if stream and stream.subscribed and (time.time()-stream.last_msg_at)>60:
            print("[watchdog] stale WS, closing")
            try:
                if stream.ws:await stream.ws.close()
            except:pass

async def reconcile_loop():
    """v16.15: periodic drift check. Every N minutes, compare exchange positions
    to local open records and alert on disagreement. The safety net that catches
    fills we missed, manual trades, or anything that slipped between the cracks.
    Only alerts on the dangerous case (exchange has a position we don't know),
    to avoid noise. Interval is config['reconcile_min'] (default 10)."""
    global scanning
    await asyncio.sleep(60)  # let things settle after boot
    while not bot.is_closed():
        interval=float(config.get("reconcile_min",10))*60
        await asyncio.sleep(max(60,interval))
        try:
            rep=reconcile_report()
            if not rep["ok"]:
                continue  # transient API blip; startup gate handles hard failures
            if rep["n_exchange"]>0 and rep["n_local"]==0:
                scanning=False
                print(f"[recon-loop] ORPHAN raw sample: {rep['exchange'][0]}")
                ch=bot.get_channel(ALERTS)
                if ch:
                    lines=["🚨 **Drift detected** — exchange shows position(s) I have no record of. PAUSED."]
                    for p in rep["exchange"][:10]:
                        lines.append(f"• `{p.get('_key','?')}` size={_pos_size(p)}")
                    lines.append("Review/close, then `/resume`. Raw structure logged.")
                    await ch.send("\n".join(lines)[:1900])
        except Exception as e:
            print(f"[recon-loop] error: {e}")

@bot.event
async def on_ready():
    global stream,scanning
    await tree.sync();print(f"Bot ready as {bot.user}")
    stream=MarketStream(on_market_tick)
    bot.loop.create_task(stream.run())
    bot.loop.create_task(discovery_loop())
    bot.loop.create_task(resolve_loop())
    bot.loop.create_task(watchdog_loop())
    bot.loop.create_task(reconcile_loop())
    ch=bot.get_channel(ALERTS)
    # v16.15: startup reconciliation. Ask the exchange what we actually hold.
    # If there are positions with no local record (the orphaned-position case
    # that bit us tonight), PAUSE and alert — never silently scan/trade on top
    # of a picture we know is incomplete.
    try:
        rep=reconcile_report()
        if not rep["ok"]:
            scanning=False
            if ch:await ch.send("🛑 **Startup: could not verify exchange positions** "
                "(API/auth issue). Started PAUSED as a precaution. Use `/sync` to recheck, "
                "`/resume` once verified.")
        elif rep["n_exchange"]>0 and rep["n_local"]==0:
            scanning=False
            print(f"[startup-recon] ORPHAN raw sample: {rep['exchange'][0]}")
            lines=["🚨 **Startup: found exchange positions I have NO record of.** Started PAUSED."]
            for p in rep["exchange"][:10]:
                lines.append(f"• `{p.get('_key','?')}` size={_pos_size(p)}")
            lines.append("These are orphaned (no local entry data, so I can't manage TP/SL). "
                "Review/close them, then `/resume`. Raw structure logged for adoption support.")
            if ch:await ch.send("\n".join(lines)[:1900])
        elif rep["n_exchange"]>0:
            # exchange has positions AND we have local records — note it, keep running
            if ch:await ch.send(f"ℹ️ Startup reconciliation: {rep['n_exchange']} exchange position(s), "
                f"{rep['n_local']} local record(s). Use `/sync` for detail.")
    except Exception as e:
        print(f"[startup-recon] error: {e}")
    if ch:
        tot2,pe2,re2,w,l,pnl,wa2,ro2,ac2,lp,lt,dl2=get_summary()
        bal=get_balance() if auth_works else 0
        snaps=load_snaps()
        msg=(f"**THE SHARP v16.15 online (WebSocket)**\n"
            f"{'LIVE' if live_mode else 'PAPER'} | ${bal:.2f} | ${config['bet_size']} flat\n"
            f"aec- only | today only | {config['drop_threshold']:.0%} drop\n"
            f"Leagues: {', '.join(LEAGUES)}\n"
            f"TP/SL {config['take_profit']:.0%} symmetric | FOK + fill verified\n"
            f"WS: {WS_MARKETS} (<={MAX_INSTRUMENTS} instruments)\n\n"
            "/golive CONFIRM LIVE | /gopaper | /closeall\n"
            "/today /games /find /scan /top /debug /raw /reconcile\n"
            "/positions /history /leagues /status /balance\n"
            "/config /set /pause /resume /update /reset")
        if w+l>0:msg+=f"\n\nResume: ${pnl:+.2f} | {w}W-{l}L"
        msg+=f"\nSnapshots: {len(snaps)}"
        await ch.send(msg)

bot.run(TOKEN)
