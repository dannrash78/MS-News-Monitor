#!/usr/bin/env python3
import json, re, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/"config/sources.json"
DATA=ROOT/"data/news.json"

KEYWORDS={
"clinical_trial":["clinical trial","phase 1","phase 2","phase 3","randomized","placebo"],
"treatment":["treatment","therapy","drug","medication","disease modifying","dmt","ocrelizumab","ofatumumab","ublituximab","siponimod","cladribine"],
"regulatory":["fda","ema","approved","approval","authorization","label"],
"research":["study","research","findings","results","trial","publication","cohort","biomarker"],
"diagnosis":["diagnosis","biomarker","diagnostic","mcdonald criteria","early diagnosis"],
"rehabilitation":["rehabilitation","exercise","physiotherapy","physical activity","mobility","walking","gait"],
"symptoms":["symptom","fatigue","spasticity","pain","cognition","vision","bladder","weakness"],
"quality_of_life":["quality of life","wellbeing","well-being","daily life"],
"nutrition":["vitamin d","diet","nutrition","supplement","microbiome"]
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

def fetch(url,accept="*/*"):
    req=Request(url,headers={"User-Agent":"MS-News-Monitor/1.2 (+https://dannrash78.github.io/MS-News-Monitor/)","Accept":accept})
    with urlopen(req,timeout=25) as r:return r.read()

def child_text(node,names):
    for c in list(node):
        tag=c.tag.rsplit("}",1)[-1].lower()
        if tag in names and c.text:return " ".join(c.text.split())
    return ""

def parse_rss(xml,source):
    root=ET.fromstring(xml); out=[]
    for node in root.iter():
        if node.tag.rsplit("}",1)[-1].lower() not in ("item","entry"):continue
        title=child_text(node,{"title"})
        link=child_text(node,{"link","guid","id"})
        for c in list(node):
            if not link and c.tag.rsplit("}",1)[-1].lower()=="link":link=c.attrib.get("href","")
        published=child_text(node,{"pubdate","published","updated","date","dc:date"})
        summary=child_text(node,{"description","summary","content","encoded"})
        if title and link:
            out.append({"id":aid(link,title),"source_id":source["id"],"source":source["name"],"title":title,"url":link,"published_at":published,"summary":summary,"kind":"news"})
    return out

def published_dt(value):
    if not value:return None
    value=str(value).strip()
    try:return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:pass
    try:
        v=value.replace("Z","+00:00")
        dt=datetime.fromisoformat(v)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:pass
    for fmt in ("%Y %b %d","%Y %b","%Y/%m/%d","%Y-%m-%d"):
        try:return datetime.strptime(value,fmt).replace(tzinfo=timezone.utc)
        except Exception:continue
    return None

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else ""

def score(a,priority):
    hay=(a.get("title","")+" "+a.get("summary","")).lower()
    value=priority*3; topics=[]
    for topic,words in KEYWORDS.items():
        hits=sum(w in hay for w in words)
        if hits:
            topics.append(topic); value+=min(12,hits*4)+WEIGHTS[topic]
    if "clinical_trial" in topics or "regulatory" in topics:value+=10
    return min(100,value),topics

def fetch_pubmed(source,cutoff):
    term=source.get("query","multiple sclerosis")
    days=max(1,(datetime.now(timezone.utc)-cutoff).days+2)
    params={"db":"pubmed","term":f"({term}) AND (last {days} days[dp])","retmax":"30","sort":"pub date","retmode":"json"}
    raw=json.loads(fetch(source["feed"]+"esearch.fcgi?"+urlencode(params),"application/json"))
    ids=raw.get("esearchresult",{}).get("idlist",[])
    if not ids:return []
    summ=json.loads(fetch(source["feed"]+"esummary.fcgi?"+urlencode({"db":"pubmed","id":",".join(ids),"retmode":"json"}),"application/json"))
    out=[]
    for pid in ids:
        item=summ.get("result",{}).get(pid,{})
        title=item.get("title","").strip()
        if not title:continue
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pid}/"
        dt=published_dt(item.get("pubdate"))
        out.append({"id":aid(url,title),"source_id":source["id"],"source":source["name"],"title":title,"url":url,"published_at":iso(dt),"summary":"PubMed record; abstract and full publication details available at the source.","kind":"publication","external_id":pid})
    return out

def fetch_trials(source,cutoff):
    params={"query.cond":source.get("query","Multiple Sclerosis"),"pageSize":"30","format":"json","query.term":f"AREA[LastUpdatePostDate]RANGE[{cutoff.strftime('%Y-%m-%d')},MAX]"}
    raw=json.loads(fetch(source["feed"]+"?"+urlencode(params),"application/json"))
    out=[]
    for study in raw.get("studies",[]):
        p=study.get("protocolSection",{})
        ident=p.get("identificationModule",{})
        status=p.get("statusModule",{})
        desc=p.get("descriptionModule",{})
        nct=ident.get("nctId","")
        title=ident.get("briefTitle","").strip()
        if not nct or not title:continue
        url=f"https://clinicaltrials.gov/study/{nct}"
        dt=published_dt(status.get("lastUpdatePostDateStruct",{}).get("date"))
        summary=desc.get("briefSummary","").strip()
        out.append({"id":aid(url,title),"source_id":source["id"],"source":source["name"],"title":title,"url":url,"published_at":iso(dt),"summary":summary,"kind":"clinical_trial","external_id":nct})
    return out

def fetch_html(source,cutoff):
    # HTML-only sources are intentionally left out of the automated parser until a stable feed/API is available.
    return []

def main():
    cfg=load(CONFIG); old=load(DATA)
    settings=cfg.get("settings",{})
    lookback=max(1,int(settings.get("lookback_days",30)))
    minimum=max(0,int(settings.get("minimum_score",0)))
    max_candidates=max(1,int(settings.get("max_candidates",10)))
    cutoff=datetime.now(timezone.utc)-timedelta(days=lookback)
    known={a["id"]:a for a in old.get("articles",[])}
    fetched=accepted=0
    errors=[]
    for s in cfg.get("sources",[]):
        if not s.get("enabled"):continue
        try:
            kind=s.get("type","rss")
            if kind=="rss":
                items=parse_rss(fetch(s["feed"],"application/rss+xml, application/atom+xml, application/xml, text/xml"),s) if s.get("feed") else []
            elif kind=="pubmed":
                items=fetch_pubmed(s,cutoff)
            elif kind=="clinicaltrials":
                items=fetch_trials(s,cutoff)
            elif kind=="html":
                items=fetch_html(s,cutoff)
            else:
                items=[]
            for a in items:
                dt=published_dt(a.get("published_at"))
                if dt and dt < cutoff:continue
                a["score"],a["topics"]=score(a,s.get("priority",1))
                if a["score"] < minimum:continue
                a["status"]=known.get(a["id"],{}).get("status","new")
                known[a["id"]]=a; fetched+=1; accepted+=1
        except Exception as e:
            msg=f"{s.get('name','source')}: {e}"
            errors.append(msg); print("[WARN]",msg)
    articles=sorted(known.values(),key=lambda a:(a.get("score",0),a.get("published_at","")),reverse=True)
    limit=max(max_candidates*10,100)
    articles=articles[:limit]
    payload={"updated_at":datetime.now(timezone.utc).isoformat(),"articles":articles,"source_errors":errors}
    DATA.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Fetched:",fetched,"Accepted:",accepted,"Stored:",len(articles),"Errors:",len(errors))

if __name__=="__main__":main()
