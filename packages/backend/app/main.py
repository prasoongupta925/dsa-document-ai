from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import access_log
from app.routers import (
    agents,
    applicants,
    artifacts,
    branches,
    chat,
    datasets,
    documents,
    eligibility,
    file_check,
    graph,
    health,
    integrations,
    projects,
    prompts,
    sagemaker,
    workflows,
)

access_log.install()

app = FastAPI(
    openapi_tags=[
        {"name": "health", "description": "헬스 체크"},
        {"name": "projects", "description": "프로젝트 관리"},
        {"name": "documents", "description": "문서 관리"},
        {"name": "workflows", "description": "워크플로우 관리"},
        {"name": "chat", "description": "채팅 기록 관리"},
        {"name": "agents", "description": "커스텀 에이전트 관리"},
        {"name": "artifacts", "description": "아티팩트 관리"},
        {"name": "prompts", "description": "프롬프트 관리"},
        {"name": "sagemaker", "description": "SageMaker 엔드포인트 관리"},
        {"name": "graph", "description": "지식 그래프 관리"},
        {"name": "datasets", "description": "정형 데이터셋 조회"},
        {
            "name": "file-check",
            "description": "Deterministic loan-file check (READY / NOT READY) for external systems such as a CRM",
        },
        {
            "name": "eligibility",
            "description": "Per-lender loan eligibility (CIBIL page): fixed formulas, SAMPLE policies, indicative",
        },
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(applicants.router)
app.include_router(artifacts.router)
app.include_router(branches.router)
app.include_router(chat.router)
app.include_router(datasets.router)
app.include_router(documents.router)
app.include_router(eligibility.router)
app.include_router(file_check.router)
app.include_router(graph.router)
app.include_router(health.router)
app.include_router(integrations.router)
app.include_router(projects.router)
app.include_router(prompts.router)
app.include_router(sagemaker.router)
app.include_router(workflows.router)
