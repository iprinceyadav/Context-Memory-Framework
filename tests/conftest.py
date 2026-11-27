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
from app.services.embedding_service import EmbeddingProvider, embedding_service
from app.services.encryption import encrypt_password


class DeterministicTestEmbeddingProvider(EmbeddingProvider):
    """Fast, deterministic 64-dim bag-of-words + trigram embedding provider for unit tests."""

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
def use_deterministic_embeddings():
    """Avoid downloading/loading heavy torch weights during automated tests."""
    prev_provider = embedding_service._provider
    embedding_service.set_provider(DeterministicTestEmbeddingProvider())
    yield
    embedding_service.set_provider(prev_provider)


@pytest.fixture()
def test_db():
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
def test_client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def sample_org(test_db):
    org = Organization(id="org-a", name="Tenant A", domain="tenanta.test")
    test_db.add(org)
    test_db.commit()
    test_db.refresh(org)
    return org


@pytest.fixture()
def other_org(test_db):
    org = Organization(id="org-b", name="Tenant B", domain="tenantb.test")
    test_db.add(org)
    test_db.commit()
    test_db.refresh(org)
    return org


@pytest.fixture()
def sample_user(test_db, sample_org):
    user = User(
        id="user-a1",
        email="a1@tenanta.test",
        full_name="Alice Analyst",
        hashed_password="hashed_pw",
        role="analyst",
        organization_id=sample_org.id,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture()
def second_user(test_db, sample_org):
    user = User(
        id="user-a2",
        email="a2@tenanta.test",
        full_name="Bob Viewer",
        hashed_password="hashed_pw",
        role="viewer",
        organization_id=sample_org.id,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture()
def other_user(test_db, other_org):
    user = User(
        id="user-b1",
        email="b1@tenantb.test",
        full_name="Carol TenantB",
        hashed_password="hashed_pw",
        role="owner",
        organization_id=other_org.id,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture()
def sample_connection(test_db, sample_org):
    conn = DatabaseConnection(
        id="conn-a1",
        name="Sales DB",
        db_type="sqlite",
        host="localhost",
        port=0,
        database_name="sales.db",
        username="sqlite",
        encrypted_password=encrypt_password(""),
        organization_id=sample_org.id,
    )
    test_db.add(conn)
    test_db.commit()
    test_db.refresh(conn)
    return conn


@pytest.fixture()
def second_connection(test_db, sample_org):
    conn = DatabaseConnection(
        id="conn-a2",
        name="HR DB",
        db_type="sqlite",
        host="localhost",
        port=0,
        database_name="hr.db",
        username="sqlite",
        encrypted_password=encrypt_password(""),
        organization_id=sample_org.id,
    )
    test_db.add(conn)
    test_db.commit()
    test_db.refresh(conn)
    return conn

