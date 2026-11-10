"""Master resume v2: original resume + Amkor CPQ + details from Akshay's own application email.
Written plain and specific so it reads like a person wrote it. Per-job tweaks come from a JSON config:
  headline, summary, skills (list of [label, text]), project_order (list of project keys), out
"""
import json, sys
from reportlab import rl_config
rl_config.useA85 = 0
rl_config.invariant = 1
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Table, TableStyle, SimpleDocTemplate, Paragraph, Spacer, KeepTogether, HRFlowable
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

cfg = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}

INK = colors.HexColor("#1a1a1a"); MUTED = colors.HexColor("#4d4d4d"); RULE = colors.HexColor("#999999")
name_s = ParagraphStyle("n", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=INK)
head_s = ParagraphStyle("hl", fontName="Helvetica", fontSize=10, leading=13, textColor=INK)
contact_s = ParagraphStyle("c", fontName="Helvetica", fontSize=8.8, leading=11.5, textColor=MUTED)
sec_s = ParagraphStyle("s", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=INK, spaceBefore=8, spaceAfter=1)
body = ParagraphStyle("b", fontName="Helvetica", fontSize=9.0, leading=11.6, textColor=INK)
bul = ParagraphStyle("bl", parent=body, leftIndent=11, bulletIndent=2, spaceAfter=1.5)
proj = ParagraphStyle("p", fontName="Helvetica-Bold", fontSize=9.4, leading=12, textColor=INK, spaceBefore=5)
sub = ParagraphStyle("sb", fontName="Helvetica-Oblique", fontSize=8.8, leading=11, textColor=MUTED, spaceAfter=1)


import re as _re
KEYWORDS = sorted(set("""Salesforce CPQ|CPQ|SBQQ|Advanced Approvals|sbaa|Apex REST|Batch Apex|Apex|Lightning Web Components|Lightning Web Component|LWCs|LWC|Aura|Visualforce|Visual force|SOQL/SOSL|SOQL|SOSL|JavaScript|REST API|REST APIs|REST integrations|REST integration|REST|SOAP|SAP|Oracle Fusion|Fusion|Oracle CRM|Heroku Connect|Heroku|Named Credentials|named credential|external credentials|external-credential|platform events|platform event|Platform Events|Custom Metadata|Flows|Flow|Process Builder|Validation Rules|validation rules|Approval Processes|approval processes|Advanced Approvals|permission sets|Permission sets|Sales Cloud|Service Cloud|Experience Cloud|Health Cloud|OmniStudio|Commerce Cloud|Data Cloud|Marketing Cloud|D&amp;B Connect|DUNS|Zoopla|Rightmove|Fixflo|Azure DevOps|Azure|Data Loader|JIRA|middleware|data migration|Data Migration|code reviews|code review|Center of Excellence|governor limits|heap size|CPU|trigger framework|Triggers Framework|Agile|agile|integrations|integration|Agentforce|Prompt Builder|prompt templates|Messaging for In-App and Web|MIAW|Omni-Channel|Einstein Bots|Service Cloud Voice|Amazon Connect|Data Lake Objects|retrievers|Nissan|ASDA|Singapore Airlines|Forcesap IT Solution|9 finance reports|9 distinct reports|9 reports|7-stage|636|53 package families|500-record|58 permission sets|11 certifications|7+ years|Deloitte|Hexaware|Siemens|Amkor Technology|Amkor|Emaar|Dubai Holding|Belden|IQVIA|Home Depot|Platform Developer I and II|Sharing and Visibility Architect|OmniStudio Developer|Advanced Administrator|Platform App Builder|Data Cloud Consultant|AI Specialist|AI Associate|11 Salesforce certifications|Claude Code|OpenAI Codex|Codex|Cursor|prompt engineering|AI coding tools|AI-assisted|Git|Bitbucket|CI/CD pipeline|CI/CD|VS Code|Python for Everybody|Introduction to Data Science in Python|governor limits""".split("|")), key=len, reverse=True)
_KW = _re.compile(r"(?<![\w/])(" + "|".join(_re.escape(k) for k in KEYWORDS) + r")(?![\w])")
def hl(text):
    out, depth = [], 0
    for part in _re.split(r"(<[^>]+>)", text):
        if part.startswith("<"):
            t = part.lower()
            if t.startswith("<b") or t.startswith("<a"): depth += 1
            elif t.startswith("</b") or t.startswith("</a"): depth -= 1
            out.append(part)
        else:
            out.append(part if depth > 0 else _KW.sub(r"<b>\1</b>", part))
    return "".join(out)

