#!/usr/bin/env python3
import json, re, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/"config/sources.json"
DATA=ROOT/"data/news.json"

KEYWORDS={
"clinical_trial":["clinical trial","phase 1","phase 2","phase 3","randomized"],
"treatment":["treatment","therapy","drug","medication","disease modifying","dmt"],
"regulatory":["fda","ema","approved","approval","authorization"],
"research":["study","research","findings","results","trial","publication"],
"diagnosis":["diagnosis","biomarker","diagnostic"],
"rehabilitation":["rehabilitation","exercise","physiotherapy","physical activity","mobility"],
"symptoms":["symptom","fatigue","spasticity","pain","cognition"],
"quality_of_life":["quality of life","wellbeing","well-being"],
"nutrition":["vitamin d","diet","nutrition","supplement"]
}
WEIGHTS={"clinical_trial":30,"treatment":28,"regulatory":28,"research":24,"diagnosis":18,"rehabilitation":18,"symptoms":15,"quality_of_life":12,"nutrition":8}

def load(p): return json.loads(p.read_text(encoding="utf-8"))

def norm(url):
    if not url:return ""
    p=urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/"),"",""))

def aid(url,title):
    base=norm(url) or re.sub(r"\s+"," ",title.lower().strip())
    return hashlib.sha256(base.encode()).hexdigest()[:16]

def fetch(url):
    req=Request(url,headers={"User-Agent":"MS-News-Monitor/1.1"})
    with urlopen(req,timeout=20) as r:return r.read()

def child_text(node,names):
    for c in list(node):
        tag=c.tag.rsplit("}",1)[-1].lower()
        if tag in names and c.text:return " ".join(c.text.split())
    return ""

def parse(xml,source):
    root=ET.fromstring(xml); out=[]
    for node in root.iter():
        if node.tag.rsplit("}",1)[-1].lower() not in ("item","entry"):continue
        title=child_text(node,{"title"})
        link=child_text(node,{"link","guid","id"})
        for c in list(node):
            if not link and c.tag.rsplit("}",1)[-1].lower()=="link":link=c.attrib.get("href","")
        published=child_text(node,{"pubdate","published","updated","date"})
        summary=child_text(node,{"description","summary","content"})
        if title and link:
            out.append({"id":aid(link,title),"source_id":source["id"],"source":source["name"],"title":title,"url":link,"published_at":published,"summary":summary})
    return out

def published_dt(value):
    if not value:return None
    try:return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:return None

def score(a,priority):
    hay=(a["title"]+" "+a.get("summary","")).lower()
    value=priority*3; topics=[]
    for topic,words in KEYWORDS.items():
        hits=sum(w in hay for w in words)
        if hits:
            topics.append(topic); value+=min(12,hits*4)+WEIGHTS[topic]
    if "clinical_trial" in topics or "regulatory" in topics:value+=10
    return min(100,value),topics

def main():
    cfg=load(CONFIG); old=load(DATA)
    settings=cfg.get("settings",{})
    lookback=max(1,int(settings.get("lookback_days",14)))
    minimum=max(0,int(settings.get("minimum_score",0)))
    max_candidates=max(1,int(settings.get("max_candidates",10)))
    cutoff=datetime.now(timezone.utc)-timedelta(days=lookback)
    known={a["id"]:a for a in old.get("articles",[])}
    fetched=0; accepted=0
    for s in cfg.get("sources",[]):
        if not s.get("enabled") or not s.get("feed"):continue
        try:
            for a in parse(fetch(s["feed"]),s):
                a["score"],a["topics"]=score(a,s.get("priority",1))
                dt=published_dt(a.get("published_at"))
                if dt and dt < cutoff:
                    continue
                if a["score"] < minimum:
                    continue
                a["status"]=known.get(a["id"],{}).get("status","new")
                known[a["id"]]=a; fetched+=1; accepted+=1
        except Exception as e: print("[WARN]",s["name"],e)
    articles=sorted(known.values(),key=lambda a:(a.get("score",0),a.get("published_at","")),reverse=True)
    # Keep a bounded history while preserving editorial statuses.
    limit=max(max_candidates*10,100)
    articles=articles[:limit]
    DATA.write_text(json.dumps({"updated_at":datetime.now(timezone.utc).isoformat(),"articles":articles},ensure_ascii=False,indent=2),encoding="utf-8")
    print("Fetched:",fetched,"Accepted:",accepted,"Stored:",len(articles))

if __name__=="__main__":main()
