# Salesforce Health Cloud: Experience and Project Details

Interview and resume reference. Section 4 covers the project actually delivered (source: `engine/resume/orig_content.py`).
Sections 1–3 are Health Cloud product knowledge; talk about them as knowledge, not as things built on the project.

---

## 1. What Health Cloud is

Salesforce Health Cloud is Salesforce's CRM for healthcare and life sciences. It sits on top of **Service Cloud** and adds:

- a healthcare data model (patients, providers, care plans, clinical data)
- ready-made screens and components for care teams
- security and compliance features for health data

Key facts:

- Started as a **managed package** (namespace `HealthCloudGA`). Most new objects are now **standard core-platform objects** modelled on **HL7 FHIR R4** (`HealthCondition`, `CareObservation`, `MedicationStatement`, ...).
- Built on **Person Accounts**: each patient or member is a Person Account.
- Salesforce signs a **BAA** for HIPAA; usually deployed with **Shield** (encryption, Event Monitoring, Field Audit Trail).
- Used by providers (hospitals, clinics), payers (insurers), medtech, pharma and life sciences. Pharma / clinical-trial work is now also sold as **Life Sciences Cloud**.

## 2. Why companies use Health Cloud

| Problem | How Health Cloud solves it |
|---|---|
| Patient data spread across EHR (Epic, Cerner), labs, pharmacy, billing | **Patient 360**: one view with clinical and non-clinical data |
| Care teams don't coordinate | Shared **care plans, care teams, tasks and timeline** |
| Building a healthcare data model from scratch is slow | **Out-of-the-box FHIR-aligned objects** |
| HIPAA and consent | **Consent management**, Shield encryption, audit trail, BAA |
| External partners (labs, CROs, hospitals) need visibility | **Experience Cloud portals** on the same data |
| Manual intake, assessments, authorizations | **OmniStudio, Flows, assessments, utilization management** |
| Engaging patients outside the clinic | Patient portals, Marketing Cloud, Agentforce / Einstein |

## 3. Main Health Cloud features

### Patient / member management
- **Person Account as patient**, with record types for Patient, Member, Caregiver, Provider.
- **Patient Card / Patient 360 console**: conditions, medications, care team, open tasks on one page.
- **Industries Timeline**: chronological view of encounters, cases, tasks, medications, observations.
- **Households and Actionable Relationship Center (ARC)**: visual map of family, caregivers, providers, payers (Account-Contact and Contact-Contact relationships).

### Care coordination
- **Integrated Care Management**: `CarePlan`, `CarePlanTemplate`, `GoalDefinition`, `CarePlanProblem`, interventions, tasks.
- **Care teams**: members with roles (physician, nurse, care coordinator).
- **Care Program Management**: `CareProgram`, `CareProgramEnrollee`, `CareProgramProduct` for patient support, disease management and clinical programs.
- **Assessments**: OmniStudio or Discovery Framework (intake, risk questionnaires, social determinants of health).

### Clinical data model (FHIR-aligned)
- `HealthCondition`, `AllergyIntolerance`, `MedicationStatement` / `MedicationRequest`, `CareObservation`, `PatientImmunization`, `ClinicalEncounter`, `PatientMedicalProcedure`, `CodeSet` / `CodeSetBundle` (ICD-10, SNOMED, LOINC).
- **FHIR APIs and EHR integration**: EHR data comes in through APIs, often via an integration layer for HL7v2 / FHIR messages.

### Provider and payer features
- **Provider network management**: `HealthcareProvider`, `HealthcareProviderSpecialty`, `HealthcarePractitionerFacility`, credentialing, contracts.
- **Provider search** for referrals.
- **Utilization management / prior authorization**: `CareRequest`, `CareRequestItem`, `CarePreauth`.
- **Member benefits and claims**: `MemberPlan`, `PurchaserPlan`, `CoverageBenefit`, `ClaimHeader`.
- **Referral management** and **Intelligent Appointment Management** (Salesforce Scheduler).

### Compliance and security
- **Consent management**: `AuthorizationForm`, `AuthorizationFormConsent`, `ContactPointTypeConsent`.
- **Health Cloud permission sets and licenses**, record-level sharing (care team sharing).
- **Shield Platform Encryption, Field Audit Trail, Event Monitoring** for HIPAA audits.

### AI and data
- **Risk stratification**, Health Cloud analytics / CRM Analytics templates.
- **Data Cloud for Health**: unified patient profile from EHR, claims and engagement data.
- **Agentforce / Einstein for Health**: patient-history summaries, scheduling and benefits agents, prompt templates.

### Experience Cloud with Health Cloud
- Patient, provider and partner portals (labs, CROs, logistics) on the same Health Cloud data, using sharing sets and external user licenses.

## 4. Project delivered: Cellution platform (US cell-therapy / life sciences company)

**Clouds:** Health Cloud + Experience Cloud

**Business context:** Patients with rare genetic disorders take part in clinical trials. Matched cells are brought in from T-cell banks and infused into the patients. Cellution gives internal Cell Evolve staff and key external vendors visibility into the whole lifecycle of a study.

| Area | Work done |
|---|---|
| **LWC** | Component showing **cell match results** and letting users **select vial records for an order** |
| **Experience Cloud** | Customized the community and built multiple pages; set up **separate communities for the CRO, Cryoport (cold-chain logistics) and hospitals** |
| **Data model** | Designed the data model and finalized the approach (donors, subjects, cell matches, vials, orders, business entities) |
| **Apex** | Apex classes, triggers, integrations, test classes; **trigger framework** and **cell selection** logic |
| **Batch / scheduling** | **Scheduled Apex** for daily sync of business-entity data |
| **Automation** | Process Builder, workflow rules, **case assignment rules** |
| **Analytics** | **Reports and dashboards** on donor and company information |
| **Support** | New enhancements and support for existing functionality |

**Health Cloud features this maps to:** Person Account / patient model (subjects, donors); Experience Cloud partner portals; clinical-trial lifecycle tracking; Service Cloud case management; sharing so each partner sees only its own records.

### 60-second interview version

> "I worked on a Health Cloud and Experience Cloud implementation for a US cell-therapy company. The platform tracked the whole process: matching donor cells from T-cell banks, ordering vials and shipping them to hospitals for infusion during clinical trials. I designed the data model and trigger framework, and built the LWC that shows cell match results and lets users pick vials for an order. I set up separate Experience Cloud portals for the CRO, the logistics partner Cryoport and the hospitals, so each partner saw only its own part of the study. I also wrote scheduled Apex for the daily data sync, set up case assignment rules and built reports and dashboards on donor data."

## 5. Common interview questions

1. **Why Person Accounts?** A patient is one person but needs both account-level relationships (household, payer) and contact-level ones (care team, caregivers).
2. **How do you protect PHI?** Shield encryption, field-level security, restrictive OWD, care team sharing, sharing sets for portals, Event Monitoring, BAA.
3. **Managed package vs core objects?** Older orgs use `HealthCloudGA__` objects; newer ones use standard FHIR-aligned objects. Migration is a common project.
4. **How does EHR data get in?** FHIR / HL7 via an integration layer and REST APIs into the clinical objects; platform events for near-real-time updates.
5. **How would you build a care plan?** CarePlanTemplate → CarePlan with goals, problems and tasks; Flow assigns the tasks.
6. **How do external partners see only their own data?** Partner Community licenses, sharing sets or sharing rules by account, Apex managed sharing for complex cases.
