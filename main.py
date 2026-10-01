import base64, hashlib, html, json, os, re, time
from cryptography.fernet import Fernet
from pathlib import Path
import pandas as pd
import requests, yaml
from jobspy import scrape_jobs

ROOT=Path(__file__).parent
CFG=yaml.safe_load((ROOT/"config.yaml").read_text())
DATA=ROOT/"data"; DATA.mkdir(exist_ok=True)
SEEN_FILE=DATA/"seen_jobs.json"
SUBSCRIBERS_FILE=DATA/"subscribers.enc"
TG_STATE=DATA/"telegram_state.json"

def load_json(path, default):
    try: return json.loads(path.read_text())
    except Exception: return default

seen=set(load_json(SEEN_FILE, []))
tg_state=load_json(TG_STATE, {"offset":0})
TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN")
DRY=os.environ.get("DRY_RUN","").lower() in {"1","true","yes"}

def clean(v):
    if v is None or (isinstance(v,float) and pd.isna(v)): return ""
    return str(v).strip()

def norm(v):
    v=clean(v).lower()
    v=re.sub(r"https?://\\S+","",v)
    v=re.sub(r"[^a-z0-9]+"," ",v)
    return " ".join(v.split())

def jid(row):
    # Cross-board identity: the same company/title/location should be one job even
    # when LinkedIn, Indeed, ZipRecruiter or Google expose different URLs.
    company=norm(row.get("company"))
    title=norm(row.get("title"))
    location=norm(row.get("location"))
    raw="|".join((company,title,location))
    if not company or not title:
        raw=norm(row.get("job_url_direct")) or norm(row.get("job_url")) or raw
    return hashlib.sha256(raw.encode()).hexdigest()[:20]

def cipher():
    key=base64.urlsafe_b64encode(hashlib.sha256(("tgjobhunter:subscribers:v1:"+TOKEN).encode()).digest())
    return Fernet(key)

def load_subscribers():
    if not TOKEN or not SUBSCRIBERS_FILE.exists(): return set()
    try: return set(json.loads(cipher().decrypt(SUBSCRIBERS_FILE.read_bytes())))
    except Exception as exc: raise RuntimeError("Subscriber state cannot be decrypted") from exc

def save_subscribers():
    if TOKEN: SUBSCRIBERS_FILE.write_bytes(cipher().encrypt(json.dumps(sorted(subscribers)).encode()))

subscribers=load_subscribers()

def tg(method, **payload):
    if not TOKEN: return None
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}",json=payload,timeout=30)
    r.raise_for_status()
    return r.json()

def salary_text(row):
    lo,hi,cur,intv=(clean(row.get(k)) for k in ("min_amount","max_amount","currency","interval"))
    if not lo and not hi:return ""
    val=f"{lo}{'–'+hi if hi else ''} {cur}".strip()
    return f"💰 {val}{'/'+intv if intv else ''}"

def annual_salary(row):
    try:
        lo=float(row.get("min_amount") or 0); hi=float(row.get("max_amount") or 0)
        val=hi or lo
        interval=clean(row.get("interval")).lower()
        if "hour" in interval: return val*2080
        if "month" in interval: return val*12
        if "week" in interval: return val*52
        if "year" in interval or "annual" in interval: return val
    except Exception: pass
    return None

def incompatible_language_job(row):
    """Reject jobs that explicitly require fluency/bilingual ability outside English or Russian."""
    title=clean(row.get("title")).lower()
    desc=clean(row.get("description")).lower()
    text=title+"\n"+desc

    # Language names commonly seen in bilingual job listings. English and Russian are allowed.
    other_languages=[
        "spanish","french","german","portuguese","mandarin","cantonese","chinese",
        "japanese","korean","arabic","hindi","urdu","punjabi","bengali","vietnamese",
        "tagalog","filipino","italian","polish","ukrainian","hebrew","yiddish",
        "creole","haitian creole","farsi","persian","turkish","greek"
    ]
    requirement_markers=[
        "bilingual","fluent","fluency","must speak","must be fluent","required language",
        "language required","proficiency in","proficient in","speaking required",
        "speaker required","speaking "
    ]

    # Titles such as "Bilingual Spanish Customer Service" are unambiguous requirements.
    if ("bilingual" in title or "fluent" in title) and any(lang in title for lang in other_languages):
        return True

    # Reject only when another language appears close to explicit requirement wording.
    for lang in other_languages:
        for marker in requirement_markers:
            patterns=(f"{marker} {lang}", f"{lang} {marker}", f"{lang}-speaking", f"{lang} speaking")
            if any(p in text for p in patterns):
                return True
    return False

def score(row):
    text=(" ".join(clean(row.get(k)) for k in ("title","description","company","location"))).lower()
    s=42; hits=[]
    for term,w in CFG["profile"]["strong_terms"].items():
        if term.lower() in text: s+=w; hits.append(term)
    for term,w in CFG["profile"]["penalties"].items():
        if term.lower() in text:s+=w
    title=clean(row.get("title")).lower()
    if any(x in title for x in ["operations","coordinator","customer","support","project","qa","web3","crypto","claims","client success"]):s+=8
    if "remote" in text:s+=5
    if "philadelphia" in text or re.search(r"\bpa\b",text):s+=4
    annual=annual_salary(row)
    if annual and annual>=CFG["profile"].get("preferred_min_salary",33280): s+=8; hits.append("pay ≥ $16/hr")
    # Penalize obviously low advertised pay; keep unknown-pay jobs eligible rather than guessing.
    if annual and annual<CFG["profile"].get("preferred_min_salary",33280): s-=45
    return max(0,min(99,s)),hits[:7]

