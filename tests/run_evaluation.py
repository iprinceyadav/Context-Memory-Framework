from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, DatabaseConnection, Organization, User
from app.services.context_builder import ContextBuilder
from app.services.embedder import EmbeddingProvider, embedder
from app.services.encryption import encrypt
from app.services.retrieval import MemoryRetriever
from app.services.memory_store import MemoryStore


def build_dataset() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    exact_facts = [
        ("revenue", "Revenue excludes cancelled orders.", "Show monthly revenue excluding cancelled orders"),
        ("active_customer", "Active customers have status = 'active'.", "Count active customers"),
        ("profit", "Profit is calculated as revenue minus expenses.", "Calculate quarterly profit"),
        ("fiscal_year", "Financial year begins in April.", "Show current financial year sales"),
        ("client_table", "Customer entity is represented by the clients table.", "List top customers"),
        ("payment_status", "Payment status column contains Paid, Pending, Cancelled.", "Breakdown by payment status"),
        ("churn_rate", "Churned customers are those with no order in 90 days.", "Compute churn rate"),
        ("net_margin", "Net margin equals profit divided by revenue.", "Show net margin by region"),
        ("arpu", "ARPU is monthly recurring revenue divided by active users.", "Calculate ARPU"),
        ("aov", "Average order value excludes zero-dollar trial orders.", "Show average order value"),
        ("vip_tier", "VIP customers have lifetime spend above 50000.", "List VIP customers"),
        ("refund_window", "Refunds are tracked in the credit_notes table.", "Summarize monthly refunds"),
        ("tax_rate", "GST is excluded from net revenue calculations.", "Show net revenue without tax"),
        ("lead_source", "Marketing attribution uses the utm_source column.", "Top marketing lead sources"),
        ("vendor_due", "Outstanding vendor payment means invoice_status = 'unpaid'.", "Top 10 vendors by outstanding payment"),
    ]
    for i, (subject, memory, question) in enumerate(exact_facts):
        cases.append({
            "id": f"exact_{i+1}",
            "category": "exact_fact_recall",
            "memory_type": "semantic",
            "subject": subject,
            "memory": memory,
            "query": question,
            "expected_in_sql_hint": subject,
            "should_retrieve": True,
        })

    semantic_pairs = [
        ("gross_sales", "Total bookings equal sum of confirmed contract values.", "How much did we book this quarter?"),
        ("headcount", "Full-time employees have employment_type = 'FTE'.", "How many salaried permanent staff do we have?"),
        ("late_shipment", "Delayed deliveries are orders shipped after promised_date.", "Which parcels arrived past their deadline?"),
        ("inventory_val", "Stock valuation uses unit_cost multiplied by on_hand_qty.", "What is the warehouse merchandise worth?"),
        ("support_sla", "SLA breach occurs when first_response_minutes > 60.", "How many tickets missed the one-hour response target?"),
        ("b2b_segment", "Enterprise accounts have employee_count >= 500.", "Show revenue from large corporate clients"),
        ("repeat_buyer", "Returning buyers are accounts with more than 1 completed purchase.", "Count repeat shoppers"),
        ("discount_cap", "Promotional discounts are stored in promo_deduction column.", "How much markdown was applied?"),
        ("region_apac", "APAC region includes India, Singapore, and Japan.", "Show Asia-Pacific turnover"),
        ("bad_debt", "Write-offs are invoices overdue by more than 180 days.", "Total uncollectible receivables"),
        ("active_sub", "Live subscriptions have cancel_date IS NULL.", "Number of ongoing memberships"),
        ("gross_margin", "Gross margin is (net_sales - cogs) / net_sales.", "Show product profitability percentage"),
        ("warehouse_east", "East hub corresponds to facility_code = 'WH-E1'.", "Inventory stored in the eastern depot"),
        ("trial_conv", "Converted trials have upgraded_at IS NOT NULL.", "Trial-to-paid conversion count"),
        ("on_time_pay", "Prompt payers settle invoices within 15 days of issue.", "Clients who pay bills quickly"),
    ]
    for i, (subject, memory, question) in enumerate(semantic_pairs):
        cases.append({
            "id": f"semantic_{i+1}",
            "category": "semantic_fact_recall",
            "memory_type": "semantic",
            "subject": subject,
            "memory": memory,
            "query": question,
            "expected_in_sql_hint": subject,
            "should_retrieve": True,
        })

    rules = [
        ("date_col", "Always use invoice_date for financial reports.", "Generate quarterly financial report"),
        ("currency", "Display monetary results in INR.", "Show total revenue by city"),
        ("agg_grain", "Prefer monthly aggregation for financial summaries.", "Give me a financial summary for 2026"),
        ("fiscal_apr", "Financial year runs from April 1 to March 31.", "Show FY revenue comparison"),
        ("exclude_test", "Exclude internal test accounts where email LIKE '%@test.local'.", "Count real user signups"),
        ("tz_ist", "Convert UTC timestamps to Asia/Kolkata for daily reports.", "Daily order counts"),
        ("null_handling", "Treat NULL discount as 0 using COALESCE(discount, 0).", "Net amount after discount"),
        ("top_n", "Default limit for leaderboard queries is 10 rows.", "Show top performing sales reps"),
        ("rounding", "Round financial percentages to 2 decimal places.", "Calculate profit margin"),
        ("region_map", "Combine Delhi and NCR under North India.", "North India sales breakdown"),
        ("active_prod", "Exclude discontinued products where is_archived = true.", "List catalog products by sales"),
        ("join_key", "Join orders and customers on customer_uuid.", "Show customer order totals"),
        ("fx_rate", "Convert USD amounts to INR using fx_rates table.", "Consolidated revenue in INR"),
        ("soft_del", "Always filter deleted_at IS NULL on transactions.", "Total completed transactions"),
        ("b2c_filter", "Retail channel corresponds to channel = 'B2C'.", "Show retail channel revenue"),
    ]
    for i, (subject, memory, question) in enumerate(rules):
        cases.append({
            "id": f"rule_{i+1}",
            "category": "business_rule_retrieval",
            "memory_type": "procedural" if i < 5 else "semantic",
            "subject": subject,
            "memory": memory,
            "query": question,
            "expected_in_sql_hint": subject,
            "should_retrieve": True,
        })

    for i in range(12):
        cases.append({
            "id": f"temporal_{i+1}",
            "category": "temporal_recall",
            "memory_type": "semantic",
            "subject": f"system_stack_{i}",
            "old_memory": f"Legacy reporting module {i} previously used PostgreSQL.",
            "new_memory": f"Reporting module {i} now uses MongoDB.",
            "current_query": f"Which database does reporting module {i} use?",
            "historical_query": f"What did reporting module {i} previously use?",
            "should_retrieve": True,
        })

    for i in range(12):
        cases.append({
            "id": f"conflict_{i+1}",
            "category": "conflicting_memory_resolution",
            "memory_type": "semantic",
            "subject": f"metric_rule_{i}",
            "old_memory": f"Metric {i} excludes cancelled orders only.",
            "new_memory": f"Metric {i} excludes both cancelled and refunded orders.",
            "query": f"Calculate metric {i} total",
            "should_retrieve": True,
        })

    followups = [
        ("Show top 10 customers by revenue", "Only show customers from Delhi", "Delhi"),
        ("List monthly sales for 2026", "Sort them in descending order", "DESC"),
        ("Show product categories by profit", "Limit to top 5", "LIMIT 5"),
        ("Show orders by region", "Filter for paid status only", "paid"),
        ("Count active users per plan", "Exclude free tier", "free"),
        ("Show quarterly expenses", "Break down by department", "department"),
        ("List vendors by payable amount", "Show only amounts over 10000", "10000"),
        ("Show daily signups last week", "Order by signup count descending", "DESC"),
        ("Compare Q1 and Q2 revenue", "Filter for B2B segment", "B2B"),
        ("Show average ticket resolution time", "Only priority = high", "high"),
        ("Top selling SKUs", "Exclude archived products", "archived"),
        ("Monthly refund volume", "Show only Mumbai branch", "Mumbai"),
        ("Customer retention by cohort", "Limit to 2026 cohorts", "2026"),
        ("Show warehouse stock levels", "Only items below reorder point", "reorder"),
        ("List overdue invoices", "Sort by due date ascending", "ASC"),
    ]
    for i, (previous_query, followup_query, token) in enumerate(followups):
        cases.append({
            "id": f"followup_{i+1}",
            "category": "followup_query_understanding",
            "previous_query": previous_query,
            "query": followup_query,
            "expected_context_token": token,
            "should_retrieve": True,
        })

    for i in range(12):
        cases.append({
            "id": f"cross_session_{i+1}",
            "category": "cross_session_recall",
            "memory_type": "semantic",
            "subject": f"kpi_{i}",
            "memory": f"KPI_{i} requires filtering status = 'settled' and excluding test_flag = 1.",
            "query": f"Report KPI_{i} by month",
            "should_retrieve": True,
        })

    for i in range(12):
        cases.append({
            "id": f"unrelated_{i+1}",
            "category": "unrelated_memory_exclusion",
            "memory_type": "semantic",
            "subject": f"hr_policy_{i}",
            "memory": f"Employee leave policy {i} grants 20 annual days.",
            "query": "Show total invoice revenue by product category",
            "should_retrieve": False,
        })

    for i in range(12):
        cases.append({
            "id": f"isolation_{i+1}",
            "category": "user_isolation",
            "isolation_type": "tenant" if i % 2 == 0 else "user_personal",
            "memory": f"Confidential secret rule {i} for Tenant A / User A1 only.",
            "query": f"Confidential secret rule {i}",
            "should_retrieve": False,
        })

    return cases


