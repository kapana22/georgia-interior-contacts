"""Read the supplied company export without changing it; preserve all source cells."""
import argparse
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
FIELDS = ['source_id', 'name', 'legal_status', 'legal_status_full', 'tax_id', 'country',
          'city', 'address', 'phone', 'fax', 'email', 'website', 'role',
          'registered_at', 'contact_people', 'active_tender']
HEADERS = ['ID', 'სრული სახელი', 'იურ. სტატუსი', 'სრული იურ. სტატუსი', 'ID კოდი',
           'ქვეყანა', 'ქალაქი', 'მისამართი', 'ტელეფონი', 'ფაქსი', 'ელ-ფოსტა',
           'ვებგვერდი', 'როლი', 'რეგისტრაცია', 'საკონტ. პირები', 'აქტიური ტენდერი']


def read_export(path):
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in si.iterfind('.//s:t', NS))
                       for si in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        rows = ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('s:sheetData/s:row', NS)
        records, headers = [], None
        for row in rows:
            cells = [''] * 16
            for cell in row:
                letters = re.match(r'[A-Z]+', cell.attrib['r'])[0]
                column = 0
                for char in letters:
                    column = column * 26 + ord(char) - 64
                if column > 16:
                    raise ValueError('Unexpected column: ' + letters)
                value = cell.find('s:v', NS)
                text = value.text if value is not None else ''
                if cell.get('t') == 's':
                    text = strings[int(text)]
                elif cell.get('t') == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.iterfind('.//s:t', NS))
                cells[column - 1] = text or ''
            if cells == HEADERS:
                headers = cells
                continue
            if headers is None or not any(cells):
                continue
            if not cells[0] or not cells[1]:
                raise ValueError('Missing ID/name at row ' + row.get('r', '?'))
            record = dict(zip(FIELDS, cells))
            record.update(id='export:' + cells[0], source=path.name, source_row=int(row.get('r')))
            records.append(record)
        if headers is None:
            raise ValueError('Expected company headers not found')
        if len({r['id'] for r in records}) != len(records):
            raise ValueError('Duplicate source IDs; import stopped without dropping records')
        return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx', type=Path)
    parser.add_argument('--output', type=Path, default=Path('web/companies/data.json'))
    args = parser.parse_args()
    records = read_export(args.xlsx)
    data = dict(version=1, imported_at=datetime.now(timezone.utc).isoformat(),
                source=args.xlsx.name, fields=FIELDS, headers=HEADERS, records=records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(args.output)
    print(f'Imported {len(records)} records → {args.output}')


if __name__ == '__main__':
    main()
