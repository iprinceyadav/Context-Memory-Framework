import hashlib
import math
from typing import List
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.main import app
from app.models import Base, DatabaseConnection, Organization, User
from app.services.embedder import EmbeddingProvider, embedder
from app.services.encryption import encrypt
from app.services.llm import llm
from app.services.reranker import RerankProvider, reranker
from app.services.text import tokenize
from app.services.vector_store import vector_store


class HashEmbeddingProvider(EmbeddingProvider):
    _DIM = 64

    @property
    def dimension(self) -> int:
        return self._DIM

    def encode(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self._DIM
            tokens = [t.strip(".,!?:;'\"") for t in text.lower().split() if t.strip()]
            for tok in tokens:
                idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % self._DIM
                vec[idx] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


@pytest.fixture(autouse=True)
def hash_embeddings():
    prev_provider = embedder._provider
    embedder.set_provider(HashEmbeddingProvider())
    yield
    embedder.set_provider(prev_provider)


class OverlapReranker(RerankProvider):
    name = "test-overlap"

    def score(self, query, documents):
        terms = tokenize(query)
        return [len(terms & tokenize(doc)) / len(terms) if terms else 0.0 for doc in documents]


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    monkeypatch.setattr(llm, "enabled", False)


@pytest.fixture(autouse=True)
def rag_stack(hash_embeddings):
    vector_store.configure(":memory:")
    previous = reranker._provider
    reranker.set_provider(OverlapReranker())
    yield
    reranker.set_provider(previous)
    vector_store.configure(None)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def sample_org(db_session):
    org = Organization(id="org-a", name="Tenant A", domain="tenanta.test")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


@pytest.fixture()
def other_org(db_session):
    org = Organization(id="org-b", name="Tenant B", domain="tenantb.test")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


@pytest.fixture()
def sample_user(db_session, sample_org):
    user = User(
        id="user-a1",
        email="a1@tenanta.test",
        full_name="Alice Analyst",
        hashed_password="hashed_pw",
        role="analyst",
        organization_id=sample_org.id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def second_user(db_session, sample_org):
    user = User(
        id="user-a2",
        email="a2@tenanta.test",
        full_name="Bob Viewer",
        hashed_password="hashed_pw",
        role="viewer",
        organization_id=sample_org.id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def other_user(db_session, other_org):
    user = User(
        id="user-b1",
        email="b1@tenantb.test",
        full_name="Carol TenantB",
        hashed_password="hashed_pw",
        role="owner",
        organization_id=other_org.id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def sample_connection(db_session, sample_org):
    conn = DatabaseConnection(
        id="conn-a1",
        name="Sales DB",
        db_type="sqlite",
        host="localhost",
        port=0,
        database_name="sales.db",
        username="sqlite",
        encrypted_password=encrypt(""),
        organization_id=sample_org.id,
    )
    db_session.add(conn)
    db_session.commit()
    db_session.refresh(conn)
    return conn


@pytest.fixture()
def second_connection(db_session, sample_org):
    conn = DatabaseConnection(
        id="conn-a2",
        name="HR DB",
        db_type="sqlite",
        host="localhost",
        port=0,
        database_name="hr.db",
        username="sqlite",
        encrypted_password=encrypt(""),
        organization_id=sample_org.id,
    )
    db_session.add(conn)
    db_session.commit()
    db_session.refresh(conn)
    return conn


def pytest_configure(config):
    config.addinivalue_line("markers", "fresh_visitor: skip choosing a user type before the test")
