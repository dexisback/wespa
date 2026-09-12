QUERY_ENTITY_SYSTEM = (
    "You extract entity names from a natural-language question. Output strictly valid JSON only."
)

QUERY_ENTITY_TMPL = """List the specific entities (companies, people, products, organizations, monetary values) this question is about. Only names that could exist in a tech-news knowledge graph.

Question: {question}

Return JSON exactly like: {{"entities": ["OpenAI", "Ilya Sutskever"]}}"""

ANSWER_SYSTEM = (
    "You are a memory-grounded answer engine. You answer ONLY from the evidence provided. "
    "Never invent facts. If the evidence is insufficient or contradictory, say so explicitly "
    "and express uncertainty. Prefer facts whose valid_to is empty (currently true) unless the "
    "question asks about the past. Cite source names inline like (Source: TechCrunch). "
    "Be concise — 2 to 4 sentences."
)

ANSWER_TMPL = """Question: {question}

Knowledge-graph facts (subject -> relation -> object, with source, confidence, and validity window):
{facts}

Retrieved passages from trusted sources:
{passages}

{mode_note}

Write a grounded answer to the question using only the evidence above.
If the evidence does not fully answer it, state what is known and what is uncertain.
Name the sources you rely on."""

MODE_NOTES = {
    "hybrid": "Evidence combines graph-traversed facts and semantically retrieved passages.",
    "graph": "Evidence comes exclusively from knowledge-graph traversal — reason over the connected facts.",
    "vector": "Evidence comes exclusively from semantically similar passages — there is no graph traversal.",
}


def build_answer_prompt(question: str, facts, passages, mode: str, as_of: str | None = None) -> list[dict]:
    fact_lines = []
    for f in facts:
        validity = "currently true" if f.active else f"held {f.valid_from} -> {f.valid_to}"
        flag = " [CONFLICTING CLAIM]" if f.conflict else ""
        fact_lines.append(
            f"- {f.subject_name or f.subject_id} -[{f.relation}]-> {f.object_name or f.object_id} "
            f"(source: {f.source_name or f.source_id}, confidence: {f.confidence:.2f}, {validity}){flag}"
        )
    passage_lines = []
    for p in passages:
        passage_lines.append(f"- [{p.source}] ({p.published_at or 'n/a'}): {p.text[:500]}")
    if not fact_lines:
        fact_lines = ["(none)"]
    if not passage_lines:
        passage_lines = ["(none)"]
    as_of_note = ""
    if as_of:
        as_of_note = (
            f"\nIMPORTANT — HISTORICAL RECONSTRUCTION: the user asks what memory believed AS OF {as_of[:10]}. "
            "Only facts whose validity window covers that date were supplied. Answer in past tense about what was known then, "
            "and do not use facts that became known later.\n"
        )
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {
            "role": "user",
            "content": ANSWER_TMPL.format(
                question=question,
                facts="\n".join(fact_lines),
                passages="\n".join(passage_lines),
                mode_note=MODE_NOTES.get(mode, "") + as_of_note,
            ),
        },
    ]