def section(t):
    return [Paragraph(t.upper(), sec_s), HRFlowable(width="100%", thickness=0.5, color=RULE, spaceBefore=1, spaceAfter=3)]
def bullets(items):
    return [Paragraph(hl(i), bul, bulletText="•") for i in items]

# Default layout = Akshay's original Word resume (engine/resume/classic.py). layout "modern" keeps the older design.
if cfg.get("layout", "classic") == "classic":
    import classic
    print(classic.build(cfg, cfg.get("out", "Akshay_Verma_Resume.pdf")))
    raise SystemExit(0)

HEADLINE = "Salesforce Developer (7+ years) | Agentforce, CPQ, Apex, LWC, Integrations | Salesforce + AI"
SUMMARY = ("Salesforce developer with 7+ years in consulting with Forcesap IT Solution, Hexaware and Deloitte. "
           "At Forcesap IT Solution I build Agentforce agents (Nissan service and sales coach agents, an ASDA customer service agent, and "
           "Service Cloud Voice agent assist for Singapore Airlines) on Data Cloud, Service Cloud and Messaging for In-App and Web, "
           "and own a Salesforce CPQ pricing platform for Amkor Technology, including its SAP integration. "
           "Earlier work includes a global Sales Cloud rollout for Siemens, real estate builds for Emaar and Dubai Holding, "
           "and projects for Belden, IQVIA and Home Depot. Most of my work sits where Apex and LWC meet other systems: SAP, Oracle Fusion, Heroku and REST APIs. "
           "Based in India and used to working US hours with distributed teams.")
SKILLS = [
    ["Development", "Apex (triggers, batch, queueable, schedulable), Lightning Web Components, Aura, Visualforce, SOQL/SOSL, JavaScript (ES6+)"],
    ["Agentforce", "Agentforce agents (topics, instructions, custom actions with Flows, Apex and prompt templates), Prompt Builder, Data Cloud data streams, DLOs, search indexes and retrievers, Knowledge, Messaging for In-App and Web, Omni-Channel, Einstein Bots, Service Cloud Voice with Amazon Connect"],
    ["CPQ", "Salesforce CPQ (SBQQ), Advanced Approvals (sbaa), quote line routing, custom pricing engines, approval history"],
    ["Clouds", "Sales Cloud, Service Cloud, Experience Cloud, Health Cloud, OmniStudio, Commerce Cloud"],
    ["Integration", "REST and SOAP, named and external credentials, platform events, SAP, Oracle Fusion, Heroku Connect"],
    ["Platform", "Flows, validation rules, approval processes, Custom Metadata, permission sets and groups, sharing rules, reports and dashboards"],
    ["AI tools", "Claude Code, OpenAI Codex, Cursor, prompt engineering (hands-on experience), AI-assisted code review and test generation"],
    ["DevOps", "Git, Bitbucket, CI/CD pipeline setup, Azure DevOps, VS Code, JIRA, Data Loader, release planning, code review, Agile"],
]
CERTS = ("11 Salesforce certifications: Platform Developer I and II, Sharing and Visibility Architect, OmniStudio Developer, "
         "Administrator, Advanced Administrator, Platform App Builder, Data Cloud Consultant, AI Specialist, AI Associate, Associate. "
         "Also: Python for Everybody and Introduction to Data Science in Python (University of Michigan, Coursera)")
CALLOUT = ("<b>Salesforce + AI:</b> 7+ years of hands-on Salesforce, recent <b>Agentforce</b> delivery for Nissan, ASDA and Singapore Airlines, and <b>experience in prompt engineering</b> and AI coding tools. "
           "I build and ship Salesforce solutions end to end with <b>Claude Code</b>, <b>Codex</b> and <b>Cursor</b>, and review every change against Salesforce best practices, governor limits and security before it goes live.")
