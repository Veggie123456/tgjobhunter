import hashlib, html, json, os, re, time
from pathlib import Path
import pandas as pd
import requests, yaml
from jobspy import scrape_jobs

ROOT=Path(__file__).parent
CFG=yaml.safe_load((ROOT/"config.yaml").read_text())
DATA=ROOT/"data"; DATA.mkdir(exist_ok=True)
SEEN_FILE=DATA/"seen_jobs.json"
JOBS_FILE=DATA/"jobs.json"
TG_STATE=DATA/"telegram_state.json"

def load_json(path, default):
    try: return json.loads(path.read_text())
    except Exception: return default

seen=set(load_json(SEEN_FILE, []))
jobs=load_json(JOBS_FILE, {})
tg_state=load_json(TG_STATE, {"offset":0})
TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT=os.environ.get("TELEGRAM_CHAT_ID")
DRY=os.environ.get("DRY_RUN","").lower() in {"1","true","yes"}

def clean(v):
    if v is None or (isinstance(v,float) and pd.isna(v)): return ""
    return str(v).strip()

def jid(row):
    raw="|".join(clean(row.get(k)) for k in ("site","company","title","job_url"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def resolve_chat_id():
    if CHAT: return str(CHAT)
    if not TOKEN: return None
    try:
        r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",params={"timeout":0},timeout=20)
        r.raise_for_status()
        for u in reversed(r.json().get("result",[])):
            msg=u.get("message") or u.get("edited_message") or {}
            if msg.get("chat",{}).get("id"): return str(msg["chat"]["id"])
    except Exception as e: print(f"Could not auto-detect Telegram chat: {e}")
    return None

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
    if annual and annual>=CFG["profile"].get("preferred_min_salary",45000): s+=5; hits.append("salary")
    return max(0,min(99,s)),hits[:7]

def store_job(row, category, s, hits):
    key=jid(row)
    jobs[key]={
      "id":key,"title":clean(row.get("title")),"company":clean(row.get("company")),
      "location":clean(row.get("location")),"description":clean(row.get("description")),
      "url":clean(row.get("job_url_direct")) or clean(row.get("job_url")),
      "site":clean(row.get("site")),"category":category,"score":s,"hits":hits,
      "salary":salary_text(row)
    }
    return key

def send_alert(row, category, s, hits):
    chat=resolve_chat_id()
    key=store_job(row,category,s,hits)
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
    if DRY or not TOKEN or not chat:
        print(re.sub("<[^>]+>","",msg),url); return
    buttons=[]
    if url: buttons.append({"text":"VIEW / APPLY","url":url})
    buttons.append({"text":"MAKE RESUME","callback_data":f"resume:{key}"})
    tg("sendMessage",chat_id=chat,text=msg,parse_mode="HTML",disable_web_page_preview=True,
       reply_markup={"inline_keyboard":[buttons]})

def send_resume(job_id, chat_id, callback_id=None):
    if callback_id:
        try: tg("answerCallbackQuery",callback_query_id=callback_id,text="Building your tailored resume…")
        except Exception: pass
    job=jobs.get(job_id)
    if not job:
        tg("sendMessage",chat_id=chat_id,text="I no longer have that job cached. Let the scanner find it again.")
        return
    if not os.environ.get("OPENAI_API_KEY"):
        tg("sendMessage",chat_id=chat_id,text="Resume generation is installed, but OPENAI_API_KEY still needs to be added to GitHub Actions secrets.")
        return
    try:
        from resume_engine import generate_resume
        path,data=generate_resume(job,DATA)
        with open(path,"rb") as f:
            r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                data={"chat_id":chat_id,"caption":f'Tailored for {job["title"]} at {job["company"]}'},
                files={"document":("Daniil Shurik Resume.pdf",f,"application/pdf")},timeout=90)
            r.raise_for_status()
    except Exception as e:
        print(f"Resume generation failed: {e}")
        tg("sendMessage",chat_id=chat_id,text=f"Resume generation failed: {str(e)[:300]}")

def process_telegram_actions():
    if not TOKEN: return
    offset=int(tg_state.get("offset",0))
    try:
        r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",params={"offset":offset,"timeout":0},timeout=20)
        r.raise_for_status()
        updates=r.json().get("result",[])
        for u in updates:
            tg_state["offset"]=u["update_id"]+1
            cq=u.get("callback_query")
            if cq:
                data=cq.get("data",""); chat_id=cq.get("message",{}).get("chat",{}).get("id")
                if data.startswith("resume:") and chat_id: send_resume(data.split(":",1)[1],chat_id,cq.get("id"))
        TG_STATE.write_text(json.dumps(tg_state,indent=2))
    except Exception as e: print(f"Telegram action processing failed: {e}")

def run():
    process_telegram_actions()
    found=[]
    for category,terms in CFG["categories"].items():
      for location in CFG["search"]["locations"]:
       for term in terms:
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
    for row in found:
        key=jid(row); s,h=score(row)
        if key not in ranked or s>ranked[key][0]: ranked[key]=(s,h,row)
    sent=0
    for key,(s,h,row) in sorted(ranked.items(),key=lambda x:x[1][0],reverse=True):
        if key in seen or s<CFG["profile"]["alert_score"]: continue
        send_alert(row,row["_category"],s,h)
        seen.add(key); sent+=1
        if sent>=25: break
    SEEN_FILE.write_text(json.dumps(sorted(seen),indent=2))
    if len(jobs)>500:\n        keep=list(jobs.items())[-500:]\n        jobs.clear(); jobs.update(dict(keep))\n    JOBS_FILE.write_text(json.dumps(jobs,indent=2))
    TG_STATE.write_text(json.dumps(tg_state,indent=2))
    print(f"Unique found: {len(ranked)} | alerts: {sent} | seen total: {len(seen)}")

if __name__=="__main__":
    run()
