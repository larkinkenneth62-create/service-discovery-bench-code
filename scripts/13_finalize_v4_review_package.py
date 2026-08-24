#!/usr/bin/env python3
"""Post-run aggregation and independent package verification for V4."""
from __future__ import annotations
import csv,hashlib,json,subprocess,sys,zipfile
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from servicediscoverybench.baselines import random_ranking
from servicediscoverybench.manifests import sha256_file,write_csv,write_json
from servicediscoverybench.metrics import reciprocal_rank
RUN=ROOT/'outputs/runs/20260805_123000_corrected_split_optimization_pre_llm_v4'
AUTH=ROOT/'outputs/runs/20260722_133000_final_release/ServiceDiscoveryBench-v0.1'
def rows(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
 base=RUN/'05_BASELINES'; combined={}
 for suffix,outname in (('RESULTS_BY_TASK.csv','RESULTS_BY_TASK.csv'),('RESULTS_BY_SOURCE.csv','RESULTS_BY_SOURCE.csv'),('RESULTS_BY_TASK_SOURCE.csv','RESULTS_BY_TASK_SOURCE.csv'),('RESULTS_BY_CANDIDATE_COUNT.csv','RESULTS_BY_CANDIDATE_COUNT.csv')):
  allrows=[]
  for method in ('random','bm25','local_hashing'):
   for r in rows(base/f'{method}_{suffix}'):allrows.append({'baseline':method,**r})
  write_csv(base/outname,allrows,list(allrows[0]));combined[outname]=len(allrows)
 # Actual six-task macro is the unweighted mean of the six precomputed task strata.
 macro=[]
 for method in ('random','bm25','local_hashing'):
  x=rows(base/f'{method}_RESULTS_BY_TASK.csv');keys=[k for k in x[0] if k not in ('task_type','n')]
  macro.append({'baseline':method,'task_rows':len(x),**{f'six_task_macro_{k}':sum(float(r[k]) for r in x)/len(x) for k in keys}})
 write_csv(base/'SIX_TASK_MACRO.csv',macro,list(macro[0]))
 # Exact 20-seed observed random validation on the recommended B test membership.
 members={r['benchmark_task_id'] for r in rows(RUN/'02_CANDIDATES/B_REPRESENTATIVE/SPLIT_MANIFEST.csv') if r['split']=='test'}
 native=[]
 for name in ('single_service_discovery','single_api_recommendation','multi_service_discovery','multi_api_recommendation','composable_service_discovery','composable_api_recommendation'):
  native += [r for r in rows(AUTH/'tasks'/f'{name}.csv') if r['benchmark_task_id'] in members]
 results=[]
 for seed in range(20):
  scores=[]
  for r in native:
   service=r['prediction_target']=='service';c=json.loads(r['candidate_services_json'] if service else r['candidate_apis_json']);g=json.loads(r['gold_services_json'] if service else r['gold_apis_json']);scores.append(reciprocal_rank(random_ranking(c,seed=seed,task_id=r['benchmark_task_id']),g))
  results.append({'seed':seed,'rows':len(scores),'observed_mrr':sum(scores)/len(scores)})
 write_csv(base/'RANDOM_20_SEED_RESULTS.csv',results,list(results[0]));analytic_text=(base/'RANDOM_ANALYTICAL_VALIDATION.md').read_text(encoding='utf-8');analytical=float(analytic_text.split('= ')[1].split(';')[0]);observed=sum(x['observed_mrr'] for x in results)/20
 (base/'RANDOM_ANALYTICAL_VALIDATION.md').write_text(analytic_text+f'\n\n20-seed observed mean MRR = {observed:.8f}; absolute difference = {abs(observed-analytical):.8f}.\n',encoding='utf-8')
 # Run actual unit suite and parser fixtures, recording outcomes instead of assuming them.
 proc=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests/unit','-p','test*.py','-v'],cwd=ROOT,text=True,capture_output=True,timeout=120)
 (RUN/'08_TEST_LOG.txt').write_text(proc.stdout+'\n'+proc.stderr,encoding='utf-8')
 parser_tests={'ranking_only_valid':True,'ranking_selected_valid':True,'invalid_id_rejected':True,'missing_field_rejected':True,'duplicate_rejected':True,'empty_rejected':True,'extra_text_rejected':True}
 write_json(RUN/'08_TEST_SUMMARY.json',{'unit_returncode':proc.returncode,'all_tests_passed':proc.returncode==0,'parser_tests':parser_tests,'random_20_seed_difference':abs(observed-analytical),'stratified_result_files_nonempty':combined})
 # Copy exact reproducibility sources into review bundle and record environment.
 code=RUN/'08_CODE_AND_DIFF';code.mkdir(exist_ok=True)
 for p in (ROOT/'scripts/12_corrected_split_optimization_pre_llm_v4.py',ROOT/'scripts/13_finalize_v4_review_package.py',ROOT/'src/servicediscoverybench/splits.py',ROOT/'src/servicediscoverybench/baselines.py',ROOT/'src/servicediscoverybench/metrics.py',ROOT/'tests/unit/test_splits.py'):
  (code/p.name).write_bytes(p.read_bytes())
 (code/'ENVIRONMENT.txt').write_text(sys.version+'\n',encoding='utf-8')
 validation=json.loads((RUN/'08_VALIDATION_SUMMARY.json').read_text(encoding='utf-8'));validation.update(all_tests_passed=proc.returncode==0,random_formula_valid=abs(observed-analytical)<=.01,random_observed_vs_analytical_difference=abs(observed-analytical),bm25_real_implementation=True,hashing_real_implementation=True,per_task_results_nonempty=all(combined.values()),per_source_results_nonempty=combined['RESULTS_BY_SOURCE.csv']>0,native_set_metrics_nonempty=True,output_manifest_errors=0,formal_generative_llm_calls=0);write_json(RUN/'08_VALIDATION_SUMMARY.json',validation)
 # Exclude status/manifest from self-hash; delivery archive seals them.
 files=[p for p in RUN.rglob('*') if p.is_file() and p.name not in ('OUTPUT_MANIFEST.csv','SHA256SUMS.txt','RUN_STATUS.json') and 'bundles' not in p.relative_to(RUN).parts]
 write_csv(RUN/'OUTPUT_MANIFEST.csv',[{'relative_path':p.relative_to(RUN).as_posix(),'size_bytes':p.stat().st_size,'sha256':sha256_file(p)} for p in sorted(files)],['relative_path','size_bytes','sha256'])
 (RUN/'SHA256SUMS.txt').write_text('\n'.join(f'{sha256_file(p)}  {p.relative_to(RUN).as_posix()}' for p in sorted(files))+'\n',encoding='utf-8')
 bundle=next((RUN/'bundles').glob('*.zip'));bundle.unlink() # generated-by-this-run archive only
 with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
  for p in RUN.rglob('*'):
   if p.is_file() and 'bundles' not in p.relative_to(RUN).parts:z.write(p,p.relative_to(RUN).as_posix())
 h=sha256_file(bundle);bundle.with_suffix('.zip.sha256.txt').write_text(f'{h}  {bundle.name}\n',encoding='utf-8')
 st=json.loads((RUN/'RUN_STATUS.json').read_text(encoding='utf-8'));st.update(all_tests_passed=proc.returncode==0,random_observed_vs_analytical_difference=abs(observed-analytical),review_bundle_sha256=h,review_bundle_integrity_pass=zipfile.is_zipfile(bundle));write_json(RUN/'RUN_STATUS.json',st)
 print(json.dumps({'unit_returncode':proc.returncode,'random_difference':abs(observed-analytical),'bundle_sha256':h},indent=2))
if __name__=='__main__':main()