AI_POINTS = [
    "Use Claude Code, OpenAI Codex and Cursor daily on Salesforce work: drafting Apex, LWC and test classes, refactoring older code and writing deployment scripts. Every change gets my own review for governor limits, security and org conventions before it ships.",
    "Delivered Amkor CPQ features end to end this way, from design notes to tested code, pull request review and deployment, cutting delivery time while keeping code quality and test coverage high.",
    "Experienced in prompt engineering for development work: writing clear specs, org context and examples so AI output follows the existing org patterns, naming and security model.",
]
ROLES = [
    ("Salesforce Consultant", "Forcesap IT Solution", "Oct 2023 &ndash; Present", "India"),
    ("Senior Salesforce Developer", "Hexaware Technologies", "Sep 2021 &ndash; Oct 2023", "Dubai, UAE"),
    ("Consultant", "Deloitte", "Mar 2019 &ndash; Mar 2021", "Bangalore"),
    ("System Executive", "Trekbin Solutions Pvt. Ltd.", "Sep 2017 &ndash; Jan 2019", "Bangalore"),
]
PROJECTS = {
 "nissan": ("Nissan &mdash; Forcesap IT Solution", "Agentforce service agent and sales coach agent on Data Cloud and Service Cloud", [
   "Built an Agentforce service agent for general FAQs and WISMO (where is my order) requests: agent topics mapped to custom actions built with Flows, Apex and prompt templates, so the agent picks the right topic and pulls live data for each answer.",
   "Grounded the answers in Data Cloud: uploaded knowledge files and datasets, built a Knowledge Articles data stream, data model and Data Lake Objects, and used them for warranty, vehicle service and FAQ responses.",
   "Deployed the agent on an Experience Cloud site through Messaging for In-App and Web (MIAW), with automatic case creation and Omni-Channel routing to the right service team.",
   "Connected Einstein Bots and the Agentforce chat so conversations hand over cleanly between them, and set up permission sets, security and routing for the agent.",
   "Built a sales coach agent with topics for account analysis, deal risk scoring, opportunity and renewal coaching and pricing insights, using prompt templates over account, opportunity and sentiment data and Flows for next-best-action suggestions.",
 ]),
 "asda": ("ASDA (UK retail and e-commerce) &mdash; Forcesap IT Solution", "Agentforce customer service agent", [
   "Designed and configured the Agentforce agent: topics, instructions, prompt templates and conversation flows for order and delivery questions.",
   "Built the FAQ layer on Knowledge Articles in the Data Library, with a Data Cloud data stream, DLOs, a search index and retrievers so answers come from approved content.",
   "Connected REST APIs for real-time order status, delivery updates and customer details, tested with Postman.",
   "Set up Messaging for In-App and Web: channels, routing, pre-chat forms, UI customization and escalation to human agents, with backend automation in Flows and Einstein Bots.",
 ]),
 "scv": ("Singapore Airlines &mdash; Forcesap IT Solution", "Service Cloud Voice with Amazon Connect and Agentforce agent assist", [
   "Configured Service Cloud Voice with Amazon Connect for inbound customer calls: Omni-Channel voice routing, call recording and real-time transcripts on the Voice Call object.",
   "Added Agentforce prompts that give agents AI-generated customer summaries and suggested responses during live calls, and built a POC connecting Agentforce with Amazon Connect partner telephony.",
   "Matched callers to customer records with Apex and Flow for screen pop and case creation, and customized the Service Console with Lightning App Builder.",
 ]),
 "amkor": ("Amkor Technology &mdash; Forcesap IT Solution", "Salesforce CPQ pricing and quoting platform for semiconductor assembly and test (current)", [
   "Own the org end to end: 79 custom objects, 134 Apex classes with test coverage, 58 LWCs and 132 flows, used across 18 sales regions, 7 plants and 10 business units.",
   "Replaced a spreadsheet-and-email approval process with a 7-stage quote line routing flow on Salesforce CPQ and Advanced Approvals. Every line now has one owner and a clear escalation path across the 6 pricing business units.",
   "Built the pricing engine that sits under CPQ. Assembly and test prices depend on package geometry, lead count and materials rather than a SKU, so codes are derived from Custom Metadata (636 mapping rules covering 53 package families). Analysts now add a new package type without a dev ticket.",
   "Moved hard-coded pricing formulas into rules that admins configure. Wrote a small expression evaluator (tokenizer plus infix-to-postfix parser) so the rules run without dynamic Apex.",
   "Built the SAP price upload. Large agreements are split into sequenced 500-record messages with retries and delta handling for amendments, and a REST endpoint matches SAP acknowledgements back to each record. Also built 4 inbound REST services on named and external credentials.",
   "Set up the delivery pipeline end to end: Git branching on Bitbucket and a CI/CD pipeline that validates and deploys across sandboxes and production, with work tracked in JIRA and built in VS Code.",
   "Added revision snapshots with rollback, an hourly email of stuck or failed price records, an Excel upload for customer RFQs where the column mapping is stored as data, and moved access from 30 profiles to 58 permission sets.",
 ]),
 "siemens": ("Siemens (through Deloitte)", "Global Sales Cloud rollout replacing Oracle CRM", [
   "Built prototypes and delivered integrations between Salesforce and the surrounding systems.",
   "Defined a middleware-based integration architecture for the rollout and ran code reviews against Salesforce best practices.",
   "Groomed sprint stories with stakeholders, built POCs ahead of sprints, planned releases and handled data migration from the old CRM.",
   "Demoed user journeys to product owners and sponsors, and helped scope follow-on work that became additional revenue.",
 ]),
 "dubai": ("Dubai Holding", "Finance reporting on the Payment Plan object", [
   "Built one generic LWC that produces 9 finance reports, with a dropdown to pick the report and filters for dates and payment status.",
   "Kept each report's SOQL in Custom Metadata, so the finance team's changes don't need a deployment.",
   "Got past the 50,000-row query limit by calling Apex asynchronously in batches and building the CSV in the browser, which also kept heap and CPU usage low.",
 ]),
 "emaar": ("Emaar", "Moving bookings and invoicing from Oracle to Salesforce", [
   "Built the REST integration from Salesforce to Oracle Fusion for property bookings, customer creation and cancellations.",
   "Worked with the Fusion team on payload formats and validation, and added error handling and logging so failed calls were easy to trace.",
 ]),
 "belden": ("Belden Inc. (US)", "Account deduplication on Sales Cloud", [
   "Wrote a batch process that finds duplicate accounts coming from several legacy systems, matched on physical address and DUNS number.",
   "Picked the surviving record by recency and completeness, merged fields like phone numbers instead of dropping them, and excluded Belden record types and Marketing Cloud records from the merge.",
 ]),
 "druce": ("Druce", "Property management for the secondary property market", [
   "Built REST integrations with the Druce website for live listing sync, and with Zoopla, Rightmove and Fixflo for pricing and maintenance data.",
   "Built LWCs for managing property records and a photo approval step before a listing goes live; deployments ran through Azure.",
 ]),
 "lifesci": ("US life sciences company (cell therapy)", "Health Cloud and Experience Cloud", [
   "Built an LWC to show cell match results and pick vials for an order, plus reports and dashboards on donor and company data.",
   "Set up separate communities for CROs, logistics partners and hospitals, designed the data model and trigger framework, and scheduled daily data syncs.",
 ]),
 "wm": ("Waste Management (US)", "Customer alerts on Salesforce and Heroku", [
   "Built LWCs and triggers for customer alerts that sync to Heroku once published, and set up OAuth-based Apex REST services for the external apps.",
 ]),
 "bcbsm": ("Blue Cross Blue Shield of Massachusetts (BCBSMA)", "OmniStudio plan shopping website for members &mdash; live: <a href='https://www.bluecrossma.org/' color='#0b5cad'><u>bluecrossma.org</u></a>", [
   "Built the plan shopping website end to end on OmniStudio, where members compare and select medical insurance plans: OmniScripts drive the step-by-step journey and FlexCards show plan details and pricing.",
   "Used Integration Procedures and DataRaptors (Extract, Transform, Load) to pull plan, rate and eligibility data and save selections back to Salesforce, keeping logic declarative and reusable across screens.",
 ]),
}
ORDER = ["nissan", "asda", "scv", "amkor", "siemens", "bcbsm", "dubai", "emaar", "belden", "druce", "lifesci", "wm"]

