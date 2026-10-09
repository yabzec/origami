from app.models import DocType, Document, Folder
from app.services.storage import Storage
from app.services.tree_paths import (
    document_rel_path,
    folder_rel_dir,
    safe_name,
    unique_name,
)


def test_safe_name_replaces_unsafe_characters():
    assert safe_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert safe_name("tab\there") == "tab_here"


def test_safe_name_trims_dots_and_spaces():
    assert safe_name("  .hidden. ") == "hidden"
    assert safe_name("../..") == "_"
    assert safe_name("...") == "Untitled"
    assert safe_name("") == "Untitled"


def test_safe_name_caps_bytes_without_breaking_characters():
    name = safe_name("è" * 150)  # 300 bytes in UTF-8
    assert len(name.encode()) <= 200
    assert name == "è" * 100


def test_unique_name(tmp_path):
    (tmp_path / "Invoice.pdf").write_bytes(b"")
    assert unique_name(tmp_path, "Invoice", ".pdf", set()) == "Invoice (2).pdf"
    assert unique_name(tmp_path, "Invoice", ".pdf", {"Invoice (2).pdf"}) == "Invoice (3).pdf"
    assert unique_name(tmp_path, "Invoice", ".pdf", set(), own="Invoice.pdf") == "Invoice.pdf"
    assert unique_name(tmp_path / "missing", "Other", ".pdf", set()) == "Other.pdf"


def make_folder(session, name, parent_id=None):
    folder = Folder(name=name, parent_id=parent_id)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


def make_doc(session, title, folder_id=None, file_path=None):
    doc = Document(title=title, doc_type=DocType.pdf, folder_id=folder_id, file_path=file_path)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_folder_rel_dir(session):
    home = make_folder(session, "Home")
    bills = make_folder(session, "Bills: 2025", home.id)
    assert folder_rel_dir(session, None) == ""
    assert folder_rel_dir(session, home.id) == "Home"
    assert folder_rel_dir(session, bills.id) == "Home/Bills_ 2025"


def test_document_rel_path_root_and_folder(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    home = make_folder(session, "Home")
    assert document_rel_path(session, storage, make_doc(session, "Note"), ".txt") == "Note.txt"
    doc = make_doc(session, "Invoice", home.id)
    assert document_rel_path(session, storage, doc, ".pdf") == "Home/Invoice.pdf"


def test_document_rel_path_avoids_other_documents(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    make_doc(session, "Invoice", file_path="Invoice.pdf")  # in the database, not on disk
    doc = make_doc(session, "Invoice")
    assert document_rel_path(session, storage, doc, ".pdf") == "Invoice (2).pdf"


def test_document_rel_path_keeps_own_name(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    storage.write_file("Invoice.pdf", b"1")
    doc = make_doc(session, "Invoice", file_path="Invoice.pdf")
    assert document_rel_path(session, storage, doc, ".pdf") == "Invoice.pdf"


def test_document_rel_path_unsafe_title_stays_inside_folder(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    home = make_folder(session, "Home")
    doc = make_doc(session, "...", home.id)
    assert document_rel_path(session, storage, doc, ".pdf") == "Home/Untitled.pdf"
    doc2 = make_doc(session, "../../etc/passwd", home.id)
    assert document_rel_path(session, storage, doc2, ".pdf") == "Home/_.._etc_passwd.pdf"
