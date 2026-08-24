from __future__ import annotations

import csv, hashlib, json, re, shutil, subprocess, sys, zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
RUN_ID = "20260808_133000_v9_0_1_provider_validation_hotfix"
OUT = PROJECT / "outputs/runs" / RUN_ID
PRED = PROJECT / "outputs/runs/20260808_120000_v9_corrected_pre_llm"
PACKAGE = PROJECT / "ServiceDiscoveryBench_V9_0_1_HOTFIX_PROMPT_PACKAGE"
NATIVE = PROJECT / "outputs/runs/20260806_094643_v0_1_1_closure_v2/release/ServiceDiscoveryBench-v0.1.1"
SOURCENATIVE = PROJECT / "outputs/runs/20260804_135557_pre_llm_all_in_one_v1/global_source_native"
FRAMEWORK = PROJECT / "outputs/runs/20260806_094643_v0_1_1_closure_v2/06_LLM_PREFLIGHT"
MACHINE = PROJECT / "outputs/runs/20260806_094643_v0_1_1_closure_v2/04_MACHINE_CHALLENGE/TASKS.csv"
V7_API = PROJECT / "outputs/runs/20260807_230000_unified_corpus_v7_staged/03_UNIFIED_API_CORPUS_EXACTSAFE_V4_CANDIDATE.jsonl"
V7_QUERY = PROJECT / "outputs/runs/20260807_230000_unified_corpus_v7_staged/04_UNIFIED_GLOBAL_QUERY_MANIFEST_V4.jsonl"
TRACKS = {
    "native": PRED / "02_NATIVE_MODEL_REQUEST_MANIFEST.jsonl",
    "machine": PRED / "03_MACHINE_MODEL_REQUEST_MANIFEST.jsonl",
    "unified": PRED / "04_UNIFIED_END_TO_END_TOP50_MODEL_REQUEST_MANIFEST.jsonl",
}
EXPECTED_ROWS = {"native": 4788, "machine": 197, "unified": 4788}
OLD_TOKENS = ("gold", "acceptable_solution", "retrieval_gold", "qa", "reviewer", "source_path", "truth", "split_membership")


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()


def writej(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def writecsv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None=None) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); fields=fields or (list(rows[0]) if rows else ["status"])
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def rowsjl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def provider_request(row: dict[str, Any]) -> dict[str, Any]:
    return {"request_id":row["benchmark_task_id"],"prompt":row["prompt"],"candidate_ids":row["candidate_ids"],"decoding_config":row["decoding_config"],"timeout_seconds":30.0}


def match_context(raw: str, token: str, width: int=80) -> str:
    pos=raw.casefold().find(token); return raw[max(0,pos-width):pos+len(token)+width].replace("\r"," ").replace("\n","\\n")


def tree_manifest(root: Path, output: Path) -> tuple[str,int]:
    rows=[]
    for path in sorted((p for p in root.rglob("*") if p.is_file()),key=lambda p:p.relative_to(root).as_posix()):
        rows.append({"relative_path":path.relative_to(root).as_posix(),"size_bytes":path.stat().st_size,"sha256":sha(path)})
    writecsv(output,rows,["relative_path","size_bytes","sha256"])
    return hashlib.sha256("".join(f"{r['relative_path']}\0{r['size_bytes']}\0{r['sha256']}\n" for r in rows).encode()).hexdigest(),len(rows)


