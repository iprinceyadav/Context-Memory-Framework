# Sample company databases

Five synthetic SQLite databases for trying CMF. All data is made up. They cover different business scenarios. Connect one on the **Connect Database → SQLite** screen, either by uploading the file or by typing its path (for example `sample_dbs/retail_store.db`).

Regenerate them, always with the same data:

```powershell
python sample_dbs/generate_sample_dbs.py
```

| File | Scenario | Tables | Rows |
|---|---|---|---|
| `retail_store.db` | Omnichannel retail chain | stores, categories, products, customers, orders, order_items, payments, returns | ~64k |
| `saas_company.db` | B2B SaaS subscriptions | plans, accounts, users, subscriptions, invoices, feature_usage, support_tickets | ~43k |
| `hr_company.db` | People and payroll | departments, employees, salary_history, leave_requests, performance_reviews, job_openings | ~18k |
| `logistics_company.db` | Warehousing and delivery | warehouses, suppliers, items, inventory, vehicles, drivers, shipments, shipment_events | ~49k |
| `finance_accounting.db` | Payables, receivables, expenses | cost_centers, gl_accounts, vendors, clients, purchase_bills, vendor_payments, sales_invoices, client_receipts, expenses, budgets | ~28k |

Dates run from 2024-01-01 to 2026-11-27 (some history goes back further). Each database has foreign keys, so schema retrieval can follow table relationships.

## Things to try

**Retail**
- "Show monthly revenue for 2026."
- Teach a rule: "For all revenue reports, exclude cancelled and refunded orders and use invoice_date." Then ask "Revenue by region" and check that the rule shows up under Context Used.
- Follow-ups: "Top 10 customers by revenue", then "now only Gold tier", then "same for last month".
- Masking: the `card_number` column in `payments` is hidden for non-admin users.

**SaaS**
- "What is MRR by plan for active subscriptions?"
- Teach a rule: "Churned means status is cancelled. Ignore trial accounts in churn reports."
- "Which accounts have overdue invoices and urgent open tickets?"
- Masking: `api_key` and `password_hash` are sensitive.

**HR**
- "Headcount by department, excluding terminated employees."
- Teach a rule: "Attrition rate = terminations this year / headcount at the start of the year."
- "Average salary by level and department." Run it as an admin and as an employee to compare; `ssn` is always masked for non-admins.
- Change request: "Approve all pending leave requests for employee 42." It needs a grant on `leave_requests` and an admin's approval.

**Logistics**
- "On-time delivery rate by warehouse." On time means `delivered_on <= promised_on`.
- "Items below reorder level in each warehouse."
- Change request: "Mark shipment TRK00000123 as delivered."

**Finance**
- "Top 10 vendors by outstanding payment." Outstanding means bills that are open, overdue or partially paid.
- Teach a rule: "Display monetary results in INR lakhs" or "Our financial year starts in April."
- "Budget vs actual expenses per cost center for FY2025-26."
- Masking: `bank_account_number` is not in the default `SENSITIVE_COLUMNS`. Add it to `.env` to see masking on a new column.
