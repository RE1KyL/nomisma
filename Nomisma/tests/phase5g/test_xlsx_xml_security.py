import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "nomisma_tools"))

from dataset_io import _read_table
from dataset_store import DatasetError
from explore_store import batches
from spreadsheet_inspector import SheetSource
from workbook_io import handle


class WorkbookXMLSecurity(unittest.TestCase):
    def test_rejects_encoded_doctype_through_every_untrusted_workbook_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = io.BytesIO()
            book = Workbook()
            book.active.append(["value"])
            book.active.append([12])
            book.save(source)
            book.close()
            source.seek(0)

            file = root / "test.xlsx"
            project = SimpleNamespace(path=lambda relative: root / relative)
            routes = {
                "editor": lambda: handle({"root": str(root), "path": file.name, "action": "open"}),
                "dataset": lambda: _read_table(file),
                "explore": lambda: list(batches(file, {})),
                "inspector": lambda: SheetSource(project, file.name),
            }
            with zipfile.ZipFile(source) as clean:
                for path, encoding in (("xl/worksheets/sheet1.xml", "utf-16"),
                                       ("xl/_rels/workbook.xml.rels", "utf-8"),
                                       ("xl/worksheets/custom.payload", "utf-16")):
                    with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as attacked:
                        for item in clean.infolist():
                            if item.filename != path:
                                content = clean.read(item)
                                if path.endswith(".payload") and item.filename == "xl/_rels/workbook.xml.rels":
                                    self.assertIn(b"sheet1.xml", content)
                                    content = content.replace(b"sheet1.xml", b"custom.payload")
                                attacked.writestr(item.filename, content)
                        attacked.writestr(path, (
                            '<!DOCTYPE x [<!ENTITY test SYSTEM "http://127.0.0.1:9/test">]><x>&test;</x>'
                        ).encode(encoding))
                    original = file.read_bytes()
                    for name, run in routes.items():
                        with self.subTest(part=path, entry=name):
                            with self.assertRaisesRegex((DatasetError, ValueError), "cannot contain a DTD"):
                                run()
                    self.assertEqual(file.read_bytes(), original)
