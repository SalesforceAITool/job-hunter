# Lightning Web Components (LWC): Project Experience

Interview and resume reference. Everything under "Projects" comes from the resume content in `engine/resume/orig_content.py`.
The "How to explain it" lines describe how the work is usually built; if an interviewer asks for details you don't remember, say so rather than guessing.

See also: [Health Cloud experience](health-cloud-experience.md).

---

## Summary

| Project | Cloud | LWC work |
|---|---|---|
| Amkor Technology (current) | Sales Cloud + CPQ | Part of an org with **58 LWCs**: pricing-rule authoring and preview LWCs, approval LWCs |
| Dubai Holding | Real estate (Sales/Service) | **One generic LWC producing 9 finance reports** as CSV, getting past the 50k SOQL row limit |
| Cell-therapy company (Cellution) | **Health Cloud + Experience Cloud** | LWC for **cell match results and vial selection for orders** |
| Druce | Real estate | LWCs to view, edit and update properties; **photo approval** before listing |
| Waste Management (US) | Salesforce + Heroku | LWC + trigger for **customer alerts**, synced to Heroku on publish, built with **LDS and SLDS** |
| General | — | **Custom report framework** using OOP (inheritance, interfaces, abstract/virtual classes) with LWC |

---

## Projects

### 1. Dubai Holding: generic finance-report LWC (strongest LWC story)

**Problem:** The finance team needed 9 different payment-plan reports downloaded as CSV for payment checks and invoicing. Standard reports and server-side export hit governor limits on large data.

**What was built:**
- **One generic LWC** for all 9 reports. A dropdown picks the report, and filters (date range, payment status) narrow the output.
- **SOQL queries stored in Custom Metadata**, so reports are added or changed without touching LWC code.
- **Large data volumes:** Apex is called **asynchronously several times**, fetching records in batches to get past the **50,000-row SOQL limit**.
- **Avoiding heap and CPU limits:** records are processed and the **CSV is built in JavaScript on the client**, not in Apex.
- **Result:** a fast, filter-driven download that improved how the finance team checked payment plans and produced invoices.

**How to explain it:** Imperative Apex calls from the LWC (not `@wire`, because each call is triggered by the user and repeated in batches), `@AuraEnabled` Apex methods, a metadata-driven query builder, and a CSV string built in JS and downloaded through a generated link.

### 2. Cell-therapy company (Cellution): Health Cloud + Experience Cloud

**What was built:**
- An LWC that **shows cell match results** and lets users **select vial records to create an order**.
- Supporting Apex (trigger framework, **cell selection** class), plus Experience Cloud pages for **CRO, Cryoport and hospital** communities.

**How to explain it:** A data table of matching vials with row selection that passes the selected records to Apex to create the order. When an LWC runs in Experience Cloud, it has to respect partner-user sharing, so each partner sees only its own records.

### 3. Amkor Technology: CPQ pricing platform (current)

**LWC-related work:**
- The org has **58 LWCs, 70 Lightning pages and 132 flows**.
- **Authoring and preview LWCs** for the configurable pricing-formula rule engine. Admins write expressions and preview the result; a safe evaluator runs them without dynamic Apex.
- **LWCs supporting approvals** on the Salesforce Approvals framework (`ApprovalWorkItem`).
- Editing grids driven by **field-set metadata** keyed on profile and service group, refreshed live by a **platform event** after batch updates.

**How to explain it:** Metadata-driven UI (field sets and Custom Metadata decide which columns appear), so new pricing setups don't need code changes, plus live refresh through a platform event subscription (`lightning/empApi`).

### 4. Druce: property management

**What was built:**
- LWCs to **view, edit and update property information**.
- A **custom approval process**: after a photographer uploads property pictures, they go through approval before the listing goes live.
- Same project: REST integrations with the Druce website, Zoopla, Rightmove and Fixflo, deployed through Azure.

### 5. Waste Management (US)

**What was built:**
- An **LWC and trigger** that create customer alerts in Salesforce and **sync back to Heroku only when published**.
- Built with **Lightning Data Service** and the **Lightning Design System**.
- OAuth-based **Apex REST services** for external apps.

### 6. Custom report framework (general experience)

- A reusable framework for custom reports, built with **OOP in Apex** (interfaces, abstract and virtual classes, inheritance) and an LWC front end. New report types are added by extending a base class instead of writing a new component.

---

## 60-second interview answer: "Tell me about your LWC experience"

> "I've built LWCs on most of my projects. My best example is at Dubai Holding: one generic LWC produced 9 finance reports. The SOQL for each report lived in Custom Metadata, so adding a report needed no code. To get past the 50,000-row limit, the component called Apex asynchronously in batches and built the CSV in JavaScript, which also avoided heap and CPU limits. On a Health Cloud project for a cell-therapy company, I built an LWC that showed cell match results and let users pick vials for an order, running inside Experience Cloud portals for CROs and hospitals. In my current CPQ work at Amkor, the org has 58 LWCs, including authoring and preview components for a configurable pricing-rule engine and components that support approvals."

---

## Common LWC interview questions

1. **`@wire` vs imperative Apex?** `@wire` is reactive, cached and read-only (needs `cacheable=true`). Imperative calls are for user actions, DML, or calls you control, as in the Dubai batching.
2. **How do components communicate?** Parent to child with `@api` properties and methods; child to parent with `CustomEvent`; unrelated components with **Lightning Message Service**.
3. **Lifecycle hooks?** `constructor`, `connectedCallback`, `renderedCallback`, `disconnectedCallback`, `errorCallback`.
4. **Lightning Data Service?** `lightning-record-form` / `record-edit-form`, `getRecord`, `updateRecord`: no Apex, with a shared cache and FLS / sharing enforced automatically. Used on the Waste Management alerts.
5. **Large data volumes in LWC?** Paging or batched imperative calls, client-side processing, lazy loading in `lightning-datatable`, and keeping Apex responses small (Dubai example).
6. **Security?** `with sharing`, `WITH USER_MODE` / `Security.stripInaccessible`, and Lightning Locker / Lightning Web Security.
7. **Making components configurable?** Expose `@api` properties in `js-meta.xml` for App Builder and Experience Builder, and drive behaviour from Custom Metadata or field sets (Dubai, Amkor).
8. **Real-time refresh?** Subscribe to platform events with `lightning/empApi`, or use `refreshApex` / `notifyRecordUpdateAvailable`.
9. **Testing?** Jest with `@salesforce/sfdx-lwc-jest`, mocking wire adapters and Apex.