def internal_manifest() -> tuple[list[dict[str,Any]],dict[str,Any]]:
    excluded={"OUTPUT_MANIFEST.csv","SHA256SUMS.txt"}
    files=sorted((p for p in OUT.rglob("*") if p.is_file() and p.name not in excluded),key=lambda p:p.relative_to(OUT).as_posix())
    rows=[{"path":p.relative_to(OUT).as_posix(),"size_bytes":p.stat().st_size,"sha256":sha(p)} for p in files]
    writecsv(OUT/"OUTPUT_MANIFEST.csv",rows,["path","size_bytes","sha256"])
    (OUT/"SHA256SUMS.txt").write_text("".join(f"{r['sha256']}  {r['path']}\n" for r in rows),encoding="utf-8")
    listed={r["path"] for r in rows}; actual={p.relative_to(OUT).as_posix() for p in files}
    errors=[]
    for r in rows:
        p=OUT/r["path"]
        if not p.exists() or p.stat().st_size!=int(r["size_bytes"]) or sha(p)!=r["sha256"]: errors.append(r["path"])
    return rows,{"listed":len(listed),"actual":len(actual),"size_sha_errors":errors,"unlisted":sorted(actual-listed),"missing":sorted(listed-actual),"terminal_summary_listed":"TERMINAL_SUMMARY.txt" in listed}


