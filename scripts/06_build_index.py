import os, sys, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
from retrieval.bm25 import BM25Index, load_chunks
recs = load_chunks(os.path.join(BASE,"data","chunks.jsonl"), os.path.join(BASE,"data","chunks_fund.jsonl"))
print("records:", len(recs))
t=time.time(); idx = BM25Index().build(recs); print("build %.1fs, terms %d"%(time.time()-t, len(idx.inv)))
os.makedirs(os.path.join(BASE,"data","index"), exist_ok=True)
idx.save(os.path.join(BASE,"data","index","bm25.pkl"))
for q in ["연금저축이랑 IRP에 넣으면 세액공제 얼마까지 되나요? 다 합쳐서요",
          "전세보증금 때문에 중도인출 되나요?",
          "솔로몬 국공채 단기 중장기 장기 뭐가 달라요",
          "DC와 DB 퇴직금이 정해지는 방식이랑 운용 주체가 어떻게 다른가요"]:
    print("\nQ:", q)
    for r in idx.search(q, 4):
        print("   %.2f %-22s p%-3s %s | %s" % (r["_score"], r["doc_id"], r["page"], (r.get("section") or "")[:18], r["text"][:70].replace("\n"," ")))