def send_alert(row, category, s, hits):
    title=html.escape(clean(row.get("title")))
    company=html.escape(clean(row.get("company")) or "Company not listed")
    loc=html.escape(clean(row.get("location")) or "Location not listed")
    board_url=clean(row.get("job_url"))
    direct_url=clean(row.get("job_url_direct"))
    site=clean(row.get("site")).lower()
    url=(board_url or direct_url) if "indeed" in site else (direct_url or board_url)
    badge="🟢" if s>=CFG["profile"]["priority_score"] else "🟡"
    why=", ".join(hits) if hits else "role/location fit"
    sal=salary_text(row)
    msg=(f"{badge} <b>{s}% keyword match — {title}</b>\n"
         f"{company} | {loc}\n"
         f"{sal+' | ' if sal else ''}{html.escape(category)}\n"
         f"<b>Matched terms:</b> {html.escape(why)}\n"
         "For a tailored PDF, paste this job into our ChatGPT resume thread.")
    buttons=[]
    if url.startswith(("https://","http://")):
        buttons.append({"text":"VIEW / APPLY","url":url})
    if direct_url.startswith(("https://","http://")) and direct_url != url:
        buttons.append({"text":"DIRECT / BACKUP","url":direct_url})
    delivered=False
    for chat in sorted(subscribers):
        try:
            if DRY: print("DRY RUN",chat,msg)
            else: tg("sendMessage",chat_id=chat,text=msg,parse_mode="HTML",disable_web_page_preview=True,
                     reply_markup={"inline_keyboard":[buttons]} if buttons else None)
            delivered=True
        except Exception as exc: print("Delivery failed:",type(exc).__name__,str(exc)[:180])
    return delivered

def process_telegram_actions():
    if not TOKEN: return
    offset=int(tg_state.get("offset",0))
    response=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                          params={"offset":offset,"timeout":0,"allowed_updates":json.dumps(["message"])},timeout=30)
    response.raise_for_status()
    payload=response.json()
    if not payload.get("ok"): raise RuntimeError("Telegram getUpdates failed")
    for update in payload.get("result",[]):
        tg_state["offset"]=update["update_id"]+1
        msg=update.get("message") or {}
        chat=msg.get("chat") or {}
        if chat.get("type")!="private": continue
        chat_id=str(chat["id"])
        body=(msg.get("text") or "").strip().lower()
        if body.startswith("/stop"):
            subscribers.discard(chat_id)
            tg("sendMessage",chat_id=chat_id,text="Job alerts stopped. Message me again or send /start to subscribe.")
        elif body.startswith("/status"):
            tg("sendMessage",chat_id=chat_id,text="Your job alerts are "+("active." if chat_id in subscribers else "not active. Send /start to subscribe."))
        else:
            new=chat_id not in subscribers
            subscribers.add(chat_id)
            if new or body.startswith("/start"):
                tg("sendMessage",chat_id=chat_id,text="Subscribed! Matching jobs will arrive after each hourly scan. Send /stop to unsubscribe.")
        save_subscribers()
        TG_STATE.write_text(json.dumps(tg_state,indent=2))

def run():
    process_telegram_actions()
    if not subscribers and not DRY:
        print("No subscribers. Message the bot /start, then run the workflow again.")
        return
    tasks=[(category,location,term) for category,terms in CFG["categories"].items()
           for location in CFG["search"]["locations"] for term in terms]
    cursor=int(tg_state.get("search_cursor",0))%len(tasks)
    batch=min(int(CFG["search"].get("queries_per_run",8)),len(tasks))
    selected=[tasks[(cursor+i)%len(tasks)] for i in range(batch)]
    tg_state["search_cursor"]=(cursor+batch)%len(tasks)
    found=[]
    for category,location,term in selected:
        try:
            df=scrape_jobs(site_name=CFG["search"]["sites"],search_term=term,location=location,
                           results_wanted=CFG["search"]["results_per_query"],
                           hours_old=CFG["search"]["hours_old"],country_indeed="USA")
            if df is not None:
                for _,row in df.iterrows():
                    d=row.to_dict(); d["_category"]=category; found.append(d)
        except Exception as e: print(f"search failed {term} / {location}: {e}")
        time.sleep(.25)

    ranked={}
    language_filtered=0
    for row in found:
        if incompatible_language_job(row):
            language_filtered+=1
            continue
        key=jid(row); s,h=score(row)
        if key not in ranked or s>ranked[key][0]: ranked[key]=(s,h,row)
    sent=0
    for key,(s,h,row) in sorted(ranked.items(),key=lambda x:x[1][0],reverse=True):
        if key in seen or s<CFG["profile"]["alert_score"]: continue
        if send_alert(row,row["_category"],s,h):
            seen.add(key); sent+=1
        if sent>=CFG["search"].get("max_alerts_per_run",10): break
    SEEN_FILE.write_text(json.dumps(sorted(seen),indent=2))
    TG_STATE.write_text(json.dumps(tg_state,indent=2))
    save_subscribers()
    print(f"Unique found: {len(ranked)} | language-filtered: {language_filtered} | alerts: {sent} | seen total: {len(seen)}")

if __name__=="__main__":
    run()
