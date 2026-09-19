import hashlib, html, json, os, re, time
from pathlib import Path
import pandas as pd
import requests, yaml
from jobspy import scrape_jobs

ROOT=Path(__file__).parent
CFG=yaml.safe_load((ROOT/"config.yaml").read_text())
SEEN_FILE=ROOT/"data"/"seen_jobs.json"
SEEN_FILE.parent.mkdir(exist_ok=True)
try: seen=set(json.loads(SEEN_FILE.read_text()))
except Exception: seen=set()

TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT=os.environ.get("TELEGRAM_CHAT_ID")
DRY=os.environ.get("DRY_RUN","").lower() in {"1","true","yes"}

def clean(v):
    if v is None or (isinstance(v,float) and pd.isna(v)): return ""
    return str(v).strip()

def jid(row):
    raw="|".join(clean(row.get(k)) for k in ("site","company","title","job_url"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]

def salary_text(row):
    lo,hi,cur,intv=(clean(row.get(k)) for k in ("min_amount","max_amount","currency","interval"))
    if not lo and not hi:return ""
    val=f"{lo}{'–'+hi if hi else ''} {cur}".strip()
    return f"💰 {val}{'/'+intv if intv else ''}"

def score(row):
    text=(" ".join(clean(row.get(k)) for k in ("title","description","company","location"))).lower()
    s=42
    hits=[]
    for term,w in CFG["profile"]["strong_terms"].items():
        if term.lower() in text:
            s+=w; hits.append(term)
    for term,w in CFG["profile"]["penalties"].items():
        if term.lower() in text:s+=w
    title=clean(row.get("title")).lower()
    if any(x in title for x in ["operations","coordinator","customer","support","project","qa","web3","crypto"]):s+=8
    if "remote" in text:s+=5
    if "philadelphia" in text or re.search(r"\bpa\b",text):s+=4
    return max(0,min(99,s)),hits[:6]

def send(row, category, s, hits):
    title=html.escape(clean(row.get("title")))
    company=html.escape(clean(row.get("company")) or "Company not listed")
    loc=html.escape(clean(row.get("location")) or "Location not listed")
    site=html.escape(clean(row.get("site")).replace("_"," ").title())
    url=clean(row.get("job_url_direct")) or clean(row.get("job_url"))
    badge="🟢" if s>=CFG["profile"]["priority_score"] else "🟡"
    why=", ".join(hits) if hits else "role/location fit"
    sal=salary_text(row)
    msg=(f"{badge} <b>{s}% MATCH — {title}</b>\n"
         f"{company} | {loc}\n"
         f"{sal+' | ' if sal else ''}{site}\n\n"
         f"<b>Category:</b> {html.escape(category)}\n"
         f"<b>Why it matched:</b> {html.escape(why)}")
    if DRY or not TOKEN or not CHAT:
        print(re.sub("<[^>]+>","",msg),url); return
    payload={"chat_id":CHAT,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True,
             "reply_markup":{"inline_keyboard":[[{"text":"VIEW / APPLY","url":url}]]}}
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json=payload,timeout=20)
    r.raise_for_status()

def run():
    found=[]
    for category,terms in CFG["categories"].items():
      for location in CFG["search"]["locations"]:
       for term in terms:
        try:
            df=scrape_jobs(site_name=CFG["search"]["sites"],search_term=term,location=location,
                           results_wanted=CFG["search"]["results_per_query"],
                           hours_old=CFG["search"]["hours_old"],country_indeed="USA")
            if df is None: continue
            for _,row in df.iterrows():
                d=row.to_dict(); d["_category"]=category; found.append(d)
        except Exception as e:
            print(f"search failed {term} / {location}: {e}")
        time.sleep(.4)

    # Highest score wins when the same posting appears in multiple searches.
    ranked={}
    for row in found:
        key=jid(row); s,h=score(row)
        if key not in ranked or s>ranked[key][0]: ranked[key]=(s,h,row)
    sent=0
    for key,(s,h,row) in sorted(ranked.items(), key=lambda x:x[1][0], reverse=True):
        if key in seen or s<CFG["profile"]["alert_score"]: continue
        send(row,row["_category"],s,h)
        seen.add(key); sent+=1
        if sent>=25: break
    SEEN_FILE.write_text(json.dumps(sorted(seen),indent=2))
    print(f"Unique found: {len(ranked)} | alerts: {sent} | seen total: {len(seen)}")

if __name__=="__main__":
    run()
