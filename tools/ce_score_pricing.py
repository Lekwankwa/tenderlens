"""Score the priced-schedule check against the key's planted arithmetic rows (development grades 1,3,6 and holdout 2,9,7).

Reads evaluation/ce_baseline/pricing/pricing_{dev,holdout}.json (written by the development scripts) and the verified keys.
A row is FOUND when a confirmed discrepancy is on the row's page (or its knock-on pages) and shares a figure with the key's
description of the change."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ce_verify_keys import parse_key, page_set
S="evaluation/ce_baseline/pricing/"
dev=json.load(open(S+"pricing_dev.json")); ho=json.load(open(S+"pricing_holdout.json"))
m=json.load(open("evaluation/ce_baseline/manifest.json")); root=Path(m["root"]); sc={str(s["grade"]):s for s in m["scenarios"]}
NUM=re.compile(r"\d{1,3}(?:[ ,]\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?")
def nums(t):
    out=set()
    for x in NUM.findall(t):
        try: out.add(float(re.sub(r"[ ,]","",x)))
        except ValueError: pass
    return {n for n in out if n>=100}                       # ignore small numbers (percentages, item numbers)
def near(a,b): return abs(a-b)<=1.0
def overlap(x,y): return any(near(a,b) for a in x for b in y)
def score(results,grades,label,verbose=True):
    tp=extra=planted=cleanfp=0; detail=[]; extras=[]
    for g in grades:
        key=parse_key(next(root/f["path"] for f in sc[g]["files"] if f["role"]=="answer_key"))
        rows=[d for d in key.defects if d.get("Type","").lower()=="arithmetic"]
        planted+=len(rows)
        conf=[d for d in results[f"{g}_tampered"] if d[3]]
        allp=[d for d in results[f"{g}_tampered"]]
        used=set()
        for r in rows:
            pages=set(page_set(re.sub(r"\(.*?knock-on:\s*([\d, ]+)\)",r", \1",r["Page"])))|set(page_set(r["Page"]))
            rn=nums(r["Exact modification"])
            hit=[i for i,d in enumerate(conf) if d[1] in pages and overlap(nums(d[5]),rn)]
            soft=[i for i,d in enumerate(allp) if d[1] in pages and overlap(nums(d[5]),rn) and not d[3]]
            if hit: tp+=1; used|=set(hit); detail.append((g,r["Test case"],"FOUND (confirmed)",conf[hit[0]][0]))
            elif soft: detail.append((g,r["Test case"],"seen, not confirmed (observation)",allp[soft[0]][0]))
            else: detail.append((g,r["Test case"],"missed",""))
        extra+=len(conf)-len(used); extras+=[(g,conf[i][1],conf[i][0],conf[i][4][:50]) for i in range(len(conf)) if i not in used]
        cleanfp+=sum(1 for d in results[f"{g}_clean"] if d[3])
    print(f"{label}: planted {planted} | FOUND and confirmed {tp} ({tp/planted:.0%}) | other confirmed on tampered packs {extra} | confirmed on CLEAN packs {cleanfp}")
    if verbose:
        for d in detail: print("   ",d)
    return tp,planted,extra,cleanfp
score(dev,["1","3","6"],"DEVELOPMENT (1,3,6)")
score(ho,["2","9","7"],"HOLDOUT (2,9,7)")
