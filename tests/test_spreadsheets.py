import pytest
import httpx
import io
from contextlib import closing
from openpyxl import Workbook

from mail_merger.spreadsheets import GoogleSpreadsheet, LocalSpreadsheet, make_recipients, normalized_rows, sheet_id
from mail_merger.spreadsheets import displayed_value
from datetime import datetime


def test_csv_bom_zeroes_and_quoted_newlines(tmp_path):
    path = tmp_path / 'people.csv'
    path.write_text('\ufeffEmail,Code,Name\none@example.com,00042,"Ada\nLovelace"\n', encoding='utf-8')
    headers, rows = LocalSpreadsheet().read(str(path), 'CSV')
    assert headers == ['Email', 'Code', 'Name']
    assert rows[0]['Code'] == '00042'
    assert rows[0]['Name'] == 'Ada\nLovelace'


def test_excel_worksheet_selection_and_zero_format(tmp_path):
    path = tmp_path / 'people.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = 'Contacts'
    sheet.append(['Email', 'Code'])
    sheet.append(['one@example.com', 42])
    sheet['B2'].number_format = '00000'
    book.create_sheet('Other')
    book.save(path)
    importer = LocalSpreadsheet()
    assert importer.worksheets(str(path)) == ['Contacts', 'Other']
    assert importer.read(str(path), 'Contacts')[1][0]['Code'] == '00042'


@pytest.mark.parametrize('rows', [[['Email', ''], ['a', 'b']], [[' Name ', 'Name'], ['a', 'b']],
                                 [['Email'], ['a', 'missing header']], [['Email']]])
def test_bad_headers_and_empty_sheet(rows):
    with pytest.raises(ValueError):
        normalized_rows(rows)


def test_limit_and_missing_trailing_values():
    assert normalized_rows([['Email', 'Name'], ['one@example.com']])[1][0]['Name'] == ''
    with pytest.raises(ValueError, match='1,000'):
        normalized_rows([['Email']] + [[f'user{i}@example.com'] for i in range(1001)])


def test_duplicate_and_invalid_rows_initially_unchecked():
    rows = [{'Email': value} for value in ['one@example.com', 'one@example.com', 'invalid', 'two@example.com']]
    assert [r.selected for r in make_recipients(rows, 'Email')] == [True, False, False, True]


def test_google_url_validation():
    assert sheet_id('https://docs.google.com/spreadsheets/d/abc-123/edit#gid=4') == 'abc-123'
    with pytest.raises(ValueError):
        sheet_id('https://evil.example/spreadsheets/d/abc/edit')


@pytest.mark.parametrize('kind', ['csv', 'xlsx', 'google'])
def test_empty_spacer_columns_before_sparse_alternate_email_are_ignored(tmp_path, monkeypatch, kind):
    # The user's layout: primary email in G, blank H/I, alternate email in J.
    headers = ['S. No','Subject code','Subject name','Name','Employee code','Phone','Email',None,None,'Alter',None]
    rows = [headers,
            [1,'PE001','DA','Ada','0007','0123456789','one@example.com',None,None,'alt@example.com'],
            [2,'PE002','EE','Lin','0008','0123456788','two@example.com'],
            [None] * 11]
    if kind == 'csv':
        import csv
        path = tmp_path / 'people.csv'
        with path.open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerows(rows)
        imported_headers, records = LocalSpreadsheet().read(str(path), 'CSV')
    else:
        book = Workbook()
        for row in rows:
            book.active.append(row)
        book.active['N20'].number_format = '0.00'  # Formatting isn't data.
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        if kind == 'google':
            monkeypatch.setattr(httpx, 'stream', lambda *a, **kw: closing(httpx.Response(200, content=stream.getvalue())))
            imported_headers, records = GoogleSpreadsheet().read('https://docs.google.com/spreadsheets/d/example/edit', 'Sheet')
        else:
            path = tmp_path / 'people.xlsx'
            path.write_bytes(stream.getvalue())
            imported_headers, records = LocalSpreadsheet().read(str(path), 'Sheet')
    assert imported_headers == [header for header in headers if header]
    assert len(records) == 2
    assert records[0]['Email'] == 'one@example.com'
    assert records[0]['Alter'] == 'alt@example.com'
    assert records[1]['Email'] == 'two@example.com' and records[1]['Alter'] == ''
    assert records[0]['Employee code'] == '0007'


def test_unnamed_column_with_late_data_identifies_the_actual_excel_cell():
    rows = [['Email',None,'Alter'], ['one@example.com',None,''], [], ['two@example.com','unexpected','']]
    with pytest.raises(ValueError, match='Column B has data at B4.*B1'):
        normalized_rows(rows)


def test_leading_spacers_are_ignored_without_shifting_values():
    headers, records = normalized_rows([[None,'Email',None,'Code'],[None,'one@example.com',None,'0001']])
    assert headers == ['Email','Code']
    assert records == [{'Email':'one@example.com','Code':'0001'}]


@pytest.mark.parametrize('value,fmt,expected', [
    (datetime(2026, 11, 1), 'dd/mm/yyyy', '01/11/2026'),
    (datetime(2026, 11, 1, 14, 5), 'm/d/yy h:mm AM/PM', '11/1/26 2:05 PM'),
    (datetime(2026, 11, 1), 'mmm d, yyyy', 'Nov 1, 2026'),
    (42, '00000', '00042'), (-42, '00000', '-00042'),
    (1234.5, '#,##0.00', '1,234.50'), (0.125, '0.0%', '12.5%'),
])
def test_common_excel_display_formats(value, fmt, expected):
    assert displayed_value(value, fmt) == expected


