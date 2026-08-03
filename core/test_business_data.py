from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from core.models import Atendimento, BusinessDataRecord, BusinessDataSource, EmpresaCliente
from core.services.ai.tools import AIToolExecutor


class BusinessDataTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='empresa-a', password='senha-segura')
        self.other_user = User.objects.create_user(username='empresa-b', password='senha-segura')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Loja A')
        self.other_company = EmpresaCliente.objects.create(usuario=self.other_user, nome='Loja B')
        self.client.force_login(self.user)

    def test_csv_import_exposes_only_authorized_columns(self):
        upload = SimpleUploadedFile(
            'catalogo.csv', 'produto;preço;estoque;custo interno\nTênis Azul;249,90;8;100\n'.encode(),
            content_type='text/csv',
        )
        response = self.client.post(reverse('dados_negocio'), {
            'name': 'Catálogo atual', 'data_type': 'PRODUCT', 'spreadsheet': upload,
            'ai_visible_columns': 'produto, preço, estoque', 'replace_existing': 'on',
        })
        self.assertRedirects(response, reverse('dados_negocio'))
        source = BusinessDataSource.objects.get(empresa=self.company)
        record = source.records.get()
        self.assertEqual(source.row_count, 1)
        self.assertEqual(record.visible_data, {'produto': 'Tênis Azul', 'preço': '249,90', 'estoque': '8'})
        self.assertNotIn('100', record.searchable_text)

    def test_xlsx_import_and_ai_search_are_tenant_scoped(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['produto', 'preço'])
        sheet.append(['Café Premium', '35.00'])
        content = BytesIO()
        workbook.save(content)
        upload = SimpleUploadedFile('produtos.xlsx', content.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.client.post(reverse('dados_negocio'), {
            'name': 'Produtos', 'data_type': 'PRODUCT', 'spreadsheet': upload,
            'ai_visible_columns': 'produto, preço', 'replace_existing': 'on',
        })
        other_source = BusinessDataSource.objects.create(
            empresa=self.other_company, name='Privado', data_type='PRODUCT', source_filename='x.csv',
            columns=['produto', 'preço'], ai_visible_columns=['produto', 'preço'], row_count=1,
        )
        BusinessDataRecord.objects.create(
            empresa=self.other_company, source=other_source, row_number=2,
            data={'produto': 'Segredo', 'preço': '999'}, searchable_text='segredo 999',
        )
        attendance = Atendimento.objects.create(empresa=self.company, nome_cliente='Cliente', telefone_cliente='5511999999999')
        executor = AIToolExecutor(atendimento=attendance)
        result = executor.execute('pesquisar_dados_negocio', {'consulta': 'Café Premium'})
        self.assertEqual(result['resultados'][0]['dados']['preço'], '35.00')
        self.assertEqual(executor.execute('pesquisar_dados_negocio', {'consulta': 'Segredo'})['resultados'], [])

    def test_company_cannot_toggle_another_company_source(self):
        source = BusinessDataSource.objects.create(
            empresa=self.other_company, name='Privado', data_type='OTHER', source_filename='x.csv',
        )
        response = self.client.post(reverse('dados_negocio_status', args=[source.pk]))
        self.assertEqual(response.status_code, 404)
        source.refresh_from_db()
        self.assertTrue(source.is_active)
