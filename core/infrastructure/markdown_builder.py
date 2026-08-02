class MarkdownBuilder:
    """Monta Markdown previsível sem acoplar o compilador à apresentação."""

    def __init__(self):
        self._parts = []

    def section(self, title, content):
        value = str(content or '').strip()
        self._parts.append(f'# {title}\n{value or "Não informado."}')
        return self

    def build(self):
        return '\n\n'.join(self._parts).strip() + '\n'

