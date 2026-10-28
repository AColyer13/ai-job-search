# CV Tailoring Guide

The canonical resume is `documents/cv/resume.md`. Every claim in a tailored CV comes from it and from `01-candidate-profile.md`. The file that gets uploaded is the PDF in `C:\Users\adamc\OneDrive\Resumes\`; `CLAUDE.md` names the current one.

## Section-by-Section Tailoring

### Profile Statement
The most important section to customize. Write 3-4 lines that say why Adam is qualified for *this specific role* and what the employer gains.

When the role sits outside his home domain, **lead with the domain-transfer argument** (sales background to engineering) in the opening sentence. Keep that framing honest per `04-job-evaluation.md`'s Experience Match notes: transferable skills, never an implied engineering or PM title he did not hold.

**For entry-level / full-stack software engineering roles:**
> Full-stack engineer building TypeScript, React, and Python apps. Sold technical products to IT buyers and cities for five years. Builds secure authorization and structured data models so AI can answer from user data while keeping sensitive information off cloud AI providers. Styles products with DCI-P3 color, fluid animation, and light/dark themes across viewports. Uses Claude Code, Cursor, and Antigravity daily; gathers requirements from technical and non-technical teams.

**For AI/ML-engineering-focused roles:**
> Full-stack engineer focused on privacy-aware AI features: RAG over user data, local faster-whisper transcription, and Claude/Gemini-backed assistants, built with React/Next.js and Python. Scored LLM search answers against Google quality rubrics at Activus Connect (Tech Mahindra), then applied the same failure-mode eye to MissionCtrl (live globe + NOVA) and Legal Eagle (sensitive info stripped before the model sees app data).

These are phrasing references, never fact sources: every factual claim still comes from `01-candidate-profile.md`.

### Skills
Reorder and emphasize based on the role. Use bold category labels, 5-7 items, each 1-2 lines. Use the posting's own core term in the matching label when it truthfully applies. ATS and skim-reading hiring managers match literally.

### Education
Include the degree and certificates. Keep it to dates and titles.

### Professional Experience
- Rewrite bullets to emphasize what is most relevant to the target role.
- 4-5 bullets for the most recent role, 2-3 for the previous, 2 (one line each) for older ones.
- Emphasize measurable results where they exist. Never invent a number.

### Evidence Links
Where the CV names a verifiable artifact (MissionCtrl, Valley Forge Automotive, the portfolio), carry its link so a reader can verify the claim in one click.

### Awards
One line each.

## Page Budget: Hard 2-Page Limit
The resume is exactly 2 pages. If in doubt, cut rather than squeeze; shrinking margins or spacing to force-fit makes it look cramped. A CV that ends early on page 2 looks incomplete, so restore the highest-relevance item that was cut.

## Cutting by Relevance
Cut by signal, not by section. An older-role bullet that speaks directly to the posting is worth more than a recent-role bullet that does not. For each candidate line, ask whether it hits a named tool, keyword, or stated responsibility in the posting, and whether it is the only place the claim appears. Cut the lowest-scoring line first, in this order:

1. Redundancy between Skills and a role bullet (keep the more concrete bullet).
2. Profile-statement fluff that restates what Skills or Experience already show.
3. Low-relevance experience bullets, wherever they sit.
4. Low-relevance supporting content (a certificate outside the posting's stack, a language entry that can shrink to one line).
5. Last resort: the oldest education entry, or an older role down to 2 bullets.

Do not mechanically cut from the bottom of the section list. "Cut the oldest role first" is wrong if that role is literally about the skill the posting asks for.

## ATS Parseability
ATS parsers read the PDF's embedded text layer, not the rendered page. After the PDF is built, check it:

```bash
pdftotext -layout Adam_Colyer_Resume_YYYY-MM.pdf -
```

`pdftotext` (poppler) is optional. If it is missing, skip the mechanical check and rely on reading the PDF.

- **Contact details as literal text.** The email and phone must appear as text, not only as an icon or a hyperlink.
- **No garbled output.** `(cid:NNN)` markers or `�` characters mean a font has no Unicode mapping.
- **Reading order.** A single-column layout extracts in visual order; sidebars and columns can interleave unrelated lines.
- **Keyword coverage.** Match the posting's required and preferred terms against the extracted text. Prefer the posting's exact term over a synonym when it is truthfully applicable. Never add a keyword the profile does not support, and leave genuine gaps visible.

## Section Order
**Technical / AI / full-stack roles:** profile statement, skills, professional experience, education, awards.

**Roles where credentials are the main qualifier:** profile statement, skills, education, professional experience, awards.
