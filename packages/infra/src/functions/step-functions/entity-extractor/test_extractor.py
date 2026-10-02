"""Smoke test for invoke_extraction.

Requires AWS credentials and Bedrock access.
Set ENTITY_EXTRACTION_MODEL_ID env var before running.

Usage:
    ENTITY_EXTRACTION_MODEL_ID=global.amazon.nova-2-lite-v1:0 \
    python -m pytest test_extractor.py -v -s
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.dirname(__file__))

from extractor import invoke_extraction


SAMPLE_TEXT = """--- Segment 0, QA 0 ---
시각적 요소
버블 차트 다이어그램 (Scope vs. Time)
이 슬라이드의 핵심 시각 요소는 2D 버블 차트로, X축(Time)과 Y축(Scope)을 기준으로 소프트웨어 개발 단계를 원(버블)의 크기와 위치로 표현합니다.

축 구성:

Y축 (Scope, 세로): 위로 갈수록 범위가 넓어짐
아래: Specific function → Isolated exercise or use case → Whole system end-to-end → Full system for a subset of use cases / users → Full system, all use cases (위)
X축 (Time, 가로): 오른쪽으로 갈수록 시간이 길어짐
Hours → Days → Weeks → Months → Years
각 버블 상세:

버블	위치(Time)	위치(Scope)	크기	색상	특이사항
Demo	Hours	Specific function	가장 소형	연한 하늘색	-
Proof of concept	Days 초반	Isolated exercise or use case	소형	중간 파란색	Demo와 겹침
Prototype	Days 후반~Weeks	Whole system end-to-end	중형	밝은 파란색	주황색 테두리로 강조
Pilot	Weeks~Months	Full system for a subset of use cases/users	대형	진한 파란색	-
Minimum Viable Product (MVP)	Months 경계	Pilot~Production 사이	(별도 레이블)	-	Pilot과 Production 겹침 영역에 표시
Production	Months~Years	Full system, all use cases	최대형	짙은 청록색	-
시각적 특징:

버블들이 왼쪽→오른쪽으로 점점 커지며, 인접 버블들이 서로 겹쳐 단계 간 연속성을 표현
Prototype 버블만 주황색(오렌지) 테두리로 강조 → 슬라이드 주제("What is prototyping?")에서 Prototype 단계를 특별히 부각
색상은 연한 하늘색에서 짙은 청록색으로 점진적으로 진해지며 성숙도 증가를 표현
AWS 로고(왼쪽 하단, 주황색 화살표 포함)
핵심 정보
슬라이드 제목: "What is prototyping?"
출처/저작권: © 2023, Amazon Web Services, Inc. or its affiliates. All rights reserved.
발행사: AWS (Amazon Web Services)
개발 단계 5단계: Demo → Proof of concept → Prototype → Pilot → Production
중간 이정표: Minimum Viable Product (MVP) — Pilot과 Production 사이 경계
Prototype 단계 특성: Days~Weeks 소요, Whole system end-to-end 범위, 주황색 테두리로 강조
Production 단계 특성: Months~Years 소요, Full system all use cases 범위, 가장 큰 버블
분석 노트
시각적 강조의 의미: Prototype 버블에만 주황색 테두리가 적용되어 있으며, 이는 슬라이드 제목("What is prototyping?")과 직결되어 Prototype이 이 교육 자료의 핵심 개념임을 나타냄
MVP 위치: "Minimum Viable Product (MVP)" 레이블은 Pilot과 Production 버블이 겹치는 경계 영역에 배치되어, MVP가 Pilot에서 Production으로 전환되는 시점의 산출물임을 시사함
연속성 표현: 버블 간 겹침(overlap) 구조를 통해 각 단계가 순차적이고 유기적으로 연결됨을 강조
Extra Analysis

1
이 다이어그램에서 각 버블(원)의 크기, 색상, 위치 관계를 설명해주세요. 특히 Prototype 버블에 주황색 테두리가 있는지, 각 버블이 X축(Time)과 Y축(Scope)의 어느 위치에 해당하는지 상세히 설명해주세요.



Direct Answer
다이어그램의 각 버블은 시간이 지남에 따라 크기가 커지며, Prototype 버블에는 명확한 주황색(오렌지색) 테두리가 있습니다. 각 버블은 X축(Time)과 Y축(Scope)에서 서로 다른 위치에 배치되어 있습니다.

Extracted Details
각 버블의 상세 정보
버블 이름	크기	색상	X축 위치(Time)	Y축 위치(Scope)	특이사항
Demo	가장 작음	연한 하늘색	Hours	Specific function	-
Proof of Concept	소형	중간 파란색	Days	Isolated exercise or use case	Demo와 겹침
Prototype	중형	밝은 파란색	Days~Weeks	Whole system end-to-end	주황색 테두리 있음
Pilot	대형	진한 파란색	Weeks~Months	Full system for a subset of use cases/users	-
Production	최대형	가장 진한 청록색	Months~Years	Full system, all use cases	MVP 레이블 포함
Supporting Evidence
위치 관계 상세 설명
Demo (가장 작은 버블)

X축: Hours 구간
Y축: Specific function 수준
색상: 가장 연한 하늘색(투명도 높음)
Proof of Concept

X축: Days 초반
Y축: Isolated exercise or use case 수준
Demo 버블과 오른쪽으로 겹쳐 배치됨
Prototype ⭐ (핵심)