s = [Paragraph("Akshay Kumar Verma", name_s),
     Paragraph(cfg.get("headline", HEADLINE), head_s), Spacer(1, 2),
     Paragraph("<a href='mailto:akshay.verma0031@gmail.com' color='#0b5cad'>akshay.verma0031@gmail.com</a> &nbsp;|&nbsp; +91 75664 42162 &nbsp;|&nbsp; "
               "<a href='https://www.linkedin.com/in/akshay-kumar-18565910a/' color='#0b5cad'>LinkedIn</a> &nbsp;|&nbsp; "
               "<a href='https://www.salesforce.com/trailblazer/akumar2783' color='#0b5cad'>Trailhead</a> &nbsp;|&nbsp; Remote from India, US hours", contact_s)]
_cs = ParagraphStyle("cs", parent=body, fontSize=9.8, leading=13, textColor=colors.HexColor("#0b2e59"))
_box = Table([["", Paragraph(cfg.get("callout", CALLOUT), _cs)]], colWidths=[0.07*inch, 7.23*inch])
_box.setStyle(TableStyle([("BACKGROUND", (0,0), (0,0), colors.HexColor("#0b5cad")), ("BACKGROUND", (1,0), (1,0), colors.HexColor("#e6f0fb")),
                          ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7), ("LEFTPADDING", (1,0), (1,0), 9),
                          ("RIGHTPADDING", (1,0), (1,0), 9), ("VALIGN", (0,0), (-1,-1), "MIDDLE")]))
