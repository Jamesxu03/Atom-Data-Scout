"""Does a page really say what the model claims? Shared by contacts and research: every email,
phone, name or quote the model reports is matched against the page it cites."""

import html as html_lib
import re
import unicodedata

TEL_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)
PHONE_IN_TEXT = re.compile(r"\+?\d[\d\s().-]{5,}\d")
MIN_QUOTE_WORDS = 4  # shorter quotes ("we sell data") match too many pages to prove anything
# Price and age quotes must also contain the value's numbers, so a short one still proves it
# ("Founded in 2016", "£500 per hour")
MIN_FACT_QUOTE_WORDS = 2


def strip_code(html: str) -> str:
    return re.sub(r"<(script|style|noscript)\b.*?</\1\s*>", " ", html, flags=re.I | re.S)


def visible_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", strip_code(html)))


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _same_phone(a: str, b: str) -> bool:
    """Same number, allowing for country-code / trunk-prefix formatting differences."""
    da, db = digits(a), digits(b)
    return len(da) >= 7 and len(db) >= 7 and da[-9:] == db[-9:]


def email_on_page(email: str, html: str) -> bool:
    """Whole-address match: an@acme.com does not match inside jean@acme.com."""
    pattern = r"(?<![\w.%+-])" + re.escape(email) + r"(?![\w-]|\.[a-z0-9])"
    return re.search(pattern, strip_code(html), re.I) is not None


def phone_on_page(phone: str, html: str) -> bool:
    """Only in tel: links or visible text — never digits glued together from scripts or IDs."""
    candidates = TEL_RE.findall(html) + PHONE_IN_TEXT.findall(visible_text(html))
    return any(_same_phone(phone, c) for c in candidates)


def name_on_page(name: str, html: str) -> bool:
    pattern = r"\b" + r"\s+".join(map(re.escape, name.split())) + r"\b"
    return re.search(pattern, visible_text(html), re.I) is not None


# NFKC already turns non-breaking spaces into spaces. Curly apostrophes and dashes are made
# plain; double quotes are dropped, since the model often wraps quotes in its own.
_PUNCTUATION = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": None, "\u201d": None, '"': None,
                              "\u2013": "-", "\u2014": "-"})


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION).lower()
    return re.sub(r"\s+", " ", text).strip(" .'\u2026")


def quote_in_text(quote: str, text: str, min_words: int = MIN_QUOTE_WORDS) -> bool:
    """The quote appears in plain text, ignoring case, spacing, double quotes and curly
    apostrophes. Quotes of fewer than min_words words never count."""
    q = _normalise(quote)
    if len(q.split()) < min_words:
        return False
    text = _normalise(text)
    # the model often joins two passages with "..." — each part must be on the page
    return all(part in text for part in re.split(r"\s*(?:\.\.\.|\u2026)\s*", q) if part)



def quote_on_page(quote: str, html: str, min_words: int = MIN_QUOTE_WORDS) -> bool:
    return quote_in_text(quote, html_lib.unescape(visible_text(html)), min_words)


def _numbers(s: str) -> set[str]:
    """'$1,250.50 from 2018' -> {'125050', '2018'}: thousands separators and decimals ignored."""
    return {re.sub(r"[.,]", "", n) for n in re.findall(r"\d(?:[\d.,]*\d)?", s)}


def value_in_quote(value: str, quote: str) -> bool:
    """Every number in the value (a price, a year) is also in the quote, so the quote backs the
    value rather than just sitting on the same page."""
    return _numbers(value) <= _numbers(quote)
