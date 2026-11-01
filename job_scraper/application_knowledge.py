"""Evidence-backed answers; absence of a fact is never treated as a negative answer."""
import re


def known_answer(field, profile):
    label = field.label or ""
    text = label.lower()
    options = list(field.options)
    value = None
    evidence = "candidate profile"
    if re.search(r"\bnot\b|\bnever\b|\bunless\b|\bexcept\b|\bno\b|n't", text):
        return None
    if re.fullmatch(r"are you currently (based|located) (in|on) (the )?(us |u\.s\. )?east coast\s*[?* ]*", text):
        if profile.get("state") == "Minnesota":
            value = "No"
    elif re.fullmatch(r"have you (ever )?(worked with|used) ai coding (agents|tools|assistants)\s*[?* ]*", text):
        value = "Yes"
        evidence = "CLAUDE.md: daily Claude Code, Cursor and Antigravity"
    elif re.search(r"inference|kv cache|tokenomics", text) and field.kind in ("text", "textarea"):
        value = ("My hands-on work includes integrating LLM APIs, building RAG and agent workflows, "
                 "and running local models with Ollama. In Stardust, I use local faster-whisper transcription "
                 "and a local RAG pipeline. I consider model choice, context size, latency, token usage and "
                 "cost when designing applications. I have not documented direct production work tuning "
                 "KV caches or building an inference-serving engine; my experience is at the application "
                 "and integration layer.")
        evidence = "candidate profile: AI/LLM skills and Stardust; infrastructure gap stated explicitly"
    elif re.search(r"(what|which).{0,25}country.{0,30}(residence|work from|working from)|what country are you planning to work from", text):
        value = profile.get("country")
    elif re.search(r"most recent/current employer", text):
        value = profile.get("current_company")
    elif re.search(r"how many years experience.{0,35}(this role|similar roles)", text):
        if field.kind in ("text", "textarea"):
            value = "1 year of independent full-stack and AI project work; I have not held a paid software engineering title."
    elif re.search(r"applicant privacy policy", text) and options == ["Acknowledge/Confirm"]:
        value = options[0]  # acknowledge the notice, not unrelated recording/marketing consent
    if value is None:
        return None
    if options:
        match = next((o for o in options if o.casefold() == str(value).casefold()), None)
        if match is None and value == profile.get('country') and field.kind == 'combobox':
            return value, evidence  # searchable country catalogs are only partially enumerated
        if match is None:
            return None
        value = match
    return value, evidence


def question_kind(field):
    text = field.label or ""
    if not text or re.fullmatch(r"section_\d+_question_\d+|field-\d+|Search|Select|(?=.*[\d_-])[A-Za-z0-9_-]{10,12}", text):
        return "form_reader_gap"
    if re.search(r"citizen|residen[ct] status|agreement|non.compete|adjustments|accommodation|consent.*transcri|transcrib|business hours|start date year|end date year", text, re.I):
        return "missing_candidate_fact_or_preference"
    return "unresolved_question"
