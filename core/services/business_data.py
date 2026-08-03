import csv
from io import TextIOWrapper

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import BusinessDataRecord, BusinessDataSource


def _clean_header(value, position):
    header = str(value or '').strip()
    return header[:120] or f'coluna_{position}'


def _csv_rows(uploaded):
    uploaded.seek(0)
    wrapper = TextIOWrapper(uploaded.file, encoding='utf-8-sig', newline='')
    try:
        sample = wrapper.read(4096)
        wrapper.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        yield from csv.reader(wrapper, dialect)
    finally:
        wrapper.detach()


def _xlsx_rows(uploaded):
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise ValidationError('O suporte a Excel não está instalado no servidor.') from error
    uploaded.seek(0)
    workbook = load_workbook(uploaded, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        for row in sheet.iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def read_spreadsheet(uploaded):
    suffix = uploaded.name.rsplit('.', 1)[-1].lower()
    iterator = _xlsx_rows(uploaded) if suffix == 'xlsx' else _csv_rows(uploaded)
    rows = iter(iterator)
    try:
        raw_headers = next(rows)
    except StopIteration as error:
        raise ValidationError('A planilha está vazia.') from error
    headers = [_clean_header(value, index + 1) for index, value in enumerate(raw_headers)]
    if len(headers) != len(set(item.casefold() for item in headers)):
        raise ValidationError('A planilha possui nomes de colunas repetidos.')
    records = []
    for row_number, values in enumerate(rows, start=2):
        normalized = [('' if value is None else str(value).strip()) for value in values[:len(headers)]]
        normalized += [''] * (len(headers) - len(normalized))
        if any(normalized):
            records.append((row_number, dict(zip(headers, normalized))))
        if len(records) > 10000:
            raise ValidationError('A planilha excede o limite de 10.000 registros.')
    if not records:
        raise ValidationError('A planilha não possui registros preenchidos.')
    return headers, records


@transaction.atomic
def import_business_data(*, empresa, user, name, data_type, uploaded, visible_columns, replace_existing):
    headers, rows = read_spreadsheet(uploaded)
    canonical = {item.casefold(): item for item in headers}
    unknown = [item for item in visible_columns if item.casefold() not in canonical]
    if unknown:
        raise ValidationError(f'Colunas não encontradas: {", ".join(unknown)}.')
    allowed = [canonical[item.casefold()] for item in visible_columns]
    existing = BusinessDataSource.objects.filter(empresa=empresa, name__iexact=name).first()
    if existing and not replace_existing:
        raise ValidationError('Já existe uma base com esse nome. Marque a opção de substituição.')
    if existing:
        existing.delete()
    source = BusinessDataSource.objects.create(
        empresa=empresa, name=name.strip(), data_type=data_type,
        source_filename=uploaded.name[:255], columns=headers,
        ai_visible_columns=allowed, row_count=len(rows), imported_by=user,
    )
    BusinessDataRecord.objects.bulk_create([
        BusinessDataRecord(
            empresa=empresa, source=source, row_number=row_number, data=data,
            searchable_text=' '.join(str(data.get(column, '')) for column in allowed).casefold()[:8000],
        )
        for row_number, data in rows
    ], batch_size=500)
    return source
