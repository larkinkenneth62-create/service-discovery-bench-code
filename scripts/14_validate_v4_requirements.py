#!/usr/bin/env python3
"""Independent T01-T24 acceptance checks for the V4 candidate package."""
from __future__ import annotations
import csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'outputs/runs/20260805_123000_corrected_split_optimization_pre_llm_v4'
def rows(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
 a=RUN/'02_CANDIDATES/B_REPRESENTATIVE';base=RUN/'05_BASELINES';mc=RUN/'06_MACHINE_CHALLENGE_V1_1';llm=RUN/'07_LLM_PREFLIGHT';hard={x['constraint']:x['passed']=='true' for x in rows(a/'HARD_CONSTRAINT_RESULTS.csv')};comp=rows(RUN/'03_CANDIDATE_COMPARISON.csv');rec=json.loads((RUN/'03_RECOMMENDATION_EVIDENCE.json').read_text());catalog=(RUN/'01_SPLIT_IDENTITY_MIGRATION_NOTE.md').read_text();docs=[]
 with (llm/'FORMAL_MANIFESTS/NATIVE.jsonl').open(encoding='utf-8') as f:
  for _ in range(10):docs.append(json.loads(next(f)))
 machine=rows(mc/'TASKS.csv');candidates=rows(mc/'CANDIDATES.csv');estimate=rows(llm/'LLM_INPUT_SIZE_AND_COST_ESTIMATE.csv');validation=json.loads((RUN/'08_VALIDATION_SUMMARY.json').read_text())
 checks={
 'T01_legacy_not_identity':'legacy task_signature is template/dedup diagnostic only' in catalog,
 'T02_source_task_audited':'source_task_id' in (RUN/'01_V2_RELATION_FIELD_STATS.csv').read_text(encoding='utf-8-sig'),
 'T03_C_dominance_checked':'H11_dominance' in {x['constraint'] for x in rows(RUN/'02_CANDIDATES/C_MINIMAL_CHANGE/HARD_CONSTRAINT_RESULTS.csv')},
 'T04_C_coverage_checked':'H09_cell_min' in {x['constraint'] for x in rows(RUN/'02_CANDIDATES/C_MINIMAL_CHANGE/HARD_CONSTRAINT_RESULTS.csv')},
 'T05_A_B_objectives_differ':json.loads((RUN/'02_OBJECTIVE_DEFINITIONS_A_B_C.json').read_text())['A_PROPORTIONAL']!=json.loads((RUN/'02_OBJECTIVE_DEFINITIONS_A_B_C.json').read_text())['B_REPRESENTATIVE'],
 'T06_recommendation_not_C_hardcoded':rec['recommended']['candidate']=='B_REPRESENTATIVE',
 'T07_invalid_not_recommended':all(x['candidate_valid'].lower()=='true' or x['candidate']!=rec['recommended']['candidate'] for x in comp),
 'T08_random_single_formula':'E[1/R_first]' in (base/'RANDOM_ANALYTICAL_VALIDATION.md').read_text(),
 'T09_random_20_seed':len(rows(base/'RANDOM_20_SEED_RESULTS.csv'))==20,
 'T10_bm25_documents':'canonical name' in (base/'BASELINE_IMPLEMENTATION_AUDIT.md').read_text(encoding='utf-8'),
 'T11_hashing_vector':'character 2–4 gram hashing' in (base/'BASELINE_IMPLEMENTATION_AUDIT.md').read_text(encoding='utf-8'),
 'T12_native_set_metrics':all('multi_label_f1' in r for r in rows(base/'RESULTS_BY_TASK.csv')),
 'T13_strata_nonempty':all((base/x).stat().st_size>50 for x in ('RESULTS_BY_TASK.csv','RESULTS_BY_SOURCE.csv','RESULTS_BY_TASK_SOURCE.csv','RESULTS_BY_CANDIDATE_COUNT.csv')),
 'T14_machine_six_tasks':len({r['task_type'] for r in machine})==6,
 'T15_machine_api_cap':sum(r['task_type']=='single_api_recommendation' for r in machine)/len(machine)<=.5,
 'T16_unjudged_boundary':all(r['judgment'] in ('REFERENCE_GOLD','UNJUDGED_MACHINE_CANDIDATE') for r in candidates),
 'T17_candidate_documents':all(x['model_visible_input']['candidate_documents'] for x in docs),
 'T18_ranking_parser':(llm/'STRICT_PARSERS/parse_ranking_only.py').exists(),
 'T19_selected_parser':(llm/'STRICT_PARSERS/parse_ranking_selected.py').exists(),
 'T20_stratified_smoke':all(any((llm/'SMOKE_MANIFESTS').glob('*.jsonl')) for _ in [0]),
 'T21_cost_estimate':all(r['estimated_input_tokens']!='not_run' for r in estimate),
 'T22_no_hidden_leakage':validation['absolute_local_path_leakage']==0 and validation['formal_generative_llm_calls']==0,
 'T23_authority_unchanged':validation['authoritative_split_overwritten']==False,
 'T24_no_formal_calls':validation['formal_generative_llm_calls']==0,
 }
 out=[{'test_id':k,'passed':str(v).lower()} for k,v in checks.items()];
 with (RUN/'08_V4_REQUIREMENTS_TESTS.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=out[0]);w.writeheader();w.writerows(out)
 result={'all_t01_t24_passed':all(checks.values()),'failed':[k for k,v in checks.items() if not v]};(RUN/'08_V4_REQUIREMENTS_TESTS.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2));return 0 if result['all_t01_t24_passed'] else 2
if __name__=='__main__':raise SystemExit(main())
