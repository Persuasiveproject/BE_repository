from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li"}
DROPPED_WITH_CONTENT = {"script", "style"}
LINE_BREAK_TAGS = {"p", "br", "li"}


class _Formatter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html, self.plain, self.open, self.skipping = [], [], [], 0

    def line_break(self):
        if self.plain and not self.plain[-1].endswith("\n"):
            self.plain.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag in DROPPED_WITH_CONTENT:
            self.skipping += 1
            return
        if self.skipping or tag not in ALLOWED_TAGS:
            return
        if tag in LINE_BREAK_TAGS:
            self.line_break()
        self.html.append(f"<{tag}>")
        if tag != "br":
            self.open.append(tag)

    def handle_endtag(self, tag):
        if tag in DROPPED_WITH_CONTENT:
            self.skipping = max(self.skipping - 1, 0)
            return
        if self.skipping or tag not in self.open:
            return
        # Close anything still open inside this tag so the output always nests.
        while (closed := self.open.pop()) != tag:
            self.html.append(f"</{closed}>")
        self.html.append(f"</{tag}>")
        if tag in LINE_BREAK_TAGS:
            self.line_break()

    def handle_data(self, data):
        if not self.skipping:
            self.html.append(escape(data, quote=False))
            self.plain.append(data)


def format_content(raw):
    """Return (content_html, content_plain) keeping only ALLOWED_TAGS without attributes."""
    parser = _Formatter()
    parser.feed(raw)
    parser.close()
    parser.html.extend(f"</{tag}>" for tag in reversed(parser.open))
    return "".join(parser.html), "".join(parser.plain).strip()