X축: Days 후반 ~ Weeks 초반
Y축: Whole system end-to-end 수준
주황색(오렌지) 테두리가 뚜렷하게 표시됨 → 이 다이어그램에서 현재 강조/하이라이트된 단계임을 시각적으로 표현
Proof of Concept 버블과 왼쪽에서 겹침
Pilot

X축: Weeks ~ Months 구간
Y축: Full system for a subset of use cases/users 수준
Prototype보다 현저히 큰 버블
"Minimum Viable Product (MVP)" 레이블이 Pilot과 Production 경계 부분에 표시됨
Production

X축: Months ~ Years 구간
Y축: Full system, all use cases 수준
가장 크고 가장 진한 청록색(teal)
Pilot 버블과 오른쪽에서 겹침
Additional Findings
버블 간 겹침(Overlap) 패턴
인접한 버블들은 서로 부분적으로 겹쳐 배치되어, 각 단계가 독립적이지 않고 연속적으로 전환됨을 시사
겹침 순서: Demo → Proof of Concept → Prototype → Pilot → Production
색상 그라데이션 의미
연한 하늘색(Demo) → 밝은 파란색(Prototype) → 진한 청록색(Production)으로 색상이 점점 진해짐 → 성숙도/완성도 증가를 시각적으로 표현
주황색 테두리의 의미
Prototype 버블만 주황색 테두리로 강조된 것은, 이 슬라이드가 "Prototyping이란 무엇인가?" 를 설명하는 맥락에서 Prototype 단계를 특별히 부각시키기 위한 의도적 디자인 선택으로 판단됨
Limitations
버블의 정확한 픽셀 크기나 수치적 비율은 측정 불가
MVP 레이블이 Pilot과 Production 중 어느 버블에 정확히 속하는지 경계가 다소 모호하게 표현되어 있음 (두 버블의 겹치는 영역에 위치)"""


SAMPLE_TEXT_2 = """--- Segment 1, QA 0 ---
문서 개요
유형: AWS 서비스 소개 프레젠테이션 슬라이드
목적: AWS Prototyping의 핵심 개념인 "Innovation Flywheel"과 프로토타이핑이 제공하는 4가지 주요 가치를 시각적으로 설명하는 슬라이드

핵심 정보
문서 주제: AWS Prototyping
핵심 개념: Innovation Flywheel (혁신 플라이휠) — Experiment(실험) → Feedback(피드백) → Ideas(아이디어)의 순환
4대 가치:
Accelerate adoption — 혁신 속도 향상
Elevate customer thinking — 고객의 사고·창의성·혁신 수준 향상
Create long term value — 선점 우위 및 장기적 가치 창출
Demystify technology — 신기술의 복잡성 해소
저작권: © 2023, Amazon Web Services, Inc. or its affiliates. All rights reserved.

분석 노트
구조적 특징: 좌측의 순환 다이어그램(Innovation Flywheel)은 AWS 프로토타이핑의 방법론적 기반을 나타내며, 우측 4가지 가치는 그 결과물을 설명하는 구조로 논리적으로 연결되어 있음

--- Segment 1, Page Description ---
AWS Prototyping 소개 슬라이드로, Innovation Flywheel(Experiment-Feedback-Ideas 순환)과 4가지 핵심 가치(Accelerate adoption, Elevate customer thinking, Create long term value, Demystify technology)를 시각적으로 설명합니다."""


@pytest.fixture
def require_model():
    if not os.environ.get('ENTITY_EXTRACTION_MODEL_ID'):
        pytest.skip('ENTITY_EXTRACTION_MODEL_ID not set')


def test_invoke_extraction_page1(require_model):
    """Page 1: What is prototyping? bubble chart."""
    entities = invoke_extraction(SAMPLE_TEXT, language='ko')

    assert isinstance(entities, list)
    assert len(entities) > 0

    for ent in entities:
        assert 'name' in ent
        assert 'mentioned_in' in ent

    print('\n--- Page 1 entities ---')
    print(json.dumps(entities, indent=2, ensure_ascii=False))


def test_invoke_extraction_page2(require_model):
    """Page 2: AWS Prototyping / Innovation Flywheel."""
    entities = invoke_extraction(SAMPLE_TEXT_2, language='ko')

    assert isinstance(entities, list)
    assert len(entities) > 0

    for ent in entities:
        assert 'name' in ent
        assert 'mentioned_in' in ent

    print('\n--- Page 2 entities ---')
    print(json.dumps(entities, indent=2, ensure_ascii=False))


def test_cross_page_entity_overlap(require_model):
    """Compare entities from page 1 and page 2 to check cross-page connections."""
    entities_p1 = invoke_extraction(SAMPLE_TEXT, language='ko')
    entities_p2 = invoke_extraction(SAMPLE_TEXT_2, language='ko')

    names_p1 = {e['name'] for e in entities_p1}
    names_p2 = {e['name'] for e in entities_p2}

    shared = names_p1 & names_p2
    only_p1 = names_p1 - names_p2
    only_p2 = names_p2 - names_p1

    print('\n--- Cross-page entity overlap ---')
    print(f'Page 1: {len(names_p1)} entities')
    print(f'Page 2: {len(names_p2)} entities')
    print(f'Shared:  {len(shared)} entities → {shared or "none"}')
    print(f'Only P1: {only_p1 or "none"}')
    print(f'Only P2: {only_p2 or "none"}')

    assert isinstance(shared, set)
