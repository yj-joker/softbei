"""依据任务最终执行快照抽取待审核候选，不访问图谱或外部知识源。"""
import json
import re
from typing import Any, Dict
from pydantic import ValidationError
from services.llm.service import LLMService, get_llm_service
from schemas.task_evidence_extraction import *

class ExtractionContractError(ValueError):
    pass

class TaskEvidenceExtractionService:
    def __init__(self, llm: LLMService | None = None): self.llm = llm or get_llm_service()
    async def extract(self, snapshot: Dict[str, Any], request_id="unknown", task_id=1, evidence_version=1):
        try:
            refs = self._evidence_index(snapshot)
            prompt_snapshot = {**snapshot, "steps": [
                {key: value for key, value in step.items() if key != "sources"}
                for step in snapshot.get("steps") or []
            ]}
            prompt_payload = {
                "snapshot": prompt_snapshot,
                "allowedEvidence": [e.model_dump(by_alias=True) for e in refs.values()],
            }
            raw = await self.llm.chat([{"role":"system","content":"仅输出JSON，顶层必须有 devices/components/faults/solutions/relations。候选字段：devices/components/faults 使用 id,name,evidenceRefs；solutions 使用 id,title,evidenceRefs。关系字段必须使用 sourceId,targetId,type,evidenceRefs；禁止使用 from/to。evidenceRefs 只能逐字使用 allowedEvidence 中的 ref；不得输出手册来源、知识库 chunkId 或任何未列出的引用。关系 type 只能 OWNS（Device→Component）、CAUSES（Component→Fault）、HAS_SOLUTION（Fault→Solution），方向错误的关系必须省略。"},{"role":"user","content":json.dumps(prompt_payload, ensure_ascii=False)}], temperature=0, response_format={"type":"json_object"})
            data=json.loads(raw.get("content", ""))
            if "candidates" in data and "devices" not in data:
                data = {**data.get("candidates", {}), "relations": data.get("relations", [])}
            for solution in data.get("solutions") or []:
                if not solution.get("title") and solution.get("name"):
                    solution["title"] = solution["name"]
            for relation in data.get("relations") or []:
                if not relation.get("sourceId") and relation.get("from") is not None:
                    relation["sourceId"] = relation["from"]
                if not relation.get("targetId") and relation.get("to") is not None:
                    relation["targetId"] = relation["to"]
            warnings=[]
            status=str(snapshot.get("resolutionStatus", ""))
            c=self._build(data, refs, warnings, status, snapshot)
            if not any((c.devices,c.components,c.faults,c.solutions,c.relations)):
                warnings.append(Warning(code="NO_ACTIONABLE_CANDIDATE", message="没有可审核的候选。"))
            status=str(snapshot.get("resolutionStatus", ""))
            if status == "PARTIALLY_RESOLVED":
                warnings.append(Warning(code="PARTIALLY_EFFECTIVE", message="部分解决；措施不可标记为已验证。"))
            elif status == "UNRESOLVED":
                warnings.append(Warning(code="ATTEMPTED_NOT_EFFECTIVE", message="未解决；尝试措施不可标记为已验证。"))
            if status != "RESOLVED":
                for x in c.solutions: x.verified=False; x.source_type="attempted"
            return TaskEvidenceExtractionSuccess(model=ModelMetadata(name=str(raw.get("model", "")), requestId=str(raw.get("request_id", ""))), requestId=request_id, taskId=task_id, evidenceVersion=evidence_version, candidates=c, evidence=list(refs.values()), warnings=self._dedupe(warnings))
        except ExtractionContractError:
            raise
        except Exception as exc:
            return TaskEvidenceExtractionFailure(requestId=request_id, taskId=task_id, evidenceVersion=evidence_version, errorCode="EXTRACTION_FAILED", error=str(exc), retryable=False)
    @staticmethod
    def _evidence_index(s):
        out={}
        def add(ref,v,step=None):
            if v not in (None, "", []): out[ref]=Evidence(ref=ref, excerpt=str(v)[:1000], stepId=step)
        for field in ("deviceName","faultDescription","finalFaultCause","effectiveMeasure","completionSummary","resolutionStatus","resolvedAt","startedAt","completedAt"):
            add("task:"+field,s.get(field))
        for i,url in enumerate(s.get("reportImages") or []): add(f"task:reportImages:{i}",url)
        for i,step in enumerate(s.get("steps") or [],1):
            sid=str(step.get("stepId") or i)
            for field in ("title","content","safetyNote","status","note","checkpointItems","checkpointConfirmed","completedAt","aiPass","aiConfidence","aiReason"):
                add(f"step:{sid}:{field}",step.get(field),sid)
            for i,url in enumerate(step.get("images") or []): add(f"step:{sid}:images:{i}",url,sid)
        return out
    def _ev(self, item, refs, warnings):
        out=[]
        for ref in item.get("evidenceRefs") or []:
            if ref not in refs: raise ExtractionContractError(f"invalid evidence ref: {ref}")
            if ref not in {x.ref for x in out}: out.append(refs[ref])
        return out
    @staticmethod
    def _normalize(value):
        return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)

    @classmethod
    def _matches(cls, candidate, excerpt, exact=False):
        a, b = cls._normalize(candidate), cls._normalize(excerpt)
        return bool(a and b and (a == b if exact else a in b))

    @classmethod
    def _content_ok(cls, key, label, evidence, snapshot):
        field = {"devices": "deviceName", "components": "finalFaultCause", "faults": "finalFaultCause", "solutions": "effectiveMeasure"}.get(key)
        relevant = [e for e in evidence if e.ref == "task:" + field] if field else []
        if field and (not snapshot.get(field) or not relevant):
            return False
        if key == "devices" and relevant and not all(cls._matches(label, e.excerpt, exact=True) for e in relevant):
            return False
        if key != "devices" and relevant and not all(cls._matches(label, e.excerpt) for e in relevant):
            return False
        for e in evidence:
            excerpt = str(e.excerpt or "").strip()
            if not excerpt or excerpt.startswith(("http://", "https://")) or ":images:" in e.ref or ":reportImages:" in e.ref:
                continue
            if not cls._matches(label, excerpt):
                return False
        return True

    def _mismatch_warning(self, warnings, key, label, field):
        warnings.append(Warning(code="EVIDENCE_CONTENT_MISMATCH", message=f"{key}候选{label}与证据字段{field}内容不匹配。"))

    def _build(self,d,refs,w,status="", snapshot=None):
        c=Candidates(); ids=set()
        entity_types = {}
        entity_labels = {}
        for key,cls,name in (("devices",Device,"name"),("components",Component,"name"),("faults",Fault,"name"),("solutions",Solution,"title")):
            arr=getattr(c,key)
            for x in d.get(key) or []:
                ident=str(x.get("id") or x.get(name,"")); label=str(x.get(name,"" )).strip()
                if not ident or not label: continue
                if ident in ids: continue
                evidence = self._ev(x,refs,w)
                if not evidence:
                    if key == "devices" and any(e.ref == "task:deviceName" and e.excerpt == label for e in refs.values()):
                        evidence = [refs["task:deviceName"]]
                    else:
                        w.append(Warning(code="MISSING_CANDIDATE_EVIDENCE", message=f"过滤无证据候选: {label}"))
                        continue
                kwargs = {name: label, "evidence": evidence}
                content_ok = self._content_ok(key, label, evidence, snapshot)
                if not content_ok:
                    self._mismatch_warning(w, key, label, {"devices":"deviceName","components":"finalFaultCause","faults":"finalFaultCause","solutions":"effectiveMeasure"}[key])
                if key == "devices":
                    kwargs["confirmed"] = content_ok and any(e.ref == "task:deviceName" for e in evidence)
                if key in ("components", "faults"):
                    kwargs["confirmed"] = content_ok and status == "RESOLVED" and any(e.ref == "task:finalFaultCause" for e in evidence)
                if key == "solutions":
                    kwargs["verified"] = content_ok and status == "RESOLVED" and any(e.ref == "task:effectiveMeasure" for e in evidence)
                    kwargs["source_type"] = "confirmed" if kwargs["verified"] else ("attempted" if status != "RESOLVED" else "candidate")
                ids.add(ident)
                entity_types[ident] = key
                entity_labels[ident] = label
                arr.append(cls(id=ident, **kwargs))
        expected = {"OWNS": ("devices", "components"), "CAUSES": ("components", "faults"), "HAS_SOLUTION": ("faults", "solutions")}
        for x in d.get("relations") or []:
            if x.get("type") not in expected: raise ExtractionContractError("invalid relation type")
            source_id, target_id = str(x.get("sourceId")), str(x.get("targetId"))
            if source_id not in ids or target_id not in ids:
                w.append(Warning(code="MISSING_RELATION_ENDPOINT", message="关系端点不存在或被无证据候选过滤。"))
                continue
            if (entity_types[source_id], entity_types[target_id]) != expected[x["type"]]:
                w.append(Warning(code="INVALID_RELATION_ENDPOINT_TYPES", message="关系类型与端点实体类型不匹配，已过滤。"))
                continue
            evidence = self._ev(x,refs,w)
            if not evidence:
                w.append(Warning(code="MISSING_RELATION_EVIDENCE", message="关系缺少证据，已过滤。"))
                continue
            source_label, target_label = entity_labels[source_id], entity_labels[target_id]
            if not any(self._matches(source_label, e.excerpt) and self._matches(target_label, e.excerpt) for e in evidence):
                w.append(Warning(code="UNSUPPORTED_RELATION_EVIDENCE", message="关系证据未同时支持两端及其关联语义，已过滤。"))
                continue
            c.relations.append(Relation(sourceId=source_id,targetId=target_id,type=x["type"],evidence=evidence))
        return c
    @staticmethod
    def _dedupe(ws):
        seen=set(); out=[]
        for w in ws:
            if w.code not in seen: seen.add(w.code); out.append(w)
        return out
_service=None
def get_task_evidence_extraction_service():
    global _service
    if _service is None: _service=TaskEvidenceExtractionService()
    return _service
