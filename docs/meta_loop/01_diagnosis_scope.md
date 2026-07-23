# 1. 진단과 변경 범위 설정

## 책임

증거가 단순한 운영 문제인지, L2/L3 조정으로 해결 가능한지, 아니면 실제로 L4/L5
또는 아키텍처 변경이 필요한 구조적 문제인지를 구분한다.

## 의사 코드

```text
function diagnose_and_scope(evidence):
    hypotheses = generate_root_cause_hypotheses(evidence)
    evaluated = []

    for hypothesis in hypotheses:
        evaluated.append({
            hypothesis: hypothesis,
            supporting_evidence: find_support(evidence, hypothesis),
            contradicting_evidence: find_counterevidence(evidence, hypothesis),
            smallest_safe_fix: identify_lowest_layer_fix(hypothesis)
        })

    recommendations = []
    for item in evaluated:
        if item.smallest_safe_fix in ["L1", "L2", "L3", "outer_loop"]:
            recommendations.append(route_to_lower_loop(item))
        else:
            recommendations.append(create_meta_recommendation(item))

    return Diagnosis(evaluated, recommendations)
```

## 규칙

- Meta Loop는 "구조적" 변경만 소유한다. L2 신뢰도 조정이나 L3 상태 전이는
  Outer Loop로 되돌린다.
- 더 낮은 계층의 안전한 수정으로 해결할 수 있다면 L4/L5 변경을 제안하지 않는다.
- 진단은 사실, 추론, 미확정 가설을 구분해 기록한다.