def shared_workbook():
    book = Workbook()
    book.active.title = 'Other'
    sheet = book.create_sheet("People's contacts")
    sheet.append(['Email', 'Name', 'Code', 'Date'])
    # Gaps in a worksheet must not truncate the nonempty recipient records.
    sheet.cell(1102, 1, 'last@example.com')
    sheet.cell(1102, 2, 'Last person')
    sheet.cell(1102, 3, 42).number_format = '00000'
    sheet.cell(1102, 4, datetime(2026, 11, 1)).number_format = 'dd/mm/yyyy'
    # Google exports styled, empty columns beyond the recipient table.
    sheet.cell(1, 22).number_format = '0.00'
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def test_google_shared_link_has_worksheets_and_formatted_values_without_auth(monkeypatch):
    content = shared_workbook()
    responses = []
    def download(method, url, **kwargs):
        assert method == 'GET'
        assert url == 'https://docs.google.com/spreadsheets/d/example/export'
        assert kwargs['params'] == {'format': 'xlsx', 'resourcekey': '0-key'}
        assert 'headers' not in kwargs and 'auth' not in kwargs and 'cookies' not in kwargs
        response = httpx.Response(200, content=content)
        responses.append(response)
        return closing(response)
    monkeypatch.setattr(httpx, 'stream', download)
    source = 'https://docs.google.com/spreadsheets/d/example/edit?usp=sharing&resourcekey=0-key#gid=42'
    importer = GoogleSpreadsheet()
    assert importer.worksheets(source) == ['Other', "People's contacts"]
    headers, rows = importer.read(source, "People's contacts")
    assert headers == ['Email', 'Name', 'Code', 'Date']
    assert rows == [{'Email': 'last@example.com', 'Name': 'Last person', 'Code': '00042', 'Date': '01/11/2026'}]
    assert all(response.is_closed for response in responses)
    with pytest.raises(ValueError, match='Load worksheets again'):
        importer.read(source, 'Deleted')


@pytest.mark.parametrize('status,content,headers', [
    (401, b'', {}), (403, b'', {}), (404, b'', {}),
    (200, b'<html>Sign in</html>', {'content-type': 'text/html; charset=utf-8'}),
    (200, b'<html>Access denied</html>', {}),
])
def test_restricted_or_download_disabled_sheet_gives_sharing_instructions(monkeypatch, status, content, headers):
    monkeypatch.setattr(httpx, 'stream', lambda *a, **kw: closing(httpx.Response(status, content=content, headers=headers)))
    with pytest.raises(ValueError, match='Anyone with the link.*Viewer'):
        GoogleSpreadsheet().worksheets('https://docs.google.com/spreadsheets/d/private/edit')


def test_shared_sheet_download_is_bounded_and_closes_response(monkeypatch):
    class LargeStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'PK\x03\x04' + b'x' * 10_000_000
            yield b'x' * 10_000_000
            raise AssertionError('Importer kept downloading after exceeding its limit')
    response = httpx.Response(200, stream=LargeStream())
    monkeypatch.setattr(httpx, 'stream', lambda *a, **kw: closing(response))
    with pytest.raises(ValueError, match='20 MB'):
        GoogleSpreadsheet().worksheets('https://docs.google.com/spreadsheets/d/example/edit')
    assert response.is_closed


def test_shared_sheet_network_failure_and_bad_workbook(monkeypatch):
    def offline(*args, **kwargs):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx, 'stream', offline)
    with pytest.raises(ValueError, match='Check your connection'):
        GoogleSpreadsheet().worksheets('https://docs.google.com/spreadsheets/d/example/edit')
    monkeypatch.setattr(httpx, 'stream', lambda *a, **kw: closing(httpx.Response(200, content=b'PK\x03\x04broken')))
    with pytest.raises(ValueError, match='unreadable workbook'):
        GoogleSpreadsheet().worksheets('https://docs.google.com/spreadsheets/d/example/edit')


def test_shared_sheet_nonempty_column_without_header_still_fails(monkeypatch):
    book = Workbook()
    book.active.append(['Email', None])
    book.active.append(['one@example.com', 'Missing header'])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    monkeypatch.setattr(httpx, 'stream', lambda *a, **kw: closing(httpx.Response(200, content=stream.getvalue())))
    with pytest.raises(ValueError, match='missing its header'):
        GoogleSpreadsheet().read('https://docs.google.com/spreadsheets/d/example/edit', 'Sheet')


def test_shared_sheet_refresh_invalidates_review_without_contacting_auth(application, monkeypatch):
    content = shared_workbook()
    monkeypatch.setattr(httpx, 'stream', lambda *a, **kw: closing(httpx.Response(200, content=content)))
    def no_oauth(*args, **kwargs):
        raise AssertionError('Shared sheet must not request Google OAuth')
    monkeypatch.setattr(application.auth, 'token', no_oauth)
    application.campaign.approved_revision = application.campaign.revision
    application.review_revision = application.campaign.revision
    assert application.discover('https://docs.google.com/spreadsheets/d/example/edit', 'google') == ['Other', "People's contacts"]
    application.stage_rows("People's contacts")
    application.build_recipients('Email', 'Email')
    assert application.campaign.approved_revision is None
    assert application.review_revision is None
    assert application.campaign.recipients[0].values['Code'] == '00042'
    assert application.store.load(application.campaign.id).recipients[0].values['Email'] == 'last@example.com'
