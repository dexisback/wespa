import re
from bs4 import BeautifulSoup


def clean_text(raw: str) -> str:
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "html.parser").get_text(separator=" ")
    raw = raw.replace("\xa0", " ")
    raw = re.sub(r"https?://\S+", "", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def clean_document_text(text: str) -> str:
    return clean_text(text)