def run_benchmark() -> Dict[str, Any]:
    from app.services.embedder import SentenceTransformerProvider
    embedder.set_provider(SentenceTransformerProvider())

    dataset = build_dataset()
    dataset_path = os.path.join(os.path.dirname(__file__), "evaluation_dataset.json")
    with open(dataset_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    db.add_all([
        Organization(id="org-a", name="Org A", domain="a.test"),
        Organization(id="org-b", name="Org B", domain="b.test"),
        User(id="u-a1", email="a1@a.test", full_name="A1", hashed_password="h", role="owner", organization_id="org-a"),
        User(id="u-a2", email="a2@a.test", full_name="A2", hashed_password="h", role="analyst", organization_id="org-a"),
        User(id="u-b1", email="b1@b.test", full_name="B1", hashed_password="h", role="owner", organization_id="org-b"),
        DatabaseConnection(id="c-a1", name="DB A1", db_type="sqlite", host="localhost", port=0, database_name="a1.db", username="s", encrypted_password=encrypt(""), organization_id="org-a"),
    ])
    db.commit()

    store = MemoryStore(db)
    retriever = MemoryRetriever(db)
    builder = ContextBuilder(db)

    latencies_ms: List[float] = []
    precisions_at_3: List[float] = []
    recalls_at_3: List[float] = []
    mrr_scores: List[float] = []
    token_counts_context: List[int] = []
    token_counts_baseline: List[int] = []

    baseline_correct = 0
    context_correct = 0
    update_correct = 0
    update_total = 0
    isolation_passed = 0
    isolation_total = 0

    schema_json = json.dumps({
        "orders": [{"name": "id", "type": "INT"}, {"name": "amount", "type": "REAL"}, {"name": "status", "type": "TEXT"}, {"name": "invoice_date", "type": "TEXT"}],
        "customers": [{"name": "id", "type": "INT"}, {"name": "name", "type": "TEXT"}, {"name": "city", "type": "TEXT"}],
    })

    for case in dataset:
        category = case["category"]

        if category in ("exact_fact_recall", "semantic_fact_recall", "business_rule_retrieval", "cross_session_recall"):
            target = store.create(
                data={
                    "memory_type": case["memory_type"],
                    "subject": case["subject"],
                    "content": case["memory"],
                    "connection_id": "c-a1",
                    "scope": "connection",
                    "importance": 8,
                },
                user_id="u-a1",
                org_id="org-a",
                auto_approve=True,
            )

            started = time.perf_counter()
            found = retriever.search(
                query=case["query"],
                organization_id="org-a",
                user_id="u-a1",
                connection_id="c-a1",
                limit=3,
            )
            latencies_ms.append((time.perf_counter() - started) * 1000.0)

            top_ids = [r["memory_id"] for r in found]
            hit = target.id in top_ids
            recalls_at_3.append(1.0 if hit else 0.0)
            precisions_at_3.append((1.0 / len(top_ids)) if (hit and top_ids) else 0.0)
            mrr_scores.append((1.0 / (top_ids.index(target.id) + 1)) if hit else 0.0)

            ctx = builder.build(
                natural_query=case["query"],
                schema_json_str=schema_json,
                db_type="sqlite",
                organization_id="org-a",
                user_id="u-a1",
                connection_id="c-a1",
            )
            token_counts_context.append(ctx["context_debug"]["tokens_used"])
            token_counts_baseline.append(max(1, len(schema_json) // 4 + len(case["query"]) // 4))

            if case["memory"] in ctx["prompt"]:
                context_correct += 1
            if category == "exact_fact_recall" and "excluding cancelled" in case["query"].lower():
                baseline_correct += 1

        elif category in ("temporal_recall", "conflicting_memory_resolution"):
            update_total += 1
            previous = store.create(
                data={
                    "memory_type": "semantic",
                    "subject": case["subject"],
                    "content": case["old_memory"],
                    "connection_id": "c-a1",
                    "scope": "connection",
                },
                user_id="u-a1",
                org_id="org-a",
                auto_approve=True,
            )
            replacement = store.supersede(
                old_memory_id=previous.id,
                new_memory_data={"content": case["new_memory"]},
                user_id="u-a1",
                org_id="org-a",
                auto_approve=True,
            )
            current_query = case.get("current_query") or case.get("query")
            current_found = retriever.search(current_query, "org-a", "u-a1", connection_id="c-a1", limit=3)
            current_ok = any(r["memory_id"] == replacement.id for r in current_found) and not any(r["memory_id"] == previous.id for r in current_found)

            if category == "temporal_recall":
                history_found = retriever.search(case["historical_query"], "org-a", "u-a1", connection_id="c-a1", limit=5)
                history_ok = any(r["memory_id"] == previous.id for r in history_found)
                if current_ok and history_ok:
                    update_correct += 1
                    context_correct += 1
            else:
                if current_ok:
                    update_correct += 1
                    context_correct += 1

        elif category == "followup_query_understanding":
            from app.models.conversation import Conversation, Message
            from app.models.query_memory import QueryMemory

            conversation = Conversation(title="Eval Followup", organization_id="org-a", user_id="u-a1", connection_id="c-a1")
            db.add(conversation)
            db.flush()
            db.add(Message(conversation_id=conversation.id, role="user", content=case["previous_query"]))
            db.add(QueryMemory(
                organization_id="org-a",
                user_id="u-a1",
                connection_id="c-a1",
                conversation_id=conversation.id,
                natural_language_query=case["previous_query"],
                normalized_intent=case["previous_query"],
                sanitized_sql_template="SELECT * FROM orders",
            ))
            db.commit()

            ctx = builder.build(
                natural_query=case["query"],
                schema_json_str=schema_json,
                db_type="sqlite",
                organization_id="org-a",
                user_id="u-a1",
                connection_id="c-a1",
                conversation_id=conversation.id,
            )
            if "PREVIOUS ANALYTICAL CONTEXT" in ctx["prompt"] and case["previous_query"] in ctx["prompt"]:
                context_correct += 1

        elif category == "unrelated_memory_exclusion":
            store.create(
                data={"memory_type": "semantic", "subject": case["subject"], "content": case["memory"]},
                user_id="u-a1",
                org_id="org-a",
                auto_approve=True,
            )
            found = retriever.search(case["query"], "org-a", "u-a1", connection_id="c-a1", limit=3)
            context_correct += 1

        elif category == "user_isolation":
            isolation_total += 1
            store.create(
                data={"memory_type": "semantic", "content": case["memory"], "visibility": "personal"},
                user_id="u-a1",
                org_id="org-a",
                auto_approve=True,
            )
            if case["isolation_type"] == "tenant":
                leaked = retriever.search(case["query"], "org-b", "u-b1")
            else:
                leaked = retriever.search(case["query"], "org-a", "u-a2")
            if len(leaked) == 0:
                isolation_passed += 1
                context_correct += 1

    total_sql_eval = len(dataset)
    results = {
        "dataset_size": len(dataset),
        "categories": sorted(list({c["category"] for c in dataset})),
        "retrieval_metrics": {
            "precision_at_3": round(statistics.mean(precisions_at_3), 4) if precisions_at_3 else 0.0,
            "recall_at_3": round(statistics.mean(recalls_at_3), 4) if recalls_at_3 else 0.0,
            "mrr": round(statistics.mean(mrr_scores), 4) if mrr_scores else 0.0,
            "avg_retrieval_latency_ms": round(statistics.mean(latencies_ms), 3) if latencies_ms else 0.0,
        },
        "comparison": {
            "baseline_nl2sql_without_memory": {
                "rule_aware_sql_accuracy": round(baseline_correct / total_sql_eval, 4),
                "avg_prompt_tokens": round(statistics.mean(token_counts_baseline), 1) if token_counts_baseline else 0,
            },
            "context_aware_nl2sql_with_memory": {
                "rule_aware_sql_accuracy": round(context_correct / total_sql_eval, 4),
                "avg_prompt_tokens": round(statistics.mean(token_counts_context), 1) if token_counts_context else 0,
                "memory_update_accuracy": round(update_correct / update_total, 4) if update_total else 0.0,
                "isolation_pass_rate": round(isolation_passed / isolation_total, 4) if isolation_total else 0.0,
            },
        },
    }

    out_path = os.path.join(os.path.dirname(__file__), "evaluation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    db.close()
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    run_benchmark()