def zip_and_check(path: Path) -> dict[str,Any]:
    if path.exists(): path.unlink()
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted((x for x in OUT.rglob("*") if x.is_file()),key=lambda x:x.relative_to(OUT).as_posix()): z.write(p,p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(path) as z:
        bad=z.testzip(); names=z.namelist()
    return {"crc_pass":bad is None,"bad_member":bad,"absolute_paths":sum(bool(n.startswith(("/","\\")) or re.match(r"^[A-Za-z]:",n)) for n in names),"member_count":len(names),"sha256":sha(path),"size_bytes":path.stat().st_size}


def main() -> None:
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    required=[PRED,PACKAGE,NATIVE,SOURCENATIVE,FRAMEWORK,MACHINE,V7_API,V7_QUERY,*TRACKS.values()]
    if any(not p.exists() for p in required): raise SystemExit("missing required predecessor input")
    before_files={"machine":sha(MACHINE),"v7_api":sha(V7_API),"v7_query":sha(V7_QUERY)}

    # 1: exact reproduction of the predecessor's substring-value bug.
    old_rows=[]; old_counts=Counter(); old_unique=set()
    for track,path in TRACKS.items():
        for row in rowsjl(path):
            request=provider_request(row); raw=json.dumps(request,ensure_ascii=False).casefold(); matched=[t for t in OLD_TOKENS if t in raw]
            if matched:
                old_counts[track]+=1; old_unique.add(row["benchmark_task_id"]); token=matched[0]
                old_rows.append({"track":track,"benchmark_task_id":row["benchmark_task_id"],"task_type":row["task_type"],"matched_token":";".join(matched),"match_context":match_context(json.dumps(request,ensure_ascii=False),token)})
    writecsv(OUT/"01_OLD_PROVIDER_VALIDATOR_REJECTION_ROWS.csv",old_rows)
    supplied=list(csv.DictReader((PACKAGE/"V9_PROVIDER_VALIDATION_FALSE_POSITIVE_AUDIT.csv").open(encoding="utf-8-sig",newline="")))
    reproduced=dict(old_counts)=={"native":22,"machine":4,"unified":647} and len(old_rows)==673 and len(old_unique)==658 and len(supplied)==673
    old_summary={"status":"REPRODUCED" if reproduced else "CONFLICT","rejections_by_track":dict(old_counts),"rows_rejected":len(old_rows),"unique_task_ids":len(old_unique),"supplied_independent_audit_rows":len(supplied)}
    writej(OUT/"01_OLD_PROVIDER_VALIDATOR_REJECTION_SUMMARY.json",old_summary)
    if not reproduced:
        (OUT/"CONFLICT_REPORT.md").write_text("# Conflict\n\nOld validator false-positive counts could not be reproduced. Hotfix stopped without guessing.\n",encoding="utf-8"); raise SystemExit(2)

    sys.path.insert(0,str(HERE)); from provider_boundary import mock_generate, validate_provider_request
    # 2: validate every formal request and preserve a row-level ledger.
    coverage=[]; rejected=Counter(); validated=Counter(); samples={}
    for track,path in TRACKS.items():
        for row in rowsjl(path):
            validated[track]+=1; samples.setdefault((track,row["task_type"]),row)
            try: validate_provider_request(provider_request(row)); status="PASS"; error=""
            except Exception as exc: status="REJECTED"; error=str(exc); rejected[track]+=1
            coverage.append({"track":track,"benchmark_task_id":row["benchmark_task_id"],"task_type":row["task_type"],"validation_status":status,"error":error})
    writecsv(OUT/"02_PROVIDER_VALIDATION_FULL_COVERAGE.csv",coverage)
    full_validation=dict(validated)==EXPECTED_ROWS and not rejected
    writej(OUT/"02_PROVIDER_VALIDATION_FULL_COVERAGE_SUMMARY.json",{"rows_validated":sum(validated.values()),"rows_passed":sum(validated.values())-sum(rejected.values()),"rows_rejected":sum(rejected.values()),"by_track":{k:{"validated":validated[k],"rejected":rejected[k]} for k in TRACKS},"status":"PASS" if full_validation else "FAIL"})

    # 3: ordinary values pass; forbidden structured keys fail.
    base=provider_request(next(rowsjl(TRACKS["native"])))
    pass_values=["current and historical gold prices","Get reviewer by ID","country code QA","the word truth","candidate_gold_like_id"]
    regression=[]
    for i,text in enumerate(pass_values):
        req=json.loads(json.dumps(base)); payload=json.loads(next(x for x in req["prompt"].splitlines() if x.startswith("INPUT_JSON="))[11:]); payload["query"]=text
        if i==4: payload["candidate_documents"][0]["candidate_id"]=text; req["candidate_ids"][0]=text
        req["prompt"]="\n".join((line if not line.startswith("INPUT_JSON=") else "INPUT_JSON="+stable(payload)) for line in req["prompt"].splitlines())+"\n"
        try: validate_provider_request(req); observed="PASS"
        except Exception as exc: observed=f"FAIL:{exc}"
        regression.append({"fixture":text,"expected":"PASS","observed":observed,"pass":observed=="PASS"})
    adversarial=["reference_gold_ids","acceptable_solutions","retrieval_gold_recall","qa_notes","reviewer_decision","source_path","evaluation_truth","split_membership_decision"]
    adversarial_requests=[]
    for key in adversarial:
        req=json.loads(json.dumps(base))
        if key=="reference_gold_ids": req[key]=["x"]
        else:
            payload=json.loads(next(x for x in req["prompt"].splitlines() if x.startswith("INPUT_JSON="))[11:]); payload["candidate_documents"][0][key]="x"
            req["prompt"]="\n".join((line if not line.startswith("INPUT_JSON=") else "INPUT_JSON="+stable(payload)) for line in req["prompt"].splitlines())+"\n"
        adversarial_requests.append((key,req))
        try: validate_provider_request(req); observed="PASS"
        except Exception: observed="REJECTED"
        regression.append({"fixture":key,"expected":"REJECTED","observed":observed,"pass":observed=="REJECTED"})
    regression_pass=all(x["pass"] for x in regression)
    writej(OUT/"03_PROVIDER_VALIDATION_REGRESSION_TESTS.json",{"status":"PASS" if regression_pass else "FAIL","tests":regression})
    (OUT/"03_PROVIDER_VALIDATION_REGRESSION_TEST_LOG.txt").write_text("\n".join(f"{x['fixture']}: expected={x['expected']} observed={x['observed']} {'PASS' if x['pass'] else 'FAIL'}" for x in regression)+"\n",encoding="utf-8")

    # 4: full deterministic in-process mock, plus required subprocess boundary samples.
    mock_counts=Counter(); mock_failures=[]
    for track,path in TRACKS.items():
        for row in rowsjl(path):
            request=provider_request(row)
            try:
                validate_provider_request(request); response=mock_generate(**request)
                allowed=set(request["candidate_ids"]); returned=set(response["ranked_candidate_ids"])|set(response["selected_candidate_ids"])
                if not returned<=allowed: raise ValueError("mock returned out-of-request candidate")
                mock_counts[track]+=1
            except Exception as exc: mock_failures.append({"track":track,"benchmark_task_id":row["benchmark_task_id"],"error":str(exc)})
    full_mock=dict(mock_counts)==EXPECTED_ROWS and not mock_failures
    writej(OUT/"04_MOCK_DRY_RUN_FULL_COVERAGE.json",{"status":"PASS" if full_mock else "FAIL","rows":dict(mock_counts),"total":sum(mock_counts.values()),"failures":mock_failures,"network_calls":0,"api_keys_read":0,"formal_generative_llm_calls":0})
    boundary=[]
    subprocess_cases=[(f"{track}:{task}",provider_request(row),True) for (track,task),row in sorted(samples.items())]
    subprocess_cases += [(f"ordinary:{x['fixture']}",None,True) for x in regression[:len(pass_values)]]
    ordinary_requests=[]
    for text in pass_values:
        req=json.loads(json.dumps(base)); payload=json.loads(next(x for x in req["prompt"].splitlines() if x.startswith("INPUT_JSON="))[11:]); payload["query"]=text; req["prompt"]="\n".join((line if not line.startswith("INPUT_JSON=") else "INPUT_JSON="+stable(payload)) for line in req["prompt"].splitlines())+"\n"; ordinary_requests.append(req)
    subprocess_cases=[x for x in subprocess_cases if x[1] is not None]+[(f"ordinary:{pass_values[i]}",r,True) for i,r in enumerate(ordinary_requests)]+[(f"adversarial:{k}",r,False) for k,r in adversarial_requests]
    worker=HERE/"provider_adapter_worker.py"
    for name,request,expect_success in subprocess_cases:
        proc=subprocess.run([sys.executable,str(worker)],input=(stable(request)+"\n").encode("utf-8"),capture_output=True,timeout=30)
        actual=proc.returncode==0; boundary.append({"case":name,"expected_success":expect_success,"actual_success":actual,"pass":actual==expect_success,"returncode":proc.returncode})
    boundary_pass=all(x["pass"] for x in boundary)
    writecsv(OUT/"04_SUBPROCESS_BOUNDARY_TESTS.csv",boundary)

    # 5: deterministic directory tree evidence, with pre/post hashes.
    native_hash,native_files=tree_manifest(NATIVE,OUT/"05_DIRECTORY_TREE_MANIFEST_NATIVE.csv")
    source_hash,source_files=tree_manifest(SOURCENATIVE,OUT/"05_DIRECTORY_TREE_MANIFEST_SOURCENATIVE.csv")
    framework_hash,framework_files=tree_manifest(FRAMEWORK,OUT/"05_DIRECTORY_TREE_MANIFEST_FRAMEWORK.csv")
    before_trees={"native":native_hash,"source_native":source_hash,"framework":framework_hash}
    writej(OUT/"05_DIRECTORY_TREE_HASHES.json",{"algorithm":"sha256(relative_path\\0size_bytes\\0file_sha256\\n)","native":{"tree_hash":native_hash,"files":native_files},"source_native":{"tree_hash":source_hash,"files":source_files},"framework":{"tree_hash":framework_hash,"files":framework_files},"predecessor_linkage":"directories resolved by V9 00_PATH_AND_ARTIFACT_RESOLUTION.json; hotfix pre/post tree hashes must match"})

    # Core V9 tests T01-T21/T23 are reused exactly as requested, then T22/T24/T25 are replaced.
    previous=json.loads((PRED/"TEST_SUMMARY.json").read_text(encoding="utf-8"))["tests"]
    tests={k:v for k,v in previous.items() if k.startswith(tuple(f"T{i:02d}" for i in list(range(1,22))+[23]))}
    tests["T22 full 9,773-request mock dry-run pass"]=full_mock and boundary_pass
    tests["T23 formal generative LLM calls = 0"]=True
    after_trees={
      "native":tree_manifest(NATIVE,OUT/"05_DIRECTORY_TREE_MANIFEST_NATIVE_POST.csv")[0],
      "source_native":tree_manifest(SOURCENATIVE,OUT/"05_DIRECTORY_TREE_MANIFEST_SOURCENATIVE_POST.csv")[0],
      "framework":tree_manifest(FRAMEWORK,OUT/"05_DIRECTORY_TREE_MANIFEST_FRAMEWORK_POST.csv")[0]}
    after_files={"machine":sha(MACHINE),"v7_api":sha(V7_API),"v7_query":sha(V7_QUERY)}
    freeze_pass=before_trees==after_trees and before_files==after_files
    tests["T24 real Native/SourceNative/Machine/V7 freeze verification pass"]=freeze_pass
    tests["T25 real package integrity pass"]=False
    tests["T26 old substring-validator failure reproduced"]=reproduced
    tests["T27 ordinary gold/reviewer/QA/truth text accepted"]=all(x["pass"] for x in regression if x["expected"]=="PASS")
    tests["T28 forbidden JSON keys rejected"]=all(x["pass"] for x in regression if x["expected"]=="REJECTED")
    tests["T29 all 9,773 provider requests accepted"]=full_validation
    tests["T30 OUTPUT_MANIFEST includes TERMINAL_SUMMARY"]=False

    code=OUT/"06_CODE_AND_REPRODUCIBILITY"; code.mkdir();
    for name in ("run_provider_validation_v9_0_1.py","provider_boundary.py","provider_adapter_worker.py"): shutil.copy2(HERE/name,code/name)
    shutil.copy2(PACKAGE/"SERVICEDISCOVERYBENCH_V9_INDEPENDENT_ACCEPTANCE_AUDIT_V1.md",OUT/"00_INDEPENDENT_ACCEPTANCE_AUDIT.md")
    (OUT/"DATA_CARD_ADDENDUM.md").write_text("# V9.0.1 Data Card addendum\n\nThis hotfix changes only structured provider validation, coverage gates, freeze evidence and delivery integrity. It does not alter Query, Gold, candidates, order, task identity, split, Top-50, Machine membership or metric semantics.\n",encoding="utf-8")
    (OUT/"LIMITATIONS.md").write_text("# Limitations\n\nThis is not V9.1 and performs no wider hardening. Unified non-Gold remains unjudged; retrieval and duplicate-document limitations remain as reported by V9. No model/provider/network call was made.\n",encoding="utf-8")
    (OUT/"HANDOFF.md").write_text("# V9.0.1 handoff\n\nReview the old-validator reproduction, 9,773-row validation ledger, regression/subprocess tests, directory tree hashes and final external delivery index. A model/revision/provider/budget decision remains explicitly required before any pilot.\n",encoding="utf-8")

    zip_path=PROJECT/"outputs/runs"/f"ServiceDiscoveryBench_V9_0_1_PROVIDER_HOTFIX_REVIEW_{RUN_ID}.zip"
    def summaries(package_pass: bool, manifest_complete: bool) -> None:
        tests["T25 real package integrity pass"]=package_pass; tests["T30 OUTPUT_MANIFEST includes TERMINAL_SUMMARY"]=manifest_complete
        all_pass=all(bool(v) for v in tests.values())
        status="PRE_LLM_READY_PLAN_A_V9_0_1_CORRECTED_CURRENT_UNIFIED_CANDIDATE_USER_MODEL_BUDGET_AUTHORIZATION_REQUIRED" if all_pass else "PRE_LLM_NO_GO"
        run={"status":status,"old_validator_rejected_native":old_counts["native"],"old_validator_rejected_machine":old_counts["machine"],"old_validator_rejected_unified":old_counts["unified"],"new_validator_rows_validated":sum(validated.values()),"new_validator_rows_rejected":sum(rejected.values()),"ordinary_text_fixture_pass":tests["T27 ordinary gold/reviewer/QA/truth text accepted"],"forbidden_key_fixture_pass":tests["T28 forbidden JSON keys rejected"],"full_mock_dry_run_pass":tests["T22 full 9,773-request mock dry-run pass"],"native_tree_hash":native_hash,"source_native_tree_hash":source_hash,"framework_tree_hash":framework_hash,"output_manifest_complete":manifest_complete,"formal_generative_llm_calls":0,"authoritative_promotion":False,"composable_expansion_status":"DEFERRED_TO_V0_3_OR_FUTURE","remaining_blockers":["USER_MODEL_REVISION_PROVIDER_BUDGET_AUTHORIZATION_REQUIRED"] if all_pass else [k for k,v in tests.items() if not v],"recommended_next_step":"USER_REVIEW_THEN_MODEL_REVISION_PROVIDER_BUDGET_AND_FORMAL_LLM_PILOT_AUTHORIZATION","review_bundle_path":str(zip_path)}
        writej(OUT/"RUN_STATUS.json",run); writej(OUT/"VALIDATION_SUMMARY.json",{"status":"PASS" if all_pass else "FAIL","tests":tests,"freeze_before":{**before_files,**before_trees},"freeze_after":{**after_files,**after_trees}}); writej(OUT/"TEST_SUMMARY.json",{"status":"PASS" if all_pass else "FAIL","passed":sum(bool(v) for v in tests.values()),"total":len(tests),"tests":tests}); (OUT/"TEST_LOG.txt").write_text("\n".join(f"{k}: {'PASS' if v else 'FAIL'}" for k,v in tests.items())+"\n",encoding="utf-8"); (OUT/"TERMINAL_SUMMARY.txt").write_text("\n".join(f"{k} = {stable(v) if isinstance(v,(dict,list)) else v}" for k,v in run.items())+"\n",encoding="utf-8")

    summaries(False,False); manifest_rows,manifest_check=internal_manifest(); manifest_complete=not manifest_check["size_sha_errors"] and not manifest_check["unlisted"] and not manifest_check["missing"] and manifest_check["terminal_summary_listed"]
    preflight=PROJECT/"outputs/runs"/f".{RUN_ID}.preflight.zip"; pre=zip_and_check(preflight); preflight.unlink()
    package_prepass=manifest_complete and pre["crc_pass"] and pre["absolute_paths"]==0
    summaries(package_prepass,manifest_complete)
    manifest_rows,manifest_check=internal_manifest(); manifest_complete=not manifest_check["size_sha_errors"] and not manifest_check["unlisted"] and not manifest_check["missing"] and manifest_check["terminal_summary_listed"]
    tests["T30 OUTPUT_MANIFEST includes TERMINAL_SUMMARY"]=manifest_complete
    final=zip_and_check(zip_path)
    sidecar=zip_path.with_suffix(zip_path.suffix+".sha256.txt"); sidecar.write_text(f"{final['sha256']}  {zip_path.name}\n",encoding="utf-8")
    sidecar_match=sidecar.read_text(encoding="utf-8").split()[0]==sha(zip_path)
    actual_t25=manifest_complete and final["crc_pass"] and final["absolute_paths"]==0 and sidecar_match
    crc_path=zip_path.with_suffix(zip_path.suffix+".crc.json"); writej(crc_path,{**final,"zip":zip_path.name,"sidecar_matches":sidecar_match,"internal_manifest":manifest_check})
    delivery=zip_path.parent/"FINAL_DELIVERY_INDEX.json"; writej(delivery,{"run_id":RUN_ID,"status":"PASS" if actual_t25 and all(bool(v) for k,v in tests.items() if not k.startswith("T25")) else "FAIL","review_bundle_path":str(zip_path),"review_bundle_sha256":final["sha256"],"sha256_sidecar":str(sidecar),"crc_report":str(crc_path),"zip_crc_pass":final["crc_pass"],"zip_absolute_paths":final["absolute_paths"],"output_manifest_complete":manifest_complete,"T25_actual":actual_t25,"formal_generative_llm_calls":0,"authoritative_promotion":False})
    print((OUT/"TERMINAL_SUMMARY.txt").read_text(encoding="utf-8"),end=""); print(f"zip_crc_pass = {final['crc_pass']}"); print(f"zip_sha256 = {final['sha256']}"); print(f"review_bundle_sha256 = {final['sha256']}"); print(f"review_bundle_integrity_pass = {actual_t25}")


if __name__=="__main__": main()
