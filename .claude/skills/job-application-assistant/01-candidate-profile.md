---
framework_version: 1.0.0
---

# Candidate Profile

<!-- SETUP: This file is populated by running /setup -->
<!-- After running /setup, all sections will be filled with your actual information -->

## Identity
- **Name:** Adam Colyer
- **Location:** Edina, MN (resume header uses Twin Cities, MN)
- **Phone:** (612) 710-7700
- **Email:** adamcolyer@gmail.com
- **LinkedIn:** https://linkedin.com/in/colyeradam
- **GitHub:** https://github.com/AColyer13
- **Portfolio:** https://acolyer13.github.io/WebPortfolio/
- **Cursor:** https://cursor.com/@colyer
- **Languages:** English (native)
- **Status:** Actively job searching. Activus Connect contract ended June 2026. Building and shipping independent full-stack/AI projects while applying.
- **Application omit (temporary):** Do **not** list Mercor (AI Generalist Expert, Current) on CVs, cover letters, or applications until Adam says otherwise. Keep the entry in this profile for internal context only.
- **Constraints:** Open to relocation — Mississippi preferably Gulfport–Biloxi; Twin Cities office roles from Rogers, MN through Chanhassen, MN (west/NW/SW suburbs, including Garmin)
- **Canonical resume source:** `documents/cv/Adam_Colyer_Resume.pdf` (OneDrive mirror) and `documents/cv/adam_colyer_resume.txt` (text extract; Mercor omitted for applications)

## Education

| Degree | Period | Institution | Key Topics |
|--------|--------|-------------|------------|
| B.A. Marketing | 2019 | University of St. Thomas | Marketing, Business Analytics |

**Certifications:**
- Microsoft Azure AI-901 Engineer Program — in progress (training program; do not call this a Microsoft certification exam unless Adam is enrolled in a named exam such as AI-900 or AZ-*)
- Coding Temple — Full-Stack Software Engineering Certificate (Feb 2026)
- Harvard Business School Online — Certificate in Business Analytics (Feb 2021)

## Professional Experience

### AI Generalist Expert (Contract) - Mercor (2026 - Current) — OMIT FROM APPLICATIONS
Remote. <!-- Internal only until Adam re-enables. -->
- Contractor marketplace work evaluating and improving model outputs for a frontier AI lab under NDA.
- Frame as contract evaluation work. Never as a staff engineering, research scientist, or "AI lab employee" title.
- **Do not put on CVs/cover letters for now.**

### AI Search Quality Evaluator - Activus Connect (Tech Mahindra) (Nov 2025 - June 2026)
Remote. Tech Mahindra acquired Activus Connect in December 2021; listing the parent in parentheses is accurate. LinkedIn currently lists the employer as Tech Mahindra.
- Scored LLM search answers against Google quality rubrics; flagged hallucinations, unsupported claims, and weak sourcing
- Wrote structured error notes and pointed models to better sources when answers were wrong

### Account Executive - Citizen Observer (Sep 2022 - Aug 2024)
St. Paul, MN
- Sold the tip411 public-safety platform to cities, counties, and law enforcement agencies
- Ran 100+ live product demos and followed up with police chiefs, mayors, and city councils, including travel to San Diego and Dallas

### Sales Development Representative - Digital River (Jul 2021 - Jul 2022)
Minnetonka, MN
- Built and qualified an e-commerce pipeline that helped source and close a $500K+ Rec Room contract
- <!-- Prefer this "helped source and close" wording over "secured" on applications; it is the more interview-safe version. -->

### Account Executive - INRY (Oct 2020 - Jul 2021)
Eden Prairie, MN
- Owned a $500K+ ServiceNow pipeline with VP and director-level IT and HR buyers
- Submitted RFPs and walked buyers through responses
- <!-- Portfolio currently says $400,000+. Canonical figure for applications is $500K+ from the live resume. Update the portfolio to match. -->

### Business Development Representative - Epicor Software (May 2019 - Jun 2020)
St. Louis Park, MN
- Built an ERP pipeline with $1.1M accepted by AEs as qualified
- Earned an Excellence Award from the CEO for performance

## Independent Projects

Feature these four on CVs. Live URLs are the proof path. GitHub Source links on the portfolio currently 404 (private or renamed); do not send recruiters to those Source links until they resolve.

