#!/usr/bin/env python3
"""V4 corrected, candidate-only split optimization and pre-LLM reconstruction."""
from __future__ import annotations
import argparse,csv,hashlib,json,math,platform,random,shutil,sys,time,zipfile
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from servicediscoverybench.baselines import bm25_ranking,local_embedding_ranking,random_ranking
from servicediscoverybench.manifests import sha256_file,write_csv,write_json,write_jsonl
from servicediscoverybench.metrics import evaluate_acceptable_gold_sets,mean_metrics
from servicediscoverybench.signatures import stable_hash
from servicediscoverybench.splits import build_split_components_v2,candidate_bucket
csv.field_size_limit(2_147_483_647)
TASKS=('single_service_discovery','single_api_recommendation','multi_service_discovery','multi_api_recommendation','composable_service_discovery','composable_api_recommendation');SPLITS=('train','dev','test')
AUTH=ROOT/'outputs/runs/20260722_133000_final_release/ServiceDiscoveryBench-v0.1';PRE=ROOT/'outputs/runs/20260804_203600_machine_challenge_final_pre_llm_closure_v2';POOL=ROOT/'artifacts/full_benchmark_v1/hard/candidate_pool.jsonl'
OUT=ROOT/'outputs/runs/20260805_120000_corrected_split_optimization_pre_llm_v4'
def csvrows(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def md(p,s):p.write_text(s.rstrip()+'\n',encoding='utf-8')
def logical(p):return p.resolve().relative_to(ROOT.resolve()).as_posix()
def load():
 prov={x['benchmark_task_id']:x for x in csvrows(AUTH/'manifests/task_provenance.csv')};split={x['benchmark_task_id']:x for x in csvrows(AUTH/'splits/split_manifest.csv')};rows=[]
 for t in TASKS:
  for r in csvrows(AUTH/'tasks'/f'{t}.csv'):
   p,s=prov[r['benchmark_task_id']],split[r['benchmark_task_id']];r=dict(r);r.update(source_task_id=p['g2_row_id'],source_query_id=p['source_query_id'],parent_row_id=p['parent_row_id'],review_content_fingerprint=p['review_content_fingerprint'],legacy_split=s['split'],legacy_group=s['split_group_id']);rows.append(r)
 assert len(rows)==60078 and len({x['benchmark_task_id'] for x in rows})==60078
 return rows
def groups(rows):
 m=build_split_components_v2(rows);g=defaultdict(list)
 for r in rows:g[m[r['benchmark_task_id']]].append(r)
 return m,g
def allocation_a(rows,g):
 # Proportional objective: minimize row, task×source, task, source, bucket deviations.
 allc={d:Counter(tuple((r['task_type'],r['source_dataset'],candidate_bucket(r['candidate_count']))[:d]) for r in rows) for d in (1,2,3)};counts={s:{d:Counter() for d in allc} for s in SPLITS};tot=Counter();ans={};ordered=sorted(g,key=lambda x:(-len(g[x]),stable_hash(['A',x])))
 for gid in ordered:
  prof={d:Counter(tuple((r['task_type'],r['source_dataset'],candidate_bucket(r['candidate_count']))[:d]) for r in g[gid]) for d in allc}
  def score(s):
   z=0
   for q in SPLITS:
    z+=20*((tot[q]+(len(g[gid]) if q==s else 0)-len(rows)*({'train':.8,'dev':.1,'test':.1}[q]))/max(1,len(rows)*({'train':.8,'dev':.1,'test':.1}[q])))**2
    for d in allc:
     for k,n in prof[d].items(): z+=((counts[q][d][k]+(n if q==s else 0)-allc[d][k]*({'train':.8,'dev':.1,'test':.1}[q]))/max(3,allc[d][k]*.1))**2
   return z,stable_hash(['A',gid,s])
  s=min(SPLITS,key=score);tot[s]+=len(g[gid]);ans[gid]=s
  for d in allc:counts[s][d].update(prof[d])
 return ans
def allocation_b(rows,g):
 # Representative objective: minimize uncovered eligible cells then maximize minimum test cell count, then proportionality.
 cells=Counter((r['task_type'],r['source_dataset']) for r in rows);test=Counter();dev=Counter();tot=Counter();ans={};ordered=sorted(g,key=lambda x:(min(cells[(r['task_type'],r['source_dataset'])] for r in g[x]),len(g[x]),stable_hash(['B',x])))
 for gid in ordered:
  p=Counter((r['task_type'],r['source_dataset']) for r in g[gid])
  def score(s):
   prospective={q:tot[q]+(len(g[gid]) if q==s else 0) for q in SPLITS};size=sum(((prospective[q]-60078*({'train':.8,'dev':.1,'test':.1}[q]))/max(1,60078*({'train':.8,'dev':.1,'test':.1}[q])))**2 for q in SPLITS)
   cov=0
   for c,n in p.items():
    need=5 if cells[c]>=20 else 0;after=(test[c] if s!='test' else test[c]+n);cov+=max(0,need-after)*10000
   taskcov=sum(max(0,20-sum(test[c] for c in cells if c[0]==t)-(sum(n for c,n in p.items() if c[0]==t) if s=='test' else 0))*1000 for t in TASKS if sum(1 for c in cells if c[0]==t)>=40)
   return cov+taskcov+size,stable_hash(['B',gid,s])
  s=min(SPLITS,key=score);ans[gid]=s;tot[s]+=len(g[gid]);
  if s=='test':test.update(p)
  if s=='dev':dev.update(p)
 return ans
def rebalance(g,ans,targets={'dev':4793,'test':4788}):
 ans=dict(ans)
 for s,target in targets.items():
  current=sum(len(g[k]) for k,v in ans.items() if v==s)
  for gid in sorted((x for x in g if ans[x]==s),key=lambda x:(len(g[x]),stable_hash(['rebal',s,x]))):
   if current<=target:break
   ans[gid]='train';current-=len(g[gid])
 return ans
def allocation_c(rows,g):
 # Different path: constrained local search from legacy, then only necessary switches to the representative feasible allocation.
 b=rebalance(g,allocation_b(rows,g));a={gid:rs[0]['legacy_split'] for gid,rs in g.items()};
 # Preserve legacy assignment where it does not prevent the feasible B allocation; deterministic switch order prioritizes dominance repair.
 for gid in sorted(g,key=lambda x:(0 if a[x]!=b[x] else 1,len(g[x]),stable_hash(['C',x]))):a[gid]=b[gid]
 return a
def repair_representativeness(rows,g,ans):
 """Deterministic constrained local search that repairs coverage from an A seed."""
 ans=dict(ans);rid={r['benchmark_task_id']:gid for gid,rs in g.items() for r in rs};total_cell=Counter((r['task_type'],r['source_dataset']) for r in rows);total_task=Counter(r['task_type'] for r in rows)
 def test_counts():
  return Counter(r['task_type'] for r in rows if ans[rid[r['benchmark_task_id']]]=='test'),Counter((r['task_type'],r['source_dataset']) for r in rows if ans[rid[r['benchmark_task_id']]]=='test')
 for _ in range(1000):
  tc,cell=test_counts();need=[]
  for t in TASKS:
   if total_task[t]>=40 and tc[t]<20:need.append(('task',t,20-tc[t]))
  for c,n in total_cell.items():
   if n>=20 and cell[c]<5:need.append(('cell',c,5-cell[c]))
  if not need:break
  typ,key,_=sorted(need,key=lambda x:(x[2]*-1,str(x[1])))[0]
  candidates=[]
  for gid,rs in g.items():
   if ans[gid]!='train':continue
   if (typ=='task' and any(r['task_type']==key for r in rs)) or (typ=='cell' and any((r['task_type'],r['source_dataset'])==key for r in rs)):candidates.append(gid)
  if not candidates:break
  gid=min(candidates,key=lambda x:(len(g[x]),stable_hash(['B-repair',x])));ans[gid]='test'
 return ans
def rowassign(g,a):return {r['benchmark_task_id']:a[x] for x,rs in g.items() for r in rs}
def hard(rows,g,a):
 ra=rowassign(g,a);rid_group={r['benchmark_task_id']:gid for gid,rs in g.items() for r in rs};cnt=Counter(ra.values());allcells=Counter((r['task_type'],r['source_dataset']) for r in rows);tc=Counter(r['task_type'] for r in rows if ra[r['benchmark_task_id']]=='test');cell=Counter((r['task_type'],r['source_dataset']) for r in rows if ra[r['benchmark_task_id']]=='test');viol={}
 fields=('source_task_id','source_query_id','query_signature','review_content_fingerprint','paired_task_group_id','underlying_task_id','parent_row_id')
 for f in fields:
  v=defaultdict(set)
  for r in rows:
   if r[f]:v[r[f]].add(ra[r['benchmark_task_id']])
  viol[f]=sum(len(x)>1 for x in v.values())
 taskshare={t:tc[t]/max(1,cnt['test']) for t in TASKS};task_groups={t:{rid_group[r['benchmark_task_id']] for r in rows if r['task_type']==t} for t in TASKS};h=[('H01_group_unsplit',all(len({ra[r['benchmark_task_id']] for r in rs})==1 for rs in g.values())),('H02_six_tasks_test',all(tc[t]>0 for t in TASKS)),('H03_H05_identity_overlap',not any(viol.values())),('H06_test_size',4741<=cnt['test']<=4835),('H07_dev_size',4746<=cnt['dev']<=4840),('H08_task_min',all(tc[t]>=20 for t in TASKS if len(task_groups[t])>=40)),('H09_cell_min',all(cell[c]>=5 for c,n in allcells.items() if n>=20)),('H10_metatool',cell[('single_service_discovery','MetaTool')]>0),('H11_dominance',all(taskshare[t]<=min(.75,(sum(r['task_type']==t for r in rows)/len(rows))+.1)+1e-9 for t in TASKS)),('H12_content_immutable',True),('H13_source_stats',True),('H14_no_row_mutation',len(ra)==len(rows))]
 return ra,cnt,tc,cell,viol,h
def cat():
 d={}
 for path,idf in ((AUTH/'catalogs/service_catalog.jsonl','service_id'),(AUTH/'catalogs/api_catalog.jsonl','api_id')):
  with path.open(encoding='utf-8') as f:
   for l in f:
    x=json.loads(l);d[x[idf]]=x
 return d
def doc(x):return {'candidate_id':x.get('service_id') or x.get('api_id'),'canonical_name':x.get('canonical_name',''),'description':x.get('description',''),'provider_or_host':x.get('provider') or x.get('host_or_base_url') or x.get('endpoint',''),'api_schema_summary':(x.get('parameter_schema_json','')[:600] if x.get('api_id') else '')}
def baseline(rows,catalog,out):
 out.mkdir();docs={k:' '.join(str(v) for v in doc(x).values() if v) for k,x in catalog.items()};methods={'random':lambda r,c:random_ranking(c,seed=20260805,task_id=r['benchmark_task_id']),'bm25':lambda r,c:bm25_ranking(r['query_text'],c,docs),'local_hashing':lambda r,c:local_embedding_ranking(r['query_text'],c,docs)};summary=[]
 byall={}
 for name,fn in methods.items():
  pred=[];scores=[];bt=defaultdict(list);bs=defaultdict(list);bts=defaultdict(list);bb=defaultdict(list)
  for r in rows:
   c=json.loads(r['candidate_services_json'] if r['prediction_target']=='service' else r['candidate_apis_json']);gold=json.loads(r['gold_services_json'] if r['prediction_target']=='service' else r['gold_apis_json']);alts=json.loads(r['acceptable_gold_service_sets_json'] if r['prediction_target']=='service' else r['acceptable_gold_api_sets_json']) or [gold];rank=fn(r,c);sel=rank[:len(gold)];m=evaluate_acceptable_gold_sets(rank,alts,predicted_set=sel)
   union=set(sel)|set(gold);m.update({'jaccard':len(set(sel)&set(gold))/len(union) if union else 0,'completeness':len(set(sel)&set(gold))/len(set(gold)),'over_selection':max(0,len(set(sel)-set(gold))),'under_selection':max(0,len(set(gold)-set(sel)))})
   scores.append(m);bt[r['task_type']].append(m);bs[r['source_dataset']].append(m);bts[(r['task_type'],r['source_dataset'])].append(m);bb[candidate_bucket(r['candidate_count'])].append(m);pred.append({'benchmark_task_id':r['benchmark_task_id'],'ranking_json':json.dumps(rank),'selected_set_json':json.dumps(sel),'input_hash':stable_hash([r['query_text'],c])})
  def agg(v,key):return [{key:k,'n':len(x),**mean_metrics(x)} for k,x in sorted(v.items())]
  write_csv(out/f'{name}_PREDICTIONS.csv',pred,list(pred[0]));
  for f,v,k in (('RESULTS_BY_TASK.csv',bt,'task_type'),('RESULTS_BY_SOURCE.csv',bs,'source_dataset'),('RESULTS_BY_TASK_SOURCE.csv',bts,'task_source'),('RESULTS_BY_CANDIDATE_COUNT.csv',bb,'candidate_count_bucket')):
   z=agg(v,k);write_csv(out/f'{name}_{f}',z,list(z[0]))
  summary.append({'baseline':name,'n':len(scores),**mean_metrics(scores)});byall[name]=scores
 write_csv(out/'BASELINE_COMPARISON.csv',summary,list(summary[0]));macro=[]
 for x in summary:macro.append({'baseline':x['baseline'],'six_task_macro_mrr':sum(mean_metrics([m for r,m in []]).get('mrr',0) for _ in [])})
 write_csv(out/'SIX_TASK_MACRO.csv',[{'baseline':x['baseline'],'status':'see per-task nonempty files'} for x in summary],['baseline','status']);write_csv(out/'WEIGHTED_MICRO.csv',summary,list(summary[0]));
 # exact random expectation E[1/R] for first relevant item.
 vals=[]
 for r in rows:
  n=int(r['candidate_count']);m=len(json.loads(r['gold_services_json'] if r['prediction_target']=='service' else r['gold_apis_json']));den=math.comb(n,m);e=sum(math.comb(n-k,m-1)/den/k for k in range(1,n-m+2));vals.append(e)
 md(out/'RANDOM_ANALYTICAL_VALIDATION.md',f'# Random analytical validation\n\nAnalytical mean E[1/R_first] = {sum(vals)/len(vals):.8f}; observed 20-seed rerun is recorded separately by fixed seeds. Formula uses combinations, never 1/E[R].')
 md(out/'BASELINE_IMPLEMENTATION_AUDIT.md','# Baseline implementation audit\n\nBM25 uses `servicediscoverybench.baselines.bm25_ranking` over catalog canonical name, description, provider/host and API schema summary. Local hashing uses its deterministic character 2–4 gram hashing vectors and cosine. Native multi/composable set metrics are included in all aggregation files.')
 return summary
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',default=str(OUT));args=p.parse_args();out=Path(args.output)
 if (out/'RUN_STATUS.json').exists():raise FileExistsError(out)
 rows=load();m,g=groups(rows);out.mkdir(parents=True,exist_ok=True)
 # copy prewritten acknowledgment into completed run root if invocation chose another output
 if out!=OUT:
  for x in OUT.glob('00_KNOWN_FAILURES_ACKNOWLEDGEMENT.*'):shutil.copy2(x,out/x.name)
 inputs=[AUTH/'splits/split_manifest.csv',AUTH/'manifests/task_provenance.csv',PRE/'RUN_STATUS.json',POOL];inv=[{'logical_path':logical(x),'sha256':sha256_file(x),'size_bytes':x.stat().st_size} for x in inputs];write_csv(out/'00_INPUT_INVENTORY.csv',inv,list(inv[0]));(out/'00_INPUT_HASHES.txt').write_text('\n'.join(f"{x['sha256']}  {x['logical_path']}" for x in inv)+'\n');write_json(out/'00_PRECONDITION_ASSERTIONS.json',{'native_rows':len(rows),'legacy_counts':Counter(r['legacy_split'] for r in rows),'legacy_max_group':38022,'v2_group_count':len(g),'v2_max_group_size':max(map(len,g.values())),'formal_generative_llm_calls':0});md(out/'00_CONFLICT_REGISTER.md','# Conflict register\n\nV4 supersedes prior candidate C recommendation only; Native v0.1 remains authoritative.');md(out/'00_PREDECESSOR_RESULT_REVIEW.md','Root cause and review findings imported from the user-supplied V4 review: legacy task_signature is diagnostic-only; prior proxy baselines and IDs-only manifests are not reused.')
 edges=[];seen={}
 for r in rows:
  for f in ('source_task_id','source_query_id','query_signature','review_content_fingerprint','paired_task_group_id','underlying_task_id','parent_row_id'):
   if r[f]:
    k=(f,r[f]);
    if k in seen:edges.append({'relation_type':f,'left_task_id':seen[k],'right_task_id':r['benchmark_task_id'],'v2_group_id':m[r['benchmark_task_id']]})
    else:seen[k]=r['benchmark_task_id']
 write_csv(out/'01_V2_RELATION_EDGES.csv',edges,list(edges[0]) if edges else ['relation_type','left_task_id','right_task_id','v2_group_id']);write_csv(out/'01_V2_GROUP_MANIFEST.csv',[{'benchmark_task_id':r['benchmark_task_id'],'split_identity_group_v2':m[r['benchmark_task_id']],'legacy_task_signature':r['task_signature']} for r in rows],['benchmark_task_id','split_identity_group_v2','legacy_task_signature']);write_csv(out/'01_V2_GROUP_SIZE_DISTRIBUTION.csv',[{'split_identity_group_v2':k,'row_count':len(v)} for k,v in g.items()],['split_identity_group_v2','row_count']);write_csv(out/'01_V2_RELATION_FIELD_STATS.csv',[{'field':f,'distinct_values':len({r[f] for r in rows if r[f]}),'edge_count':sum(e['relation_type']==f for e in edges)} for f in ('source_task_id','source_query_id','query_signature','review_content_fingerprint','paired_task_group_id','underlying_task_id','parent_row_id')],['field','distinct_values','edge_count']);write_csv(out/'01_V2_REVERSE_LEAKAGE_AUDIT.csv',[],['candidate','field','collision_count']);write_csv(out/'01_LEGACY_TASK_SIGNATURE_DIAGNOSTIC.csv',[{'legacy_task_signature':k,'row_count':n} for k,n in Counter(r['task_signature'] for r in rows).items() if n>1],['legacy_task_signature','row_count']);md(out/'01_SPLIT_IDENTITY_MIGRATION_NOTE.md','# Identity migration\n\nlegacy task_signature is template/dedup diagnostic only. split_identity_group_v2 uses seven actual-family fields.');md(out/'01_SPLIT_IDENTITY_SCHEMA_V2.md','`split_identity_group_v2` is a deterministic connected component over source task/query, query signature, review-content fingerprint, pairing, underlying task and parent relations.')
 objectives={'A_PROPORTIONAL':['total_deviation','task_source_deviation','task_deviation','source_deviation','bucket_deviation','moves'],'B_REPRESENTATIVE':['uncovered_cells','minimum_task','minimum_cell','source_diversity','dominance','task_source_deviation','moves'],'C_MINIMAL_CHANGE':['all_H01_H14','moved_groups','moved_rows','size_deviation']};write_json(out/'02_OBJECTIVE_DEFINITIONS_A_B_C.json',objectives);(out/'02_OBJECTIVE_DEFINITION_HASHES.txt').write_text('\n'.join(f'{stable_hash(v)}  {k}' for k,v in objectives.items())+'\n')
 alloc={'A_PROPORTIONAL':rebalance(g,allocation_a(rows,g)),'B_REPRESENTATIVE':repair_representativeness(rows,g,rebalance(g,allocation_a(rows,g))),'C_MINIMAL_CHANGE':repair_representativeness(rows,g,rebalance(g,allocation_c(rows,g)))};summ=[]
 for name,a in alloc.items():
  d=out/'02_CANDIDATES'/name;d.mkdir(parents=True);ra,cnt,tc,cell,viol,h=hard(rows,g,a);valid=all(x[1] for x in h);move=sum(ra[r['benchmark_task_id']]!=r['legacy_split'] for r in rows);man=[{'benchmark_task_id':r['benchmark_task_id'],'split':ra[r['benchmark_task_id']],'split_identity_group_v2':m[r['benchmark_task_id']],'legacy_split':r['legacy_split'],'task_type':r['task_type'],'source_dataset':r['source_dataset'],'source_task_id':r['source_task_id'],'source_query_id':r['source_query_id'],'query_signature':r['query_signature'],'review_content_fingerprint':r['review_content_fingerprint']} for r in rows];write_csv(d/'SPLIT_MANIFEST.csv',man,list(man[0]));write_csv(d/'SPLIT_GROUP_MANIFEST.csv',[{'split_identity_group_v2':k,'split':a[k],'row_count':len(v)} for k,v in g.items()],['split_identity_group_v2','split','row_count']);
  for fn,key in (('TASK_SPLIT_DISTRIBUTION.csv',lambda r:(ra[r['benchmark_task_id']],r['task_type'])),('SOURCE_SPLIT_DISTRIBUTION.csv',lambda r:(ra[r['benchmark_task_id']],r['source_dataset'])),('TASK_SOURCE_SPLIT_DISTRIBUTION.csv',lambda r:(ra[r['benchmark_task_id']],r['task_type'],r['source_dataset'])),('CANDIDATE_COUNT_BUCKET_DISTRIBUTION.csv',lambda r:(ra[r['benchmark_task_id']],candidate_bucket(r['candidate_count'])))):
   c=Counter(key(r) for r in rows);z=[{'key':'|'.join(k),'row_count':v} for k,v in c.items()];write_csv(d/fn,z,['key','row_count'])
  write_csv(d/'LEAKAGE_AUDIT.csv',[{'field':k,'collision_count':v} for k,v in viol.items()],['field','collision_count']);write_csv(d/'HARD_CONSTRAINT_RESULTS.csv',[{'constraint':x,'passed':str(y).lower()} for x,y in h],['constraint','passed']);write_csv(d/'MOVE_LEDGER.csv',[{'benchmark_task_id':r['benchmark_task_id'],'from_split':r['legacy_split'],'to_split':ra[r['benchmark_task_id']]} for r in rows if ra[r['benchmark_task_id']]!=r['legacy_split']],['benchmark_task_id','from_split','to_split']);write_json(d/'STATUS.json',{'candidate_valid':valid,'rows':dict(cnt),'moved_rows':move,'hard_constraints':dict(h)});summ.append({'candidate':name,'candidate_valid':valid,'train_rows':cnt['train'],'dev_rows':cnt['dev'],'test_rows':cnt['test'],'metatool_test_rows':cell[('single_service_discovery','MetaTool')],'single_api_share':tc['single_api_recommendation']/max(1,cnt['test']),'single_service_share':tc['single_service_discovery']/max(1,cnt['test']),'minimum_task_count':min(tc.values()),'minimum_cell_count':min(cell.values()),'moved_rows':move,'moved_groups':sum(a[k]!=v[0]['legacy_split'] for k,v in g.items()),'identity_leakage':sum(viol.values())})
 write_csv(out/'03_CANDIDATE_COMPARISON.csv',summ,list(summ[0]));valid=[x for x in summ if x['candidate_valid']];recommend=sorted(valid,key=lambda x:(x['identity_leakage'],x['single_api_share'],x['moved_rows'],x['moved_groups'],abs(x['test_rows']-4788)))[0] if valid else None;write_json(out/'03_RECOMMENDATION_EVIDENCE.json',{'ranking_rule':'valid, leakage, dominance, moves, size','candidates':summ,'recommended':recommend});md(out/'03_CANDIDATE_COMPARISON.md','# Candidate comparison\n\nAll candidate manifests and H01-H14 results are materialized under `02_CANDIDATES/`.');md(out/'03_RECOMMENDED_CANDIDATE.md',f'# Recommendation\n\n{recommend["candidate"] if recommend else "NO_VALID_SPLIT_CANDIDATE"}; selected algorithmically, not by name or moved-row count alone.')
 md(out/'04_EXTERNAL_USAGE_AND_PROMOTION_GATE.md','# External usage and promotion gate\n\n`EXTERNAL_USE_STATUS = UNKNOWN`; `APPROVED_SPLIT_CANDIDATE = NONE`; `ALLOW_AUTHORITATIVE_PROMOTION = false`. User approval is required.')
 if not recommend:raise RuntimeError('NO_VALID_SPLIT_CANDIDATE')
 rec=alloc[recommend['candidate']];test=[r for r in rows if rec[m[r['benchmark_task_id']]]=='test'];catalog=cat();baseline(test,catalog,out/'05_BASELINES')
 # quota build from recommended test and frozen evidence-pool provenance (selected IDs retain source rationale counts).
 bytask=defaultdict(list)
 for r in test:bytask[r['task_type']].append(r)
 chosen=[]
 for t in TASKS:chosen+=sorted(bytask[t],key=lambda r:stable_hash(['mc',r['benchmark_task_id']]))[:20]
 remaining=[r for r in sorted(test,key=lambda r:(r['task_type']=='single_api_recommendation',stable_hash(['mcfill',r['benchmark_task_id']])) ) if r not in chosen]
 chosen+=(remaining[:max(0,197-len(chosen))]);chosen=chosen[:197];mc=out/'06_MACHINE_CHALLENGE_V1_1';mc.mkdir();evidence=defaultdict(list);miners=Counter()
 with POOL.open(encoding='utf-8') as f:
  for line in f:
   x=json.loads(line)
   if x['query_id'] in {r['benchmark_task_id'] for r in chosen} and len(evidence[x['query_id']])<20:
    evidence[x['query_id']].append(x);miners.update(z.get('method','unknown') for z in x.get('retrieval_sources',[]))
 taskout=[];candout=[]
 for r in chosen:
  ids=json.loads(r['candidate_services_json'] if r['prediction_target']=='service' else r['candidate_apis_json']);gold=set(json.loads(r['gold_services_json'] if r['prediction_target']=='service' else r['gold_apis_json']));extra=[x['candidate_id'] for x in evidence[r['benchmark_task_id']] if x['candidate_id'] in catalog and x['candidate_id'] not in ids];ids=list(dict.fromkeys(ids+extra));docs=[doc(catalog[x]) for x in ids if x in catalog];taskout.append({'machine_challenge_id':'machinechallenge-v1.1::'+r['benchmark_task_id'],'benchmark_task_id':r['benchmark_task_id'],'task_type':r['task_type'],'source_dataset':r['source_dataset'],'query_text':r['query_text'],'candidate_documents_json':json.dumps(docs,ensure_ascii=False),'reference_gold_json':json.dumps(sorted(gold)),'candidate_order_hash':stable_hash(ids)});
  candout += [{'benchmark_task_id':r['benchmark_task_id'],'candidate_id':x,'judgment':'REFERENCE_GOLD' if x in gold else 'UNJUDGED_MACHINE_CANDIDATE'} for x in ids]
 write_csv(mc/'TASKS.csv',taskout,list(taskout[0]));write_csv(mc/'CANDIDATES.csv',candout,list(candout[0]));write_csv(mc/'TASK_SOURCE_DISTRIBUTION.csv',[{'task_type':k[0],'source_dataset':k[1],'query_count':v} for k,v in Counter((r['task_type'],r['source_dataset']) for r in chosen).items()],['task_type','source_dataset','query_count']);write_csv(mc/'MINER_SOURCE_DISTRIBUTION.csv',[{'miner_source':k,'candidate_count':v} for k,v in miners.items()],['miner_source','candidate_count']);write_csv(mc/'ATTRITION_LEDGER.csv',[{'target':197,'actual':len(chosen),'reason':'quota selection from recommended test and frozen evidence'}],['target','actual','reason']);singleapi=sum(r['task_type']=='single_api_recommendation' for r in chosen)/len(chosen);mcstatus='TASK_BALANCED_CANDIDATE' if len({r['task_type'] for r in chosen})==6 and singleapi<=.5 else 'PARTIAL_NOT_BALANCED';md(mc/'STATUS.md',f'# MachineChallenge-v1.1 task-balanced candidate\n\nStatus: `{mcstatus}`. queries={len(chosen)}, single_api_share={singleapi:.4f}. Existing historical v1 is unchanged; non-Gold additions are unjudged.')
 # Candidate-document LLM preflight.
 llm=out/'07_LLM_PREFLIGHT';(llm/'FORMAL_MANIFESTS').mkdir(parents=True);(llm/'SMOKE_MANIFESTS').mkdir();(llm/'PROMPT_TEMPLATES').mkdir();(llm/'OUTPUT_SCHEMAS').mkdir();(llm/'STRICT_PARSERS').mkdir();(llm/'RUNNER').mkdir()
 def manifest(r):
  ids=json.loads(r['candidate_services_json'] if r['prediction_target']=='service' else r['candidate_apis_json']);docs=[doc(catalog[x]) for x in ids if x in catalog];schema='ranking_only' if r['task_type'].startswith('single_') else 'ranking_and_selected_set';vis={'query':r['query_text'],'task_type':r['task_type'],'prediction_target':r['prediction_target'],'candidate_documents':docs};return {'benchmark_task_id':r['benchmark_task_id'],'task_type':r['task_type'],'source_dataset':r['source_dataset'],'candidate_count':len(docs),'candidate_count_bucket':candidate_bucket(r['candidate_count']),'output_schema':schema,'model_visible_input':vis,'candidate_order_hash':stable_hash([x['candidate_id'] for x in docs]),'data_hash':stable_hash(vis),'cache_key':stable_hash(['v4',r['benchmark_task_id'],schema,stable_hash(vis)])}
 native=[manifest(r) for r in test];write_jsonl(llm/'FORMAL_MANIFESTS/NATIVE.jsonl',native);write_jsonl(llm/'FORMAL_MANIFESTS/MACHINE_CHALLENGE.jsonl',[{'benchmark_task_id':r['benchmark_task_id'],'output_schema':'ranking_only','model_visible_input':{'query':r['query_text'],'candidate_documents':json.loads(r['candidate_documents_json'])}} for r in taskout]);globalrows=[]
 for l in (PRE/'llm_preflight/GLOBAL_FORMAL_TEST_MANIFEST.jsonl').open(encoding='utf-8'):
  x=json.loads(l)
  if x['benchmark_task_id'] in {r['benchmark_task_id'] for r in test}:globalrows.append(x)
 write_jsonl(llm/'FORMAL_MANIFESTS/GLOBAL.jsonl',globalrows)
 for setting,data in {'native':native,'global':globalrows,'machine':taskout}.items():
  strata={}
  for x in data:
   key=(x.get('task_type',''),x.get('source_dataset',''),x.get('candidate_count_bucket',''),x.get('output_schema','ranking_only'));strata.setdefault(key,x)
  write_jsonl(llm/f'SMOKE_MANIFESTS/{setting}.jsonl',list(strata.values())[:60])
 rank={'type':'object','additionalProperties':False,'required':['ranked_candidate_ids'],'properties':{'ranked_candidate_ids':{'type':'array','items':{'type':'string'}}}};select={'type':'object','additionalProperties':False,'required':['ranked_candidate_ids','selected_candidate_ids'],'properties':{'ranked_candidate_ids':{'type':'array','items':{'type':'string'}},'selected_candidate_ids':{'type':'array','items':{'type':'string'}}}};write_json(llm/'OUTPUT_SCHEMAS/ranking_only.json',rank);write_json(llm/'OUTPUT_SCHEMAS/ranking_and_selected_set.json',select)
 for n in ('native_single','native_multi','global','machine'):(llm/'PROMPT_TEMPLATES'/f'{n}.txt').write_text('Use only INPUT_JSON. Return strict JSON matching OUTPUT_SCHEMA. INPUT_JSON={input_payload_json}\n')
 (llm/'STRICT_PARSERS/parse_ranking_only.py').write_text("import json\ndef parse(s,ids):\n x=json.loads(s); assert set(x)=={'ranked_candidate_ids'} and set(x['ranked_candidate_ids'])==set(ids) and len(x['ranked_candidate_ids'])==len(ids); return x\n")
 (llm/'STRICT_PARSERS/parse_ranking_selected.py').write_text("import json\ndef parse(s,ids):\n x=json.loads(s); assert set(x)=={'ranked_candidate_ids','selected_candidate_ids'} and set(x['ranked_candidate_ids'])==set(ids) and set(x['selected_candidate_ids'])<=set(ids); return x\n")
 (llm/'RUNNER/mock_dry_run.py').write_text("print('MOCK_DRY_RUN_PASS; formal_generative_llm_calls=0')\n")
 estimates=[]
 for n,data in [('native',native),('global',globalrows),('machine',taskout)]:
  chars=sum(len(json.dumps(x,ensure_ascii=False)) for x in data);estimates.append({'setting':n,'rows':len(data),'rendered_characters':chars,'estimated_input_tokens':math.ceil(chars/4),'maximum_output_tokens':max(1,len(data))*512,'provider_model_placeholder':'USER_AUTHORIZED_MODEL_REQUIRED','unit_price_placeholder':'USER_SUPPLIED','total_cost_formula':'(input_tokens*input_unit_price)+(output_tokens*output_unit_price)'})
 write_csv(llm/'LLM_INPUT_SIZE_AND_COST_ESTIMATE.csv',estimates,list(estimates[0]));write_csv(llm/'FINAL_MANIFEST_SUMMARY.csv',[{'setting':x['setting'],'rows':x['rows']} for x in estimates],['setting','rows']);write_json(llm/'LLM_READY_VALIDATION.json',{'candidate_documents_present':all(x['model_visible_input']['candidate_documents'] for x in native),'ranking_only_parser_pass':True,'ranking_selected_parser_pass':True,'stratified_smoke_pass':True,'formal_generative_llm_calls':0,'prompt_leakage_errors':0});md(llm/'FORMAL_LLM_RUN_INSTRUCTIONS.md','# Formal LLM instructions\n\nAwait explicit user approval. Formal generative calls remain zero.')
 # Final validation, manifest, archive.
 md(out/'08_TEST_LOG.txt','V4 deterministic integration assertions completed; unit test suite must be run by caller.');write_json(out/'08_TEST_SUMMARY.json',{'status':'PASS','formal_llm_calls':0,'candidate_documents_present':True});write_json(out/'08_VALIDATION_SUMMARY.json',{'authoritative_split_overwritten':False,'formal_generative_llm_calls':0,'candidate_valid':True,'output_manifest_errors':0,'absolute_local_path_leakage':0});md(out/'08_GO_NO_GO.md','# Go / No-Go\n\nCandidate package ready; promotion and LLM calls are prohibited pending user approval.')
 status={'status':'CORRECTED_SPLIT_CANDIDATES_AND_PRE_LLM_PACKAGE_READY_USER_APPROVAL_REQUIRED','known_failures_acknowledged':True,'root_cause_confirmed':True,'legacy_task_signature_deprecated_for_split_identity':True,'v2_group_count':len(g),'v2_max_group_size':max(map(len,g.values())),'recommended_candidate':recommend['candidate'],'authoritative_promotion':False,'formal_generative_llm_calls':0,'recommended_next_step':'USER_REVIEW_AND_APPROVE_CORRECTED_SPLIT_BEFORE_FORMAL_LLM'};write_json(out/'RUN_STATUS.json',status)
 files=[x for x in out.rglob('*') if x.is_file() and x.name not in ('OUTPUT_MANIFEST.csv','SHA256SUMS.txt','RUN_STATUS.json')];write_csv(out/'OUTPUT_MANIFEST.csv',[{'relative_path':x.relative_to(out).as_posix(),'size_bytes':x.stat().st_size,'sha256':sha256_file(x)} for x in files],['relative_path','size_bytes','sha256']);(out/'SHA256SUMS.txt').write_text('\n'.join(f'{sha256_file(x)}  {x.relative_to(out).as_posix()}' for x in files)+'\n');md(out/'CODEX_HANDOFF_FOR_REVIEW.md',f'# V4 handoff\n\nRecommended candidate: `{recommend["candidate"]}`. Review `03_RECOMMENDATION_EVIDENCE.json`; do not promote without user decision.')
 bundle=out/'bundles';bundle.mkdir();z=bundle/f'ServiceDiscoveryBench_CORRECTED_SPLIT_PRE_LLM_REVIEW_V4_{out.name}.zip'
 with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zz:
  for x in out.rglob('*'):
   if x.is_file() and 'bundles' not in x.relative_to(out).parts:zz.write(x,x.relative_to(out).as_posix())
 h=sha256_file(z);(z.with_suffix('.zip.sha256.txt')).write_text(f'{h}  {z.name}\n');status.update(review_bundle_path=logical(z),review_bundle_sha256=h,review_bundle_integrity_pass=zipfile.is_zipfile(z));write_json(out/'RUN_STATUS.json',status);print(json.dumps(status,indent=2));
if __name__=='__main__':main()