s += [Spacer(1, 8), _box]
s += section("Summary"); s.append(Paragraph(hl(cfg.get("summary", SUMMARY)), body))
s += section("Salesforce + AI")
s += bullets(cfg.get("ai_points", AI_POINTS))
s += section("Skills")
for a, b in cfg.get("skills", SKILLS):
    s.append(Paragraph(f"<b>{a}:</b> " + hl(b), bul))
s += section("Certifications"); s.append(Paragraph(hl(CERTS), body))
s += section("Experience")
for t, c, d, l in ROLES:
    s.append(Paragraph(f"<b>{t}</b>, {c} &nbsp;&nbsp;<font color='#4d4d4d'>{d} | {l}</font>", body))
s += section("Projects")
for k in cfg.get("project_order", ORDER):
    client, what, items = PROJECTS[k]
    blk = [Paragraph(client, proj), Paragraph(what, sub)] + bullets(items[:1])
    s.append(KeepTogether(blk)); s += bullets(items[1:])
s += section("Education"); s.append(Paragraph("B.E., Information Technology, 2017", body))

if cfg.get("append_original", True):
    import orig_content as O
    ptitle = ParagraphStyle("pt", fontName="Helvetica-Bold", fontSize=9.6, leading=12.5, textColor=INK, spaceBefore=9, spaceAfter=3)
    s += section("Full Resume Details")
    s.append(Paragraph("<u>Skills Summary / Technical Highlights</u>", ptitle))
    s += bullets(O.SKILLS_SUMMARY)
    s.append(Paragraph("<u>Work Experience</u>", ptitle))
    s.append(Paragraph("7+ years of experience in IT industry working as Salesforce Consultant", body))
    s += bullets([f"{a} &nbsp;<font color='#4d4d4d'>({b})</font>" for a, b in O.WORK])
    s.append(KeepTogether([Paragraph("<u>Technical Skills</u>", ptitle), Paragraph(hl(O.TECH_SKILLS), body)]))
    cert_cell = [Paragraph("<b>" + c + "</b>", ParagraphStyle("cc", parent=body, fontSize=9, leading=11.5)) for c in O.CERTS]
    other = [Paragraph("<b>" + c + "</b>", ParagraphStyle("cc2", parent=body, fontSize=9, leading=11.5)) for c in
             ["Python for Everybody (University of Michigan, Coursera)", "Introduction to Data Science in Python (University of Michigan, Coursera)"]]
    t = Table([[Paragraph("<b>Salesforce certification</b>", ParagraphStyle("cl", parent=body, alignment=1)), cert_cell],
               [Paragraph("<b>Other certification</b>", ParagraphStyle("cl2", parent=body, alignment=1)), other]],
              colWidths=[1.6*inch, 5.1*inch])
    t.setStyle(TableStyle([("BOX", (0,0), (-1,-1), 0.8, INK), ("INNERGRID", (0,0), (-1,-1), 0.8, INK),
                           ("VALIGN", (0,0), (0,-1), "MIDDLE"), ("VALIGN", (1,0), (1,-1), "TOP"),
                           ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5), ("LEFTPADDING", (0,0), (-1,-1), 6)]))
    s.append(Spacer(1, 8)); s.append(t)
    for head, intro, items in O.PROJECTS:
        blk = [Paragraph("<u>" + head + "</u>", ptitle)]
        if intro: blk += [Paragraph(hl(intro), body), Spacer(1, 3)]
        s.append(KeepTogether(blk + bullets(items[:1]))); s += bullets(items[1:])

out = cfg.get("out", "Akshay_Verma_Resume.pdf")
SimpleDocTemplate(out, pagesize=letter, leftMargin=0.6*inch, rightMargin=0.6*inch, topMargin=0.45*inch, bottomMargin=0.45*inch,
                  title="Akshay Kumar Verma - Resume", author="Akshay Kumar Verma").build(s)
print(out)