- **MissionCtrl** (https://missionctrl.org/) — Next.js 15, TypeScript, Python, Three.js, Gemini API, Docker, Playwright. Satellite mission simulator. Renders live satellite positions and orbital paths on a 3D globe from TLE data (CelesTrak) as the simulation clock advances. Gemini flight assistant (NOVA) answers questions about the current orbit, upcoming burns, and related mission topics. Playwright and unit tests run in GitHub Actions. Public site requires sign-in for the globe.
- **Valley Forge Automotive** (https://valleyforgeautomotive.org/) — React, TypeScript, Firebase Auth, Firestore, Tailwind CSS. Shop management: live service records, parts inventory, and scheduling. Role checks in Firestore block customers and staff from each other's records; admins can see all. Public brand on the live site is also **VF Tires**; optional parenthetical on longer CVs.
- **Legal Eagle** — Next.js, TypeScript, PostgreSQL, FastAPI. Practice tools for estate attorneys: client files, filing deadlines, per-case change log. Assistant answers from app data only after names and other sensitive info is stripped. **Do not link** https://legaleagleproject-mu.vercel.app/ until the production debug banner is gone (it currently exposes a Gmail allowlist and `ALLOW_DEV_MAGIC_IN_PROD`).
- **Stardust** — Next.js, React, Python, PostgreSQL, Docker, faster-whisper. Voice history / digital legacy. Records interviews, then transcribes and formats them for a local RAG index or custom model. Transcription runs faster-whisper on the host so audio never goes to a hosted API. No public URL on file.

Additional portfolio projects (do not feature on a one-page resume unless a posting specifically matches):
- **Writing Consultant** (Python, Flask): Python API backend, client talks to it over HTTPS. <!-- SETUP GAP: portfolio description is generic template text - ask user for real specifics before using in a CV/cover letter -->
- **Event Center Website** (HTML, CSS, JS, PWA). <!-- SETUP GAP: same as above -->
- **Dream Vacation App** (React, Vite, Hono, LangGraph, Mapbox). <!-- SETUP GAP: same as above -->
- **Swimming Website** (HTML, CSS, JS, PWA). <!-- SETUP GAP: same as above -->
- **The Office: Dunder Mifflin** (Node.js, Express, Three.js). <!-- SETUP GAP: same as above -->
- **Immaculate Draft** (HTML, CSS, JavaScript): Static baseball-themed site/game built with vanilla JS.
- **UFO Abductor** (Three.js, WebGL, Vite): Real-time 3D browser game.
- **Minnesota Snowmobile** (HTML, Canvas, JavaScript).

## Technical Skills

Resume-facing subset lives in `documents/cv/adam_colyer_resume.txt` (synced from OneDrive PDF; Mercor omitted). Broader inventory below. Prefer the resume subset on applications unless a posting names a skill that is honestly in this inventory.

**Resume positioning (from live PDF summary):** Full-stack engineer (TypeScript/React/Python); five years selling technical products to IT buyers and cities; secure authorization; structured data models so AI answers from user data while keeping sensitive info off cloud AI providers; DCI-P3 color, fluid animation, light/dark themes across viewports / system settings; daily Claude Code, Cursor, Antigravity; requirements gathering with technical and non-technical teams.

### Frontend
React, Next.js, React 19, TypeScript, JavaScript, Tailwind CSS, Vite, React Router, React Query / TanStack Query, Radix UI, Framer Motion, Three.js, Recharts, React Hook Form, Zustand

### Mobile
React Native, Expo & EAS, Flutter, Native iOS, Native Android, React Native Reanimated & Skia, Mobile Architecture, Mobile Release Engineering, Offline-First & Sync
<!-- SETUP NOTE: user confirmed these are genuine skills, though not yet reflected in any shipped project - keep in mind when a reviewer/interviewer probes for a concrete example -->

### Backend & APIs
Python (FastAPI, Flask), Node.js, Express, Hono, REST/GraphQL, OpenAPI/Swagger, Socket.IO, JWT, OAuth, Axios

### Databases & Data
PostgreSQL, Prisma ORM, SQLModel, DynamoDB, Amazon RDS, Firebase (Auth/Firestore), ChromaDB, Redis, DragonflyDB, Valkey, Zod, JSON exports

### AI & LLM Integration
Gemini API, Claude API, RAG pipelines (ChromaDB), faster-whisper voice transcription, LoRA fine-tuning, PEFT, Ollama, OpenAI/Anthropic, LangChain, LangGraph, Prompt Engineering, AI Agent Development, Local LLMs, MCP, Claude Code, Cursor, Antigravity, Grok, AI rate limiting

### Data Science & Analytics
Pandas, PyTorch, Statistical Rigor, Matplotlib, Seaborn

### DevOps, Testing & Cloud
Docker, docker-compose, GitHub Actions, CI/CD pipelines, Playwright, Jest, Vitest, pytest, Supertest, Mutation Testing, Property-Based Testing, Lighthouse CI, axe Accessibility, AWS, Google Cloud, Microsoft Azure, Cloud Run, Cloudflare, Render, Vercel, GitHub Pages, Firebase Hosting

### Security & Architecture
Privacy-first design, PIN + JWT auth, bcrypt, OWASP practices, rate limiting, consent/ethics layers, provenance tracking, NextAuth/Auth.js, passwordless auth, Google Auth, Firestore rules, RBAC, Secrets & IAM, Helmet & CSP, Presidio, AI redaction (names and other sensitive info stripped before model calls)

### Observability
Sentry, OpenTelemetry, Pino logging

## Public artifacts
- Proof path for applications: portfolio + missionctrl.org + valleyforgeautomotive.org + cursor.com/@colyer
- Do not put GitHub in the resume header until featured repos are public and pinned
- Portfolio stack lines and "Activus = Present" are stale relative to the live resume; align before sending recruiters there as the only proof

## Publications
None.

## Awards
- Excellence Award - Epicor Software (2020), for consistent performance

## References
<!-- SETUP GAP: ask user for references -->
- [NAME], [TITLE], [COMPANY] ([EMAIL], [PHONE])

More references available upon request.
